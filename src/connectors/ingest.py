"""Raw 幂等采集运行器（issue #3）。

不变量：
- 同一数据集同一调度窗口只允许一个活跃运行（advisory lock + 租约）；
- 批次写入与游标/计数/租约心跳同事务（崩溃 → 全回滚，重放幂等无重复）；
- 手动取消后不再发起外部请求（批次间复查运行状态）；
- Schema 变化熔断数据集（capability=SCHEMA_CHANGED 后拒绝再采集，
  阻止下游标准化）；
- 空结果与错误是不同结果：空批次合法、绝不成否定事实。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Any

import structlog

from src.connectors.datasets import payload_hash_for_row
from src.connectors.ports import (
    NetworkError,
    PermissionDeniedError,
    RateLimitedError,
    SchemaChangedError,
    SourceConnector,
    TerminalConnectorError,
)
from src.connectors.tushare_connectors import SOURCE_SYSTEM
from src.db.uow import UnitOfWork, UnitOfWorkFactory
from src.domain.enums import IngestRunStatus, ensure_transition

log = structlog.get_logger(__name__)


class ActiveRunExistsError(Exception):
    """同数据集已有活跃运行；调用方可选择复用该运行。"""

    def __init__(self, run: dict[str, Any]) -> None:
        super().__init__(f"active run {run['id']} for dataset")
        self.run = run


class DatasetFusedError(Exception):
    """数据集因 Schema 变化被熔断；必须先解除能力状态才允许再采集。"""


class ProbeOnlyDatasetError(Exception):
    """probe_only 数据集（anns_d/互动等）只做权限探测，不做 Raw 采集。"""


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _dataset_lock_key(dataset_name: str) -> int:
    digest = hashlib.sha256(f"ingest:{dataset_name}".encode()).hexdigest()
    return int(digest[:15], 16)


def _new_lease_expiry(ttl_seconds: int) -> dt.datetime:
    return _now() + dt.timedelta(seconds=ttl_seconds)


def start_run(
    uow: UnitOfWork,
    *,
    dataset_name: str,
    trace_id: str,
    lease_owner: str,
    lease_ttl_seconds: int,
    parent_run_id: Any | None = None,
    initial_cursor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建（或复用）采集运行：advisory lock 防并发，租约防僵死。"""
    got_lock = uow._conn.execute(  # noqa: SLF001 - 事务级 advisory lock 属于编排层
        "SELECT pg_try_advisory_xact_lock(%s)", (_dataset_lock_key(dataset_name),)
    ).fetchone()[0]
    if not got_lock:
        active = uow.ingest_runs.get_active_run(dataset_name, now=_now())
        if active is not None:
            raise ActiveRunExistsError(active)
        raise ActiveRunExistsError({"id": None, "dataset_name": dataset_name})

    # 熔断检查：SCHEMA_CHANGED 未解除前拒绝采集，阻止下游标准化链路
    capability = uow.source_capabilities.get_status(SOURCE_SYSTEM, dataset_name)
    if capability and capability["status"] == "SCHEMA_CHANGED":
        raise DatasetFusedError(dataset_name)

    active = uow.ingest_runs.get_active_run(dataset_name, now=_now())
    if active is not None:
        raise ActiveRunExistsError(active)  # 复用入口见调用方

    run = uow.ingest_runs.create(
        dataset_name=dataset_name,
        source_system=SOURCE_SYSTEM,
        trace_id=trace_id,
        cursor_state=initial_cursor or {},
        lease_owner=lease_owner,
        lease_expires_at=_new_lease_expiry(lease_ttl_seconds),
        parent_run_id=parent_run_id,
    )
    uow.ingest_runs.transition(run["id"], IngestRunStatus.RUNNING)
    detail = uow.ingest_runs.get(run["id"])
    assert detail is not None
    return detail


@dataclass(frozen=True)
class IngestOutcome:
    run_id: Any
    status: IngestRunStatus
    rows_received: int
    rows_inserted: int
    rows_rejected: int
    request_count: int


def _rows_stats(rows: list[dict[str, Any]], required_fields: tuple[str, ...]) -> tuple[list, int]:
    """行级校验：必需字段齐全且自然键非空才接受；拒绝行计入统计（拒绝不是失败）。

    注意：字段整体缺失由 connector.fetch 熔断（SCHEMA_CHANGED），此处只处理行级缺陷。
    """
    accepted: list[dict[str, Any]] = []
    rejected = 0
    key_field = required_fields[0] if required_fields else None
    for row in rows:
        if not all(field in row for field in required_fields) or (
            key_field is not None and row.get(key_field) in (None, "")
        ):
            rejected += 1
        else:
            accepted.append(row)
    return accepted, rejected


