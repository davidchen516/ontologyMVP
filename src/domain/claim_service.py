"""接受 Claim 的单库原子事务（issue #2 核心不变量）。

同一事务内完成，任一步失败全部回滚：
1. Claim 状态迁移（合法迁移校验 + 乐观并发守卫）
2. ClaimEvidence 关联（Evidence 必须已存在，经营 Claim 必须有证据）
3. Provenance 写入
4. 审计事件
5. Graph Outbox 事件（与 ACCEPTED 同事务产生，ADR-0001/0002）

并发语义：两个审核者并发处理同一 Claim，只允许一个基于当前版本成功；
后到者得到 ConcurrentClaimUpdateError，不产生双重决定。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from src.db.repositories import ConcurrentClaimUpdateError
from src.db.uow import UnitOfWork
from src.domain.enums import CLAIM_TRANSITIONS, ClaimStatus, ensure_transition


class ClaimAcceptanceError(Exception):
    """接受条件不满足：调用方必须视为未接受，不得重试相同参数。"""


@dataclass(frozen=True)
class ClaimAcceptanceResult:
    claim_id: uuid.UUID
    status: ClaimStatus
    outbox_event_id: uuid.UUID
    outbox_inserted: bool


def accept_claim(
    uow: UnitOfWork,
    *,
    claim_id: uuid.UUID,
    reviewed_by: str,
    evidence_ids: list[uuid.UUID],
    provenance: dict[str, Any],
    trace_id: str | None = None,
    review_task_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> ClaimAcceptanceResult:
    """在调用方事务内执行接受动作；由 UnitOfWork.transaction() 提交/回滚。"""
    reviewed_at = now or dt.datetime.now(tz=dt.UTC)

    # 前置校验：经营 Claim 接受必须关联 Evidence（ADR-0002 #4）
    if not evidence_ids:
        raise ClaimAcceptanceError("operating claim acceptance requires at least one evidence")

    # 悲观锁 NOWAIT：并发审核同一 Claim，后到者立即冲突
    claim = uow.claims.get_for_update_nowait(claim_id)
    if claim is None:
        raise LookupError(f"claim {claim_id} not found")
    current_status = ClaimStatus(claim["claim_status"])

    # 状态机校验：非法迁移不得产生任何写入
    if ClaimStatus.ACCEPTED not in CLAIM_TRANSITIONS[current_status]:
        raise ClaimAcceptanceError(
            f"claim {claim_id} in status {current_status.value} cannot be accepted"
        )

    # Evidence 必须真实存在；缺失则不得进入 ACCEPTED
    for evidence_id in evidence_ids:
        if not uow.claim_evidence.exists(evidence_id):
            raise ClaimAcceptanceError(f"evidence fragment {evidence_id} does not exist")

    # 1) 状态迁移（守卫当前版本）
    uow.claims.update_status_guarded(
        claim_id,
        expected_status=current_status,
        target=ClaimStatus.ACCEPTED,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )

    # 2) ClaimEvidence 关联
    for evidence_id in evidence_ids:
        uow.claim_evidence.link(claim_id=claim_id, evidence_id=evidence_id)

    # 3) Provenance
    provenance_payload = {
        "entity_id": str(claim_id),
        "entity_type": "Claim",
        **provenance,
    }
    uow.provenance.insert(**provenance_payload)

    # 4) 审计事件（同事务）
    uow.audit_events.insert(
        entity_type="Claim",
        entity_id=claim_id,
        event_type="CLAIM_ACCEPTED",
        payload={"reviewed_by": reviewed_by, "evidence_ids": [str(e) for e in evidence_ids]},
        trace_id=trace_id,
    )

    # 5) Outbox 事件：与 ACCEPTED 同事务产生；idempotency_key 幂等
    outbox = uow.graph_outbox.insert_idempotent(
        aggregate_type="Claim",
        aggregate_id=claim_id,
        event_type="CLAIM_ACCEPTED",
        payload={"claim_id": str(claim_id)},
        idempotency_key=f"claim-accepted:{claim_id}",
    )

    # 审核任务同事务闭环
    if review_task_id is not None:
        uow.review_tasks.transition(
            review_task_id,
            target_status_transition(review_task_id, uow),
            decision="ACCEPT",
            decision_reason=f"accepted by {reviewed_by}",
            completed_at=reviewed_at,
        )

    return ClaimAcceptanceResult(
        claim_id=claim_id,
        status=ClaimStatus.ACCEPTED,
        outbox_event_id=outbox["id"],
        outbox_inserted=outbox["inserted"],
    )


def target_status_transition(task_id: uuid.UUID, uow: UnitOfWork) -> Any:
    """ReviewTask 走 OPEN/IN_PROGRESS → COMPLETED 的合法迁移。"""
    task = uow.review_tasks.get(task_id)
    if task is None:
        raise LookupError(f"review_task {task_id} not found")
    from src.domain.enums import REVIEW_TASK_TRANSITIONS, ReviewTaskStatus

    current = ReviewTaskStatus(task["status"])
    if ReviewTaskStatus.COMPLETED not in REVIEW_TASK_TRANSITIONS[current]:
        raise ClaimAcceptanceError(
            f"review_task {task_id} in status {current.value} cannot be completed"
        )
    return ReviewTaskStatus.COMPLETED


__all__ = [
    "ClaimAcceptanceError",
    "ClaimAcceptanceResult",
    "ConcurrentClaimUpdateError",
    "accept_claim",
    "ensure_transition",
]
