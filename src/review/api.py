"""审核写 API（issue #33 / ADR-0006）：最小写面——唯一写端点。

POST /api/v1/review/tasks/{task_id}/decision
- 认证：X-Reviewer-Key（SHA-256 哈希比对）+ 服务端开关（默认 503）；
- 幂等：Idempotency-Key 头（客户端 UUID）——决定提交后以
  ops.audit_event（event_type=REVIEW_DECISION_IDEMPOTENCY +
  payload.idempotency_key）锚点查重，同 key 重放返回任务终态；
- 并发：#7 review_decide 的乐观并发（ReviewConflictError → 409 +
  当前状态回传供刷新差异）；
- 事务：决定经 review_decide → accept_claim/状态机 原子提交
  （Claim/Evidence/Provenance/审计/Outbox 同事务——既有资产）。
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.claims.review import ReviewConflictError, review_decide
from src.db.repositories import ConcurrentClaimUpdateError
from src.db.uow import UnitOfWorkFactory
from src.domain.claim_service import ClaimAcceptanceError
from src.query.api import _connect_read_only
from src.review.auth import authenticate_reviewer, review_write_available

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/review", tags=["review"])


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(ACCEPTED|REJECTED)$")
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] | None = None


def _reviewer(request: Request) -> str:
    return authenticate_reviewer(request)


@router.get("/queue")
async def review_queue(
    request: Request,
    reviewer: str = Depends(_reviewer),
    status: str = Query(default="OPEN", max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """待审队列（Reviewer 只读）：任务 + Claim 摘要 + 证据计数。"""
    settings = request.app.state.settings
    # status 白名单校验（非法枚举 → 422，而非 DB 转换错误 500）
    allowed_statuses = {"OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
    if status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(allowed_statuses)}",
        )
    with _connect_read_only(settings.postgres_dsn) as conn:
        rows = conn.execute(
            """
            SELECT t.id AS task_id, t.status AS task_status, t.priority,
                   t.reason_codes,
                   c.id AS claim_id, c.predicate_code, c.claim_status,
                   c.business_stage, c.evidence_state, c.confidence,
                   c.subject_entity_id, c.object_value,
                   (SELECT count(*) FROM fact.claim_evidence ce
                    WHERE ce.claim_id = c.id) AS evidence_count
            FROM fact.review_task t
            JOIN fact.claim c ON c.id = t.claim_id
            WHERE t.status = %s::review_task_status
            ORDER BY t.created_at
            LIMIT %s
            """,
            (status, limit),
        ).fetchall()
        total = conn.execute(
            "SELECT count(*) FROM fact.review_task WHERE status = %s::review_task_status",
            (status,),
        ).fetchone()[0]
    cols = ["task_id", "task_status", "priority", "reason_codes", "claim_id",
            "predicate_code", "claim_status", "business_stage", "evidence_state",
            "confidence", "subject_entity_id", "object_value", "evidence_count"]
    tasks = [dict(zip(cols, row, strict=True)) for row in rows]
    return {"tasks": tasks, "total": total, "status_filter": status,
            "write_available": review_write_available(settings)}


@router.post("/tasks/{task_id}/decision")
async def submit_review_decision(
    task_id: str,
    body: ReviewDecisionRequest,
    request: Request,
    reviewer: str = Depends(_reviewer),
) -> dict[str, Any]:
    """提交审核决定（唯一写端点）。

    GWT 并发/幂等矩阵：
    - 乐观并发失败 → 409 + 当前状态（第二位 Reviewer 刷新差异）；
    - 同 Idempotency-Key 重放 → 首次结果原样返回（不重复决定/审计）；
    - 状态机拒绝（如任务已 COMPLETED）→ 409 带前后状态；
    - 任何失败：事实库零副作用（事务回滚）。
    """
    settings = request.app.state.settings
    try:
        uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="task id must be a UUID") from exc

    idempotency_key = request.headers.get("idempotency-key")
    trace_id = request.headers.get("x-trace-id")
    uow_factory = UnitOfWorkFactory(settings.postgres_dsn)

    # 幂等查重：同 Idempotency-Key 的既有决定原样返回
    if idempotency_key:
        with _connect_read_only(settings.postgres_dsn) as conn:
            existing = conn.execute(
                """
                SELECT id FROM ops.audit_event
                WHERE event_type = 'REVIEW_DECISION_IDEMPOTENCY'
                  AND entity_id = %s
                  AND payload->>'idempotency_key' = %s
                """,
                (uuid.UUID(task_id), idempotency_key),
            ).fetchone()
        if existing is not None:
            # 返回任务的当前终态（幂等重放——同一决定不再执行）
            with _connect_read_only(settings.postgres_dsn) as conn:
                task = conn.execute(
                    "SELECT id, status, decision, decision_reason "
                    "FROM fact.review_task WHERE id = %s",
                    (task_id,),
                ).fetchone()
            if task is not None:
                return {
                    "idempotent_replay": True,
                    "task_id": task_id,
                    "task_status": task[1],
                    "decision": task[2],
                    "reason": task[3],
                }

    evidence_ids = (
        [uuid.UUID(e) for e in body.evidence_ids] if body.evidence_ids else None
    )
    try:
        with uow_factory.transaction() as uow:
            result = review_decide(
                uow,
                review_task_id=uuid.UUID(task_id),
                decision=body.decision,
                reviewed_by=reviewer,
                decision_reason=body.reason,
                evidence_ids=evidence_ids,
                trace_id=trace_id or idempotency_key,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReviewConflictError, ConcurrentClaimUpdateError) as exc:
        # 并发冲突（乐观守卫/悲观锁 NOWAIT——另一审核者正在处理或已决定）：
        # 回传当前状态供 UI 刷新差异（绝不让后到者覆盖）
        with _connect_read_only(settings.postgres_dsn) as conn:
            current = conn.execute(
                "SELECT status, decision FROM fact.review_task WHERE id = %s",
                (task_id,),
            ).fetchone()
            claim_state = conn.execute(
                "SELECT claim_status FROM fact.claim "
                "WHERE id = (SELECT claim_id FROM fact.review_task WHERE id = %s)",
                (task_id,),
            ).fetchone()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "review task was modified by another reviewer",
                "reason": str(exc),
                "current_task_status": current[0] if current else None,
                "current_decision": current[1] if current else None,
                "current_claim_status": claim_state[0] if claim_state else None,
            },
        ) from exc
    except ClaimAcceptanceError as exc:
        # 状态机/证据校验拒绝：可修复原因明确回传
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except psycopg.errors.Error as exc:
        log.error("review_decision_db_error", error=type(exc).__name__,
                  trace_id=trace_id)
        raise HTTPException(
            status_code=500, detail="review decision failed"
        ) from exc

    # 审计幂等锚点（Idempotency-Key 存在时记录，供重放查重）
    if idempotency_key:
        try:
            with uow_factory.transaction() as uow:
                from psycopg.types.json import Json

                uow._conn.execute(  # noqa: SLF001
                    """
                    INSERT INTO ops.audit_event
                        (event_type, entity_type, entity_id, payload, trace_id)
                    VALUES ('REVIEW_DECISION_IDEMPOTENCY', 'ReviewTask',
                            %s, %s, %s)
                    """,
                    (uuid.UUID(task_id),
                     Json({"idempotency_key": idempotency_key,
                           "decision": body.decision, "reviewer": reviewer}),
                     trace_id),
                )
        except Exception:  # noqa: BLE001 - 审计锚点失败不推翻已提交的决定
            log.warning("review_idempotency_audit_failed", task_id=task_id)

    return {
        "idempotent_replay": False,
        "task_id": result.get("review_task_id", task_id),
        "claim_id": result.get("claim_id"),
        "before_status": result.get("before_status"),
        "after_status": result.get("after_status"),
        "decision": body.decision,
        "reason": body.reason,
    }