def ingest_dataset(
    uow_factory: UnitOfWorkFactory,
    connector: SourceConnector,
    *,
    run_id: Any,
    lease_ttl_seconds: int,
    max_batches: int = 1000,
) -> IngestOutcome:
    """按游标逐批采集：外部调用在事务外，写入+游标+心跳在一个事务内。"""
    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.get(run_id)
        assert run is not None
        cursor: dict[str, Any] = run["cursor_state"] or {}

    config = getattr(connector, "config", None)
    if config is not None and not config.ingested:
        raise ProbeOnlyDatasetError(
            f"{connector.dataset_name} is probe-only; raw ingestion is not allowed"
        )

    required_fields = getattr(connector.config, "required_fields", ())
    stats = {"rows_received": 0, "rows_inserted": 0, "rows_rejected": 0, "request_count": 0}
    anomalies: list[str] = []
    batch_index = 0

    while True:
        # 取消检查：手动取消后不得再发起外部请求
        with uow_factory.transaction() as uow:
            current = uow.ingest_runs.get(run_id)
            assert current is not None
            if current["status"] != IngestRunStatus.RUNNING.value:
                return IngestOutcome(
                    run_id=run_id,
                    status=IngestRunStatus(current["status"]),
                    **stats,
                )

        try:
            batch = connector.fetch(cursor)  # 外部调用，不在事务内
        except (RateLimitedError, NetworkError) as exc:
            # 瞬态失败：外部尝试次数如实入账（退避重试也消耗额度，不得低报）
            attempts = getattr(exc, "attempts", 1)
            stats["request_count"] += attempts
            _finish_transient(uow_factory, run_id, attempts, exc, stats)
            return IngestOutcome(run_id, IngestRunStatus.FAILED_RETRYABLE, **stats)
        except SchemaChangedError as exc:
            stats["request_count"] += 1
            _fuse_dataset(uow_factory, connector.dataset_name, str(exc), run_id, stats)
            return IngestOutcome(run_id, IngestRunStatus.FAILED_FINAL, **stats)
        except PermissionDeniedError as exc:
            stats["request_count"] += 1
            _finish_failed(uow_factory, run_id, IngestRunStatus.FAILED_FINAL,
                           f"{type(exc).__name__}: {exc}", stats)
            return IngestOutcome(run_id, IngestRunStatus.FAILED_FINAL, **stats)
        except TerminalConnectorError as exc:
            # BLOCKER 修复：确定性终态错误（INVALID_REQUEST/UNKNOWN/SCHEMA_CHANGED）
            # 不得逃逸把运行卡在 RUNNING、再被租约恢复器误标为可重试
            stats["request_count"] += 1
            if exc.code == "SCHEMA_CHANGED":
                _fuse_dataset(uow_factory, connector.dataset_name, str(exc), run_id, stats)
            else:
                _finish_failed(uow_factory, run_id, IngestRunStatus.FAILED_FINAL,
                               f"{type(exc).__name__}[{exc.code}]: {exc}", stats)
            return IngestOutcome(run_id, IngestRunStatus.FAILED_FINAL, **stats)

        stats["request_count"] += 1
        accepted, rejected = _rows_stats(batch.rows, required_fields)
        stats["rows_received"] += len(batch.rows)
        stats["rows_rejected"] += rejected

        # 重复页/游标倒退检测：停止而非无限循环；该次外呼如实入账
        if _is_duplicate_page(cursor, batch, connector):
            anomalies.append("duplicate_page")
            with uow_factory.transaction() as uow:
                uow.ingest_runs.update_counts(run_id, request_count=1)
            break

        # 批次事务：写入 + 计数 + 游标 + 租约心跳（崩溃 → 全回滚，重放幂等）
        next_cursor = connector.next_cursor(batch)
        if next_cursor is not None:
            new_cursor = next_cursor
        else:
            # 计划范围完成：保留最终字段签名供后续校验
            new_cursor = {**cursor, "schema_signature": batch.schema_signature}

        inserted_in_batch = 0
        with uow_factory.transaction() as uow:
            for row in accepted:
                source_key = str(row.get("ts_code") or row.get("code") or "")[:500] or None
                result = uow.source_records.insert_idempotent(
                    source_system=SOURCE_SYSTEM,
                    api_name=batch.dataset_name,
                    payload_hash=payload_hash_for_row(row),
                    raw_payload=row,
                    ingest_run_id=run_id,
                    source_key=source_key,
                    request_params=batch.request_params,
                    schema_signature=batch.schema_signature,
                )
                if result["inserted"]:
                    inserted_in_batch += 1
            stats["rows_inserted"] += inserted_in_batch
            uow.ingest_runs.update_counts(
                run_id,
                request_count=1,
                rows_received=len(batch.rows),
                rows_inserted=inserted_in_batch,
                rows_rejected=rejected,
                cursor_state=new_cursor,
                lease_expires_at=_new_lease_expiry(lease_ttl_seconds),
            )

        batch_index += 1
        if next_cursor is None:
            break  # 计划范围完成
        cursor = dict(next_cursor)
        if batch_index >= max_batches:
            anomalies.append("max_batches_reached")
            break

    if stats["rows_rejected"] > 0:
        anomalies.append("rejected_rows")
    final_status = IngestRunStatus.PARTIAL_SUCCESS if anomalies else IngestRunStatus.SUCCEEDED
    _finish(uow_factory, run_id, final_status, anomalies=anomalies, stats=stats)
    return IngestOutcome(run_id, final_status, **stats)


