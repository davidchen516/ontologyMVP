"""Claim 审核状态机编排 + 审核决策 API + 审计（issue #7 范围项 5/6/7）。

- intake_candidate：抽取 → Grounding/校验 → SHACL（#5 端口）→ 实体解析 →
  幂等入库（content_hash = 候选幂等键），状态 EXTRACTED/VALIDATED/
  NEEDS_REVIEW/REJECTED；
- review_decide：人工审核决策（NEEDS_REVIEW → ACCEPTED/REJECTED，
  乐观并发守卫：版本不符返回冲突，不覆盖他人决定）；
- accept_claim 事务复用 #2 的 claim_service（Evidence/Provenance/审计/Outbox
  同事务）；冲突检测经 #5 适配器；高严重度冲突强制人工审核。
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
# 默认值仅用于无 Settings 上下文的直接调用；生产经 Settings.claim_auto_accept 注入
AUTO_ACCEPT_ENABLED = True


def intake_candidate(
    uow: Any,
    candidate: CandidateClaim,
    *,
    document_version_id: uuid.UUID,
    fragment_checksums: list[str],
    security_id: uuid.UUID | None,
    trace_id: str | None = None,
    auto_accept: bool = AUTO_ACCEPT_ENABLED,
    shacl_validate: bool = False,
    check_conflicts: bool = True,
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

    # 谓词白名单快速校验（常量集合查找——shapes.ttl ClaimShape 的 Python 内联）
    shacl_reasons = _fast_predicate_check(candidate)
    # 完整 SHACL（pyshacl 引擎——高成本，仅显式请求时执行）
    if shacl_validate:
        shacl_reasons.extend(_shacl_validate(uow, candidate))
    validation.reasons.extend(shacl_reasons)
    if shacl_reasons and validation.status == "VALIDATED":
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
    elif status == ClaimStatus.VALIDATED and not auto_accept:
        # 自动接受关闭：全部候选转人工审核（回滚要求）
        task = uow.review_tasks.create(
            task_type="CLAIM_REVIEW",
            claim_id=claim["id"],
            priority="NORMAL",
            reason_codes=["auto_accept_disabled"],
        )
        review_task_id = task["id"]

    # 冲突检测（#5 能力）：高严重度冲突强制人工审核（可跳过——测试提速）
    if check_conflicts:
        _check_conflicts(
            uow, claim["id"], candidate,
            resolved_subject_id=(
                subject_resolution.entity_id
                if subject_resolution and subject_resolution.entity_id
                else None
            ),
        )

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
                    "checksum": idempotency,
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


# shapes.ttl stock:ClaimShape 的谓词白名单（sh:in 列表的 Python 内联——
# 完整 SHACL 由 _shacl_validate 按需执行；此处为快速路径）
ALLOWED_PREDICATES = frozenset({
    "ISSUES", "LISTED_ON", "CLASSIFIED_AS", "TAGGED_AS",
    "PRODUCES", "DEVELOPS", "HAS_TECHNOLOGY_RESERVE",
    "SAMPLES_TO", "SUPPLIES_TO", "USES_TECHNOLOGY",
    "HAS_REVENUE_FROM", "DENIES_INVOLVEMENT",
    "CONTROLS", "HOLDS", "HAS_SEGMENT", "MAPPED_TO_PRODUCT",
    "VERIFIED_RELEVANT_TO", "DIRECT_EXPOSURE_TO",
    "SECOND_ORDER_EXPOSURE_TO",
})


def _fast_predicate_check(candidate: CandidateClaim) -> list[str]:
    """谓词白名单快速校验：shapes.ttl sh:in 列表的 Python 内联（O(1)）。"""
    if candidate.predicate_code not in ALLOWED_PREDICATES:
        return [
            f"shacl: predicate_code {candidate.predicate_code!r} "
            "not in allowed whitelist (shapes.ttl ClaimShape)"
        ]
    return []


_SEMANTIC_RUNTIME = None


def _get_semantic_runtime():
    """模块级懒加载单例：SHACL 校验与冲突检测共用同一运行时实例。"""
    global _SEMANTIC_RUNTIME
    if _SEMANTIC_RUNTIME is None:
        from pathlib import Path

        from src.semantic.semantica_adapter import SemanticaRuntimeAdapter

        shapes = str(Path(__file__).resolve().parents[2] / "ontology" / "shapes.ttl")
        _SEMANTIC_RUNTIME = SemanticaRuntimeAdapter(shapes_path=shapes, provenance=None)
    return _SEMANTIC_RUNTIME


def _shacl_validate(uow: Any, candidate: CandidateClaim) -> list[str]:
    """把候选 Claim 转为 RDF 图并按 shapes.ttl 执行 SHACL（#5 端口）。

    追加校验理由（谓词白名单、对象类型约束、枚举范围等），绝不静默通过。
    """
    from src.semantic.claim_rdf import claim_to_graph

    runtime = _get_semantic_runtime()
    claim_dict = {
        "id": str(uuid.uuid4()),
        "subject_entity_type": candidate.subject_entity_type,
        "subject_entity_id": str(candidate.subject_entity_id),
        "predicate_code": candidate.predicate_code,
        "claim_status": "VALIDATED",
        "confidence": candidate.confidence,
        "business_stage": candidate.business_stage.value,
        "evidence_state": candidate.evidence_state.value,
        "object_entity_id": str(candidate.object_entity_id)
        if candidate.object_entity_id else None,
        "object_entity_type": candidate.object_entity_type,
        "object_value": candidate.object_value,
        "valid_from": candidate.valid_from,
        "valid_to": candidate.valid_to,
        "superseded_at": None,
        "evidence_ids": [str(q.evidence_fragment_id) for q in candidate.quotes],
    }
    try:
        report = runtime.validate_claim(claim_to_graph(claim_dict))
    except Exception as exc:  # noqa: BLE001 - SHACL 引擎失败不伪成功
        return [f"shacl engine failure: {type(exc).__name__}"]
    if report.conforms:
        return []
    return [f"shacl: {v.message or v.constraint}" for v in report.violations[:10]]


def _status_from_validation(validation: CandidateValidation) -> ClaimStatus:
    if validation.status == "REJECTED":
        return ClaimStatus.REJECTED
    if validation.status == "NEEDS_REVIEW":
        return ClaimStatus.NEEDS_REVIEW
    # VALIDATED 候选：置信度高且无理由 → VALIDATED（自动接受仍走完整事务）
    return ClaimStatus.VALIDATED


def _check_conflicts(
    uow: Any, claim_id: uuid.UUID, candidate: CandidateClaim,
    resolved_subject_id: uuid.UUID | None,
) -> None:
    """把新候选与既有 ACCEPTED/NEEDS_REVIEW 同主体 Claim 交给冲突检测；
    矛盾业务阶段 + 重叠有效期 → 创建审核任务（不自动裁决）。
    主体必须用解析后的权威 ID（与落库主体一致）。"""
    subject_id = resolved_subject_id or candidate.subject_entity_id
    rows = uow._conn.execute(  # noqa: SLF001
        """
        SELECT id, subject_entity_id, predicate_code, business_stage,
               evidence_state, valid_from, valid_to, claim_status
        FROM fact.claim
        WHERE subject_entity_id = %s AND claim_status IN ('ACCEPTED', 'NEEDS_REVIEW')
        LIMIT 20
        """,
        (subject_id,),
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
    # 冲突检测经 SemanticRuntime 端口：真实 Semantica 适配器（单例复用，
    # 冲突能力不依赖 Provenance 存储）
    runtime = _get_semantic_runtime()
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
    decided_at = dt.datetime.now(tz=dt.UTC)
    return {
        "claim_id": str(claim_id),
        "before_status": before_status,
        "after_status": expected_next.value,
        "reviewed_by": reviewed_by,
        "reviewed_at": decided_at.isoformat(),
        "decision_reason": decision_reason,
        "review_task_id": str(review_task_id),
    }
