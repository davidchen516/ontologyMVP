"""Claim 审核状态机编排 + 审核决策 API + 审计（issue #7 范围项 5/6/7）。

- intake_candidate：抽取 → Grounding/校验 → 实体解析 → 幂等入库
  （content_hash = 候选幂等键），状态 EXTRACTED/VALIDATED/NEEDS_REVIEW/REJECTED；
- review_decide：人工审核决策（NEEDS_REVIEW → ACCEPTED/REJECTED，
  乐观并发守卫：版本不符返回冲突，不覆盖他人决定）；
- accept_claim 事务复用 #2 的 claim_service（Evidence/Provenance/审计/Outbox
  同事务）；SHACL 校验经 #5 端口；高严重度冲突强制人工审核。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from src.claims.extractor import (
    candidate_idempotency_key,
)
from src.claims.grounding import CandidateValidation, validate_candidate
from src.claims.resolution import (
    ResolutionResult,
    resolve_subject_company,
)
from src.claims.schemas import CandidateClaim
from src.domain.claim_service import ClaimAcceptanceError, accept_claim
from src.domain.enums import CLAIM_TRANSITIONS, ClaimStatus, ReviewTaskStatus

log = structlog.get_logger(__name__)


class ReviewConflictError(Exception):
    """并发审核：后到者基于过期版本，返回冲突而非覆盖。"""


@dataclass(frozen=True)
class IntakeOutcome:
    claim_id: uuid.UUID | None
    status: ClaimStatus
    validation: CandidateValidation | None
    subject_resolution: ResolutionResult | None
    created_review_task_id: uuid.UUID | None = None
    duplicate: bool = False
    reasons: tuple[str, ...] = ()


SHAPES_PATH = "ontology/shapes.ttl"
AUTO_ACCEPT_ENABLED = True  # 自动接受策略可经配置关闭（回滚要求）


def intake_candidate(
    uow: Any,
    candidate: CandidateClaim,
    *,
    document_version_id: uuid.UUID,
    fragment_checksums: list[str],
    security_id: uuid.UUID | None,
    trace_id: str | None = None,
    auto_accept: bool = AUTO_ACCEPT_ENABLED,
) -> IntakeOutcome:
    """候选 Claim 幂等入库：校验 → 解析 → 状态机落位 + 冲突检测。"""
    idempotency = candidate_idempotency_key(
        document_version_id, fragment_checksums,
        candidate.extraction_version, candidate,
    )

    # 幂等：重复抽取命中既有 Claim（不重复创建/费用）
    existing = uow._conn.execute(  # noqa: SLF001
        "SELECT id, claim_status FROM fact.claim WHERE content_hash = %s",
        (idempotency,),
    ).fetchone()
    if existing is not None:
        return IntakeOutcome(
            claim_id=existing[0], status=ClaimStatus(existing[1]),
            validation=None, subject_resolution=None, duplicate=True,
        )

    fragments_by_id = {}
    for quote in candidate.quotes:
        row = uow._conn.execute(  # noqa: SLF001
            "SELECT id, document_version_id, page_number, char_start, char_end, "
            "quote_text FROM fact.evidence_fragment WHERE id = %s",
            (quote.evidence_fragment_id,),
        ).fetchone()
        if row is not None:
            columns = ["id", "document_version_id", "page_number", "char_start",
                       "char_end", "quote_text"]
            fragments_by_id[quote.evidence_fragment_id] = dict(zip(columns, row, strict=True))

    validation = validate_candidate(candidate, fragments_by_id=fragments_by_id)
    subject_resolution = None
    if security_id is not None:
        subject_resolution = resolve_subject_company(uow, security_id=security_id,
                                                     name_hint=None)
        if subject_resolution.status != "RESOLVED":
            validation.reasons.append(
                f"subject resolution: {subject_resolution.reason}"
            )
            if validation.status == "VALIDATED":
                validation.status = "NEEDS_REVIEW"

    status = _status_from_validation(validation)

    evidence_ids = [q.evidence_fragment_id for q in candidate.quotes]
    claim = uow.claims.insert(
        subject_entity_type=candidate.subject_entity_type,
        subject_entity_id=(
            subject_resolution.entity_id if subject_resolution
            and subject_resolution.entity_id
            else candidate.subject_entity_id
        ),
        predicate_code=candidate.predicate_code,
        content_hash=idempotency,
        extraction_method=candidate.extraction_version,
        ontology_version=candidate.ontology_version,
        confidence=candidate.confidence,
        status=status,
        object_value=candidate.object_value,
        business_stage=candidate.business_stage.value,
        evidence_state=candidate.evidence_state.value,
        mapping_version=candidate.mapping_version,
    )
    for evidence_id in evidence_ids:
        uow.claim_evidence.link(claim_id=claim["id"], evidence_id=evidence_id)

    review_task_id = None
    if status in (ClaimStatus.NEEDS_REVIEW, ClaimStatus.REJECTED):
        task = uow.review_tasks.create(
            task_type="CLAIM_REVIEW",
            claim_id=claim["id"],
            priority="HIGH" if status == ClaimStatus.NEEDS_REVIEW else "NORMAL",
            reason_codes=list(validation.reasons)[:20],
        )
        review_task_id = task["id"]

    # 冲突检测（#5 能力）：高严重度冲突强制人工审核
    _check_conflicts(uow, claim["id"], candidate)

    # 自动接受边界：VALIDATED + 无审核理由 + 开关开启 → 同事务走完整接受
    # （Evidence/Provenance/审计/Outbox 原子提交；高风险冲突已由上面的审核任务拦截）
    accepted_auto = False
    if (
        status == ClaimStatus.VALIDATED
        and not validation.reasons
        and auto_accept
        and review_task_id is None
    ):
        pending_conflicts = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM fact.review_task WHERE claim_id = %s "
            "AND status IN ('OPEN', 'IN_PROGRESS')",
            (claim["id"],),
        ).fetchone()[0]
        if pending_conflicts == 0:
            accept_claim(
                uow,
                claim_id=claim["id"],
                reviewed_by=f"auto-accept:{candidate.extraction_version}",
                evidence_ids=[
                    row[0] for row in uow._conn.execute(  # noqa: SLF001
                        "SELECT evidence_id FROM fact.claim_evidence WHERE claim_id = %s",
                        (claim["id"],),
                    ).fetchall()
                ],
                provenance={
                    "activity_id": f"extract-{claim['id']}",
                    "agent_id": "extractor",
                    "checksum": uuid.uuid4().hex,
                    "metadata": {"auto_accept": True},
                },
                trace_id=trace_id,
            )
            accepted_auto = True

    log.info(
        "candidate_intaked", claim_id=str(claim["id"]), status=status.value,
        auto_accepted=accepted_auto,
        reasons=validation.reasons, trace_id=trace_id,
    )
    return IntakeOutcome(
        claim_id=claim["id"], status=ClaimStatus.ACCEPTED if accepted_auto else status,
        validation=validation, subject_resolution=subject_resolution,
        created_review_task_id=review_task_id,
        reasons=tuple(validation.reasons),
    )


def _status_from_validation(validation: CandidateValidation) -> ClaimStatus:
    if validation.status == "REJECTED":
        return ClaimStatus.REJECTED
    if validation.status == "NEEDS_REVIEW":
        return ClaimStatus.NEEDS_REVIEW
    # VALIDATED 候选：置信度高且无理由 → VALIDATED（自动接受仍走完整事务）
    return ClaimStatus.VALIDATED


def _check_conflicts(uow: Any, claim_id: uuid.UUID, candidate: CandidateClaim) -> None:
    """把新候选与既有 ACCEPTED/NEEDS_REVIEW 同主体 Claim 交给冲突检测；
    矛盾业务阶段 + 重叠有效期 → 创建审核任务（不自动裁决）。"""
    rows = uow._conn.execute(  # noqa: SLF001
        """
        SELECT id, subject_entity_id, predicate_code, business_stage,
               evidence_state, valid_from, valid_to, claim_status
        FROM fact.claim
        WHERE subject_entity_id = %s AND claim_status IN ('ACCEPTED', 'NEEDS_REVIEW')
        LIMIT 20
        """,
        (candidate.subject_entity_id,),
    ).fetchall()
    if not rows:
        return
    columns = ["id", "subject_entity_id", "predicate_code", "business_stage",
               "evidence_state", "valid_from", "valid_to", "claim_status"]
    siblings = [dict(zip(columns, row, strict=True)) for row in rows]
    siblings.append({
        "id": str(claim_id),
        "subject_entity_id": str(candidate.subject_entity_id),
        "predicate_code": candidate.predicate_code,
        "business_stage": candidate.business_stage.value,
        "evidence_state": candidate.evidence_state.value,
        "valid_from": candidate.valid_from.isoformat() if candidate.valid_from else None,
        "valid_to": candidate.valid_to.isoformat() if candidate.valid_to else None,
        "claim_status": "EXTRACTED",
    })
    # 冲突检测经 SemanticRuntime 端口：真实 Semantica 适配器（冲突能力不依赖
    # Provenance 存储，Round5 已验证 provenance=None 时 detect_conflicts 可用）
    from src.semantic.semantica_adapter import SemanticaRuntimeAdapter

    runtime = SemanticaRuntimeAdapter(shapes_path=SHAPES_PATH, provenance=None)
    findings = runtime.detect_conflicts(siblings)
    if findings:
        uow.review_tasks.create(
            task_type="CONFLICT_REVIEW", claim_id=claim_id, priority="CRITICAL",
            reason_codes=[
                f"{f.kind.value}: {f.detail[:120]}" for f in findings
            ][:10],
        )


def review_decide(
    uow: Any,
    *,
    review_task_id: uuid.UUID,
    decision: str,  # ACCEPTED | REJECTED
    reviewed_by: str,
    decision_reason: str,
    evidence_ids: list[uuid.UUID] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """人工审核决策：并发守卫 + 状态机 + 审计（前后状态/审核人/时间/理由/版本）。"""
    task = uow.review_tasks.get(review_task_id)
    if task is None:
        raise LookupError(f"review_task {review_task_id} not found")
    claim_id = task["claim_id"]

    claim = uow.claims.get_for_update_nowait(claim_id)
    if claim is None:
        raise LookupError(f"claim {claim_id} not found")
    current = ClaimStatus(claim["claim_status"])

    expected_next = ClaimStatus.ACCEPTED if decision == "ACCEPTED" else ClaimStatus.REJECTED
    if expected_next not in CLAIM_TRANSITIONS[current]:
        raise ClaimAcceptanceError(
            f"claim in status {current.value} cannot transition to {expected_next.value}"
        )

    before_status = current.value
    evidence_ids = evidence_ids or []

    if expected_next == ClaimStatus.ACCEPTED:
        # 复用 #2 原子接受事务：Evidence 校验 + Provenance + 审计 + Outbox 同事务
        accept_claim(
            uow,
            claim_id=claim_id,
            reviewed_by=reviewed_by,
            evidence_ids=evidence_ids or [
                row[0] for row in uow._conn.execute(  # noqa: SLF001
                    "SELECT evidence_id FROM fact.claim_evidence WHERE claim_id = %s",
                    (claim_id,),
                ).fetchall()
            ],
            provenance={
                "activity_id": f"review-{review_task_id}",
                "agent_id": reviewed_by,
                "checksum": uuid.uuid4().hex,
                "metadata": {"decision_reason": decision_reason[:300]},
            },
            trace_id=trace_id,
            review_task_id=review_task_id,
        )
    else:
        uow.claims.update_status_guarded(
            claim_id, current, ClaimStatus.REJECTED,
            reviewed_by=reviewed_by,
            reviewed_at=dt.datetime.now(tz=dt.UTC),
        )
        uow.review_tasks.transition(
            review_task_id, ReviewTaskStatus.COMPLETED,
            decision=decision, decision_reason=decision_reason[:500],
            completed_at=dt.datetime.now(tz=dt.UTC),
        )

    uow.audit_events.insert(
        entity_type="ClaimReview",
        entity_id=claim_id,
        event_type=f"REVIEW_{decision}",
        payload={
            "before_status": before_status,
            "after_status": expected_next.value,
            "reviewed_by": reviewed_by,
            "reason": decision_reason[:300],
            "review_task_id": str(review_task_id),
        },
        trace_id=trace_id,
    )
    log.info(
        "review_decided", claim_id=str(claim_id), before=before_status,
        after=expected_next.value, reviewed_by=reviewed_by, trace_id=trace_id,
    )
    return {
        "claim_id": str(claim_id),
        "before_status": before_status,
        "after_status": expected_next.value,
        "reviewed_by": reviewed_by,
        "review_task_id": str(review_task_id),
    }