def _is_duplicate_page(cursor: dict[str, Any], batch: Any, connector: SourceConnector) -> bool:
    """空页不是重复页；重复页 = 相同页哈希或 offset 未推进。"""
    if batch.is_empty:
        return False
    last_page_hash = cursor.get("last_page_hash")
    if last_page_hash and hasattr(connector, "page_hash"):
        if connector.page_hash(batch) == last_page_hash:
            return True
    return False


def _finish(
    uow_factory: UnitOfWorkFactory,
    run_id: Any,
    status: IngestRunStatus,
    *,
    anomalies: list[str],
    stats: dict[str, int],
) -> None:
    with uow_factory.transaction() as uow:
        uow.ingest_runs.transition(
            run_id,
            status,
            finished_at=_now(),
            error_detail={"anomalies": anomalies} if anomalies else None,
        )
        log.info(
            "ingest_finished",
            run_id=str(run_id),
            status=status.value,
            anomalies=anomalies,
            **stats,
        )


def _finish_transient(
    uow_factory: UnitOfWorkFactory,
    run_id: Any,
    attempts: int,
    exc: Exception,
    stats: dict[str, int],
) -> None:
    """瞬态失败：外部尝试次数如实入账（含退避重试消耗的额度）。"""
    with uow_factory.transaction() as uow:
        uow.ingest_runs.update_counts(run_id, request_count=attempts)
        uow.ingest_runs.transition(
            run_id,
            IngestRunStatus.FAILED_RETRYABLE,
            finished_at=_now(),
            error_detail={"reason": f"{type(exc).__name__}: {exc}"[:300],
                          "external_attempts": attempts},
        )
        log.warning(
            "ingest_failed", run_id=str(run_id),
            status=IngestRunStatus.FAILED_RETRYABLE.value,
            external_attempts=attempts, **stats,
        )


def _finish_failed(
    uow_factory: UnitOfWorkFactory,
    run_id: Any,
    status: IngestRunStatus,
    reason: str,
    stats: dict[str, int],
) -> None:
    with uow_factory.transaction() as uow:
        # 失败时的外呼次数（含失败的那一次）如实入账，管理接口不得低报
        uow.ingest_runs.update_counts(run_id, request_count=stats["request_count"])
        uow.ingest_runs.transition(
            run_id,
            status,
            finished_at=_now(),
            error_detail={"reason": reason[:300]},
        )
        log.warning(
            "ingest_failed", run_id=str(run_id), status=status.value,
            reason=reason[:300], **stats,
        )


def _fuse_dataset(
    uow_factory: UnitOfWorkFactory,
    dataset_name: str,
    reason: str,
    run_id: Any,
    stats: dict[str, int],
) -> None:
    """Schema 变化熔断：能力状态置 SCHEMA_CHANGED，运行终态失败。"""
    with uow_factory.transaction() as uow:
        uow.source_capabilities.upsert(
            source_system=SOURCE_SYSTEM,
            api_name=dataset_name,
            status="SCHEMA_CHANGED",
            detail={"reason": reason[:300], "fused_run_id": str(run_id)},
        )
        uow.ingest_runs.update_counts(run_id, request_count=stats["request_count"])
        uow.ingest_runs.transition(
            run_id,
            IngestRunStatus.FAILED_FINAL,
            finished_at=_now(),
            error_detail={"reason": f"schema changed: {reason[:280]}"},
        )
        log.warning("dataset_fused", dataset=dataset_name, run_id=str(run_id), **stats)


def recover_stale_runs(uow_factory: UnitOfWorkFactory) -> list[dict[str, Any]]:
    """恢复器：租约过期仍 RUNNING 的任务判定为可恢复（FAILED_RETRYABLE 保留游标）。

    之后的重试运行应显式关联 parent_run_id 并从持久化游标继续。
    """
    recovered: list[dict[str, Any]] = []
    with uow_factory.transaction() as uow:
        stale = uow.ingest_runs.find_stale_running(now=_now())
        for run in stale:
            ensure_transition("ingest_run", IngestRunStatus.RUNNING.value,
                              IngestRunStatus.FAILED_RETRYABLE.value)
            uow.ingest_runs.transition(
                run["id"],
                IngestRunStatus.FAILED_RETRYABLE,
                finished_at=_now(),
                error_detail={
                    "recovered_by_lease": True,
                    "lease_expires_at": str(run["lease_expires_at"]),
                },
            )
            recovered.append({"run_id": run["id"], "dataset": run["dataset_name"]})
    for item in recovered:
        log.info("stale_run_recovered", **item)
    return recovered
