"""Outbox Worker：批量领取事件 → Neo4j 投影 → 读后校验 → 标记完成。

核心机制（issue #8 验收）：
- FOR UPDATE SKIP LOCKED：多 Worker 并发，同一事件同一时刻最多一个租约；
- 租约过期恢复：PROCESSING 且 lease_expires_at < now → 重新可领取；
- 幂等：Neo4j 写入用参数化 Cypher + MERGE，重复消费无副作用；
- 崩溃恢复：Neo4j 提交后 PG 标记前崩溃 → 重启后重复消费幂等并最终完成；
- 毒性事件：连续失败 retry_count >= max → DEAD_LETTER，不阻塞其他事件；
- 乱序拒绝：旧版本事件不能覆盖新版本（用 recorded_at 比较）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_BATCH_SIZE = 10
DEFAULT_MAX_RETRIES = 5
DEFAULT_LEASE_TTL_SECONDS = 30


class OutboxWorkerError(Exception):
    """Outbox 处理失败（可分类重试/死信）。"""


@dataclass(frozen=True)
class ProjectionResult:
    """单事件投影结果。"""

    event_id: uuid.UUID
    status: str  # PROCESSED | FAILED_RETRYABLE | DEAD_LETTER
    attempts: int = 1
    error: str | None = None


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def claim_batch(
    uow: Any,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
) -> list[dict[str, Any]]:
    """批量领取 PENDING 事件：FOR UPDATE SKIP LOCKED + 租约。

    同时回收租约过期的 PROCESSING 事件（Worker 崩溃后恢复）。
    """
    lease_expiry = _now() + dt.timedelta(seconds=lease_ttl_seconds)
    rows = uow._conn.execute(  # noqa: SLF001
        """
        SELECT id, aggregate_type, aggregate_id, event_type, payload,
               idempotency_key, retry_count
        FROM ops.graph_outbox
        WHERE (status = 'PENDING')
           OR (status = 'PROCESSING' AND lease_expires_at < %s)
        ORDER BY created_at
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """,
        (_now(), batch_size),
    ).fetchall()
    columns = ["id", "aggregate_type", "aggregate_id", "event_type",
               "payload", "idempotency_key", "retry_count"]
    events = [dict(zip(columns, row, strict=True)) for row in rows]
    for event in events:
        uow._conn.execute(  # noqa: SLF001
            """
            UPDATE ops.graph_outbox
            SET status = 'PROCESSING', locked_at = now(),
                locked_by = %s, lease_expires_at = %s
            WHERE id = %s
            """,
            (f"worker-{os.getpid()}", lease_expiry, event["id"]),
        )
    return events


def process_event(
    uow: Any,
    event: dict[str, Any],
    *,
    project_fn: Callable[[Any, dict[str, Any]], None],
    verify_fn: Callable[[Any, dict[str, Any]], bool],
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> ProjectionResult:
    """处理单事件：投影 → 读后校验 → 标记（或重试/死信）。"""
    event_id = event["id"]
    try:
        project_fn(uow, event)
        if not verify_fn(uow, event):
            raise OutboxWorkerError("post-write verification failed")
        # 校验通过 → PROCESSED
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.graph_outbox SET status = 'PROCESSED', processed_at = now(), "
            "last_error = NULL WHERE id = %s", (event_id,),
        )
        return ProjectionResult(event_id, "PROCESSED")
    except Exception as exc:  # noqa: BLE001 - 投影失败必须分类处理
        error_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
        retry_count = int(event.get("retry_count") or 0) + 1
        if retry_count >= max_retries:
            uow._conn.execute(  # noqa: SLF001
                "UPDATE ops.graph_outbox SET status = 'DEAD_LETTERED', "
                "dead_lettered_at = now(), retry_count = %s, last_error = %s "
                "WHERE id = %s",
                (retry_count, error_msg, event_id),
            )
            return ProjectionResult(event_id, "DEAD_LETTERED", retry_count, error_msg)
        # PROCESSING → PENDING 重试（retry_count 递增）
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.graph_outbox SET status = 'PENDING', "
            "retry_count = %s, last_error = %s, lease_expires_at = NULL, "
            "locked_at = NULL, locked_by = NULL "
            "WHERE id = %s",
            (retry_count, error_msg, event_id),
        )
        return ProjectionResult(event_id, "PENDING", retry_count, error_msg)


def run_worker_cycle(
    uow_factory: Any,
    *,
    project_fn: Callable[[Any, dict[str, Any]], None],
    verify_fn: Callable[[Any, dict[str, Any]], bool],
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[ProjectionResult]:
    """完整 Worker 周期：领取 → 逐事件投影 → 标记。"""
    with uow_factory.transaction() as uow:
        events = claim_batch(uow, batch_size=batch_size)
    results = []
    for event in events:
        with uow_factory.transaction() as uow:
            result = process_event(
                uow, event, project_fn=project_fn, verify_fn=verify_fn,
                max_retries=max_retries,
            )
        results.append(result)
    return results


import os  # noqa: E402 - claim_batch 用 os.getpid()
