"""Evidence Grounding 与候选校验（issue #7 范围项 3/4 + 负向场景）。

Grounding：引文必须能映射回指定 DocumentVersion、页码和文本范围，
且 quote_text 在片段原文中逐字可定位——编造引文/页码不符进 NEEDS_REVIEW
或 REJECTED，绝不接受。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src.claims.schemas import (
    BusinessStage,
    CandidateClaim,
    EvidenceState,
)

GROUNDING_OK = "OK"
GROUNDING_FRAGMENT_MISSING = "FRAGMENT_MISSING"
GROUNDING_VERSION_MISMATCH = "VERSION_MISMATCH"
GROUNDING_TEXT_NOT_FOUND = "TEXT_NOT_FOUND"
GROUNDING_RANGE_MISMATCH = "RANGE_MISMATCH"


@dataclass(frozen=True)
class GroundingResult:
    fragment_id: uuid.UUID
    status: str
    detail: str | None = None


@dataclass
class CandidateValidation:
    """校验结论：可入库状态 + 拒绝/审核原因（全部保留，不静默）。"""

    status: str  # VALIDATED | NEEDS_REVIEW | REJECTED
    grounding: list[GroundingResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _normalize_ws(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text).strip()


def ground_claim(
    claim: CandidateClaim, *, fragments_by_id: dict[uuid.UUID, dict[str, Any]]
) -> list[GroundingResult]:
    """逐条引文定位校验。"""
    results: list[GroundingResult] = []
    for quote in claim.quotes:
        fragment = fragments_by_id.get(quote.evidence_fragment_id)
        if fragment is None:
            results.append(
                GroundingResult(
                    quote.evidence_fragment_id, GROUNDING_FRAGMENT_MISSING,
                    "evidence fragment does not exist",
                )
            )
            continue
        if str(fragment.get("document_version_id")) != str(quote.document_version_id):
            results.append(
                GroundingResult(
                    quote.evidence_fragment_id, GROUNDING_VERSION_MISMATCH,
                    "quote references a different document version",
                )
            )
            continue
        original = _normalize_ws(str(fragment.get("quote_text") or ""))
        target = _normalize_ws(quote.quote_text)
        if target not in original:
            results.append(
                GroundingResult(
                    quote.evidence_fragment_id, GROUNDING_TEXT_NOT_FOUND,
                    "quote text not found in fragment (fabricated or altered)",
                )
            )
            continue
        page_mismatch = int(fragment.get("page_number") or 1) != quote.page_number
        char_mismatch = (
            int(fragment.get("char_start") or 0) != quote.char_start
            or int(fragment.get("char_end") or 0) != quote.char_end
        )
        if page_mismatch or char_mismatch:
            results.append(
                GroundingResult(
                    quote.evidence_fragment_id, GROUNDING_RANGE_MISMATCH,
                    "quote page/range does not match fragment",
                )
            )
            continue
        results.append(GroundingResult(quote.evidence_fragment_id, GROUNDING_OK))
    return results


def validate_candidate(
    claim: CandidateClaim,
    *,
    fragments_by_id: dict[uuid.UUID, dict[str, Any]],
) -> CandidateValidation:
    """候选 → VALIDATED / NEEDS_REVIEW / REJECTED 的确定性校验。

    规则（issue #7 不变量 + 负向场景）：
    1. 引文 Grounding：不可定位 → NEEDS_REVIEW（编造）或 REJECTED（片段不存在）；
    2. 阶段/证据状态语义：
       - MASS_PRODUCTION 由 PLATFORM_CLASSIFICATION_ONLY 单独支持 → REJECTED；
       - REVENUE_DISCLOSED 无报告期/主营构成/收入原文 → NEEDS_REVIEW；
       - COMPANY_DENIAL 无原文 → NEEDS_REVIEW；
    3. 证据不足/UNKNOWN：只能是"证据不足"陈述，不得携带否定谓词 → REJECTED；
    4. 条件/计划/未来时态（hedged）：绝不提升为当前量产事实 → NEEDS_REVIEW。
    """
    validation = CandidateValidation(status="VALIDATED")
    validation.grounding = ground_claim(claim, fragments_by_id=fragments_by_id)

    for result in validation.grounding:
        if result.status == GROUNDING_FRAGMENT_MISSING:
            validation.reasons.append("quote references missing evidence fragment")
        elif result.status == GROUNDING_VERSION_MISMATCH:
            validation.reasons.append("quote version mismatch")
        elif result.status == GROUNDING_TEXT_NOT_FOUND:
            validation.reasons.append("quote text not found (possibly fabricated)")
        elif result.status == GROUNDING_RANGE_MISMATCH:
            validation.reasons.append("quote page/range mismatch")

    if not claim.quotes:
        validation.reasons.append("text claim requires at least one grounded quote")

    # 语义红线
    if (
        claim.business_stage == BusinessStage.MASS_PRODUCTION
        and claim.evidence_state == EvidenceState.PLATFORM_CLASSIFICATION_ONLY
    ):
        validation.reasons.append(
            "MASS_PRODUCTION cannot be supported by platform classification alone"
        )

    if claim.evidence_state == EvidenceState.REVENUE_DISCLOSED:
        revenue_ok = bool(claim.object_value and claim.object_value.get("period"))
        if not revenue_ok and not any(
            r.status == GROUNDING_OK for r in validation.grounding
        ):
            validation.reasons.append(
                "REVENUE_DISCLOSED requires a report period, segment or revenue quote"
            )

    if claim.evidence_state == EvidenceState.COMPANY_DENIAL:
        if not any(r.status == GROUNDING_OK for r in validation.grounding):
            validation.reasons.append("COMPANY_DENIAL requires the original text")

    if claim.evidence_state == EvidenceState.EVIDENCE_INSUFFICIENT:
        validation.reasons.append(
            "EVIDENCE_INSUFFICIENT must not produce a fact claim (unknown != false)"
        )

    hedged = bool(claim.object_value and claim.object_value.get("hedged"))
    if hedged:
        validation.reasons.append(
            "conditional/planned/future phrasing cannot be promoted to a current fact"
        )

    # 分类
    fatal_keywords = (
        "quote references missing evidence fragment",
        "quote version mismatch",
        "quote text not found",
        "quote page/range mismatch",
        "MASS_PRODUCTION cannot be supported",
        "EVIDENCE_INSUFFICIENT must not produce",
    )
    fatal = any(any(k in reason for k in fatal_keywords) for reason in validation.reasons)
    if fatal:
        validation.status = "REJECTED"
    elif validation.reasons:
        validation.status = "NEEDS_REVIEW"
    return validation
