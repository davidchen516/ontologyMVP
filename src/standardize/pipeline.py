"""标准化管道运行器（issue #4）。

不变量：
- 同数据集同窗口单活跃运行（advisory lock + 租约）；
- 每个"组"（默认单记录；快照数据集按快照日期）在一个事务内提交——
  概念快照部分失败不会暴露"半个快照"；
- 水位持久化（retrieved_at, id 元组）：崩溃后从水位续跑，重放不重复（幂等 upsert）；
- 拒绝行进 ops.normalization_event（REJECTED），绝不静默纠正；
- 有拒绝行的运行只能 PARTIAL_SUCCESS；SUCCEEDED 表示计划范围完整处理。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from src.db.uow import UnitOfWork, UnitOfWorkFactory
from src.domain.enums import IngestRunStatus
from src.standardize.processors import (
    ConceptMemberProcessor,
    StandardizeContext,
    build_processors,
)
from src.standardize.repositories import (
    NormalizationEventRepository,
    NormalizationRunRepository,
)
from src.standardize.transforms import mapping_version

log = structlog.get_logger(__name__)

BATCH_GROUP_SIZE = 200


class ActiveNormalizationExistsError(Exception):
    def __init__(self, run: dict[str, Any] | None) -> None:
        super().__init__(f"active normalization run: {run}")
        self.run = run


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _dataset_lock_key(dataset_name: str) -> int:
    digest = hashlib.sha256(f"normalize:{dataset_name}".encode()).hexdigest()
    return int(digest[:15], 16)


def start_normalization_run(
    uow: UnitOfWork,
    *,
    dataset_name: str,
    trace_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
    parent_run_id: uuid.UUID | None = None,
    initial_watermark: dict[str, Any] | None = None,
    replay_from_scratch: bool = False,
) -> dict[str, Any]:
    """启动标准化运行。

    水位语义（issue #4 恢复/重放双需求）：
    - 默认自动继承该数据集最近完成运行的水位 → 只处理新增 Raw（续传）；
    - replay_from_scratch=True → 从零水位全量重放（映射版本回退/重建场景）；
    - 显式 initial_watermark 优先（恢复器续跑用）。
    """
    got_lock = uow._conn.execute(  # noqa: SLF001 - 事务级 advisory lock 属编排层
        "SELECT pg_try_advisory_xact_lock(%s)", (_dataset_lock_key(dataset_name),)
    ).fetchone()[0]
    if not got_lock:
        active = NormalizationRunRepository(uow._conn).get_active_run(  # noqa: SLF001
            dataset_name, now=_now()
        )
        raise ActiveNormalizationExistsError(active)

    runs = NormalizationRunRepository(uow._conn)  # noqa: SLF001
    events = NormalizationEventRepository(uow._conn)  # noqa: SLF001
    active = runs.get_active_run(dataset_name, now=_now())
    if active is not None:
        raise ActiveNormalizationExistsError(active)

    if initial_watermark is None and not replay_from_scratch:
        initial_watermark = runs.latest_watermark(dataset_name) or {}
    run = runs.create(
        dataset_name=dataset_name,
        source_system="TUSHARE",
        mapping_version=mapping_version(),
        trace_id=trace_id,
        watermark=initial_watermark or {},
        lease_owner=lease_owner,
        lease_expires_at=_now() + dt.timedelta(seconds=lease_ttl_seconds),
        parent_run_id=parent_run_id,
    )
    # 新映射版本重跑必须生成可审计事件
    events.add(
        run_id=run["id"],
        event_type="VERSION_APPLIED",
        entity_type="DATASET",
        entity_ref=dataset_name,
        detail={"mapping_version": mapping_version()},
    )
    runs.transition(run["id"], IngestRunStatus.RUNNING.value)
    detail = runs.get(run["id"])
    assert detail is not None
    return detail


def _group_key(processor: Any, payload: dict[str, Any]) -> str:
    """快照型数据集按快照日期分组（原子提交单位）；默认按记录分组。"""
    if isinstance(processor, ConceptMemberProcessor):
        return f"snapshot:{payload.get('in_date') or payload.get('snapshot_date')}"
    return f"record:{uuid.uuid4().hex}"


@dataclass(frozen=True)
class NormalizationOutcome:
    run_id: uuid.UUID
    status: IngestRunStatus
    rows_read: int
    rows_written: int
    rows_rejected: int


def _fetch_raw_records(
    uow: UnitOfWork,
    dataset_name: str,
    watermark: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """按 (retrieved_at, id) 有序读取水位之后的 Raw 记录。"""
    cur = uow._conn.execute(  # noqa: SLF001
        """
        SELECT id, raw_payload, retrieved_at FROM raw.source_record
        WHERE api_name = %s AND source_system = 'TUSHARE' AND is_current
          AND (retrieved_at, id) > (%s::timestamptz, %s::uuid)
        ORDER BY retrieved_at, id
        LIMIT %s
        """,
        (
            dataset_name,
            watermark.get("retrieved_at") or "1970-01-01",
            watermark.get("source_record_id") or "00000000-0000-0000-0000-000000000000",
            limit,
        ),
    )
    columns = [desc.name for desc in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def normalize_dataset(
    uow_factory: UnitOfWorkFactory,
    dataset_name: str,
    *,
    run_id: uuid.UUID,
    lease_ttl_seconds: int,
    processor: Any | None = None,
) -> NormalizationOutcome:
    processor = processor or build_processors()[dataset_name]
    stats = {"rows_read": 0, "rows_written": 0, "rows_rejected": 0}

    with uow_factory.transaction() as uow:
        run = NormalizationRunRepository(uow._conn).get(run_id)  # noqa: SLF001
        assert run is not None
        watermark: dict[str, Any] = run["watermark"] or {}
        current_status = run["status"]
    if current_status != IngestRunStatus.RUNNING.value:
        return NormalizationOutcome(run_id, IngestRunStatus(current_status), **stats)

    while True:
        # 取消/状态复查：手动取消后停止
        with uow_factory.transaction() as uow:
            run = NormalizationRunRepository(uow._conn).get(run_id)  # noqa: SLF001
            assert run is not None
            if run["status"] != IngestRunStatus.RUNNING.value:
                return NormalizationOutcome(
                    run_id, IngestRunStatus(run["status"]), **stats
                )
            records = _fetch_raw_records(uow, dataset_name, watermark, BATCH_GROUP_SIZE)
        if not records:
            break

        # 快照数据集按快照日期分组；同组在一个事务内原子提交
        groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            payload = record["raw_payload"]
            key = _group_key(processor, payload)
            groups.setdefault(key, []).append(
                {
                    "source_record_id": record["id"],
                    "payload": payload,
                    "retrieved_at": record["retrieved_at"],
                }
            )

        for _, group_records in groups.items():
            last_record = group_records[-1]
            new_watermark = {
                "retrieved_at": last_record["retrieved_at"].isoformat(),
                "source_record_id": str(last_record["source_record_id"]),
            }
            group_written = 0
            group_rejected = 0
            with uow_factory.transaction() as uow:
                runs = NormalizationRunRepository(uow._conn)  # noqa: SLF001
                events = NormalizationEventRepository(uow._conn)  # noqa: SLF001
                ctx = StandardizeContext(uow, run_id)
                for record in group_records:
                    outcome = processor.process(ctx, record)
                    stats["rows_read"] += 1
                    group_written += outcome.written
                    for rejection in outcome.rejected:
                        events.add(
                            run_id=run_id,
                            event_type="REJECTED",
                            entity_type="RAW_RECORD",
                            entity_ref=rejection["ref"],
                            detail={"reason": rejection["reason"]},
                            source_record_id=rejection["source_record_id"],
                        )
                        group_rejected += 1
                stats["rows_written"] += group_written
                stats["rows_rejected"] += group_rejected
                # 组内增量入账（update_counts 语义为累加，绝不传累计值）
                runs.update_counts(
                    run_id,
                    rows_read=len(group_records),
                    rows_written=group_written,
                    rows_rejected=group_rejected,
                    watermark=new_watermark,
                    lease_expires_at=_now() + dt.timedelta(seconds=lease_ttl_seconds),
                )
            watermark = new_watermark

    final = (
        IngestRunStatus.PARTIAL_SUCCESS
        if stats["rows_rejected"] > 0
        else IngestRunStatus.SUCCEEDED
    )
    with uow_factory.transaction() as uow:
        NormalizationRunRepository(uow._conn).transition(  # noqa: SLF001
            run_id,
            final.value,
            finished_at=_now(),
        )
    log.info(
        "normalization_finished", dataset=dataset_name, run_id=str(run_id),
        status=final.value, **stats,
    )
    return NormalizationOutcome(run_id, final, **stats)


def recover_stale_normalizations(uow_factory: UnitOfWorkFactory) -> list[dict[str, Any]]:
    """租约过期的 RUNNING 标准化运行判定为可恢复（保留水位）。"""
    recovered: list[dict[str, Any]] = []
    with uow_factory.transaction() as uow:
        runs = NormalizationRunRepository(uow._conn)  # noqa: SLF001
        for run in runs.find_stale_running(now=_now()):
            runs.transition(
                run["id"],
                IngestRunStatus.FAILED_RETRYABLE.value,
                finished_at=_now(),
                error_detail={"recovered_by_lease": True},
            )
            recovered.append({"run_id": run["id"], "dataset": run["dataset_name"]})
    for item in recovered:
        log.info("stale_normalization_recovered", **item)
    return recovered
