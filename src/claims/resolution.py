"""实体与产品映射解析（issue #7 范围项 4）。

- Company 解析：优先 master.company_security 锚定（#4 的受控键），
  未见过的主体进审核，绝不凭名称猜；
- 产品别名映射：master.product_term_mapping（#2 表，normalized_term +
  confidence + review_status）；低于阈值或不唯一 → NEEDS_REVIEW；
- 高风险关系（实际控制人/核心供应商等）不做自动映射。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

MAP_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ResolutionResult:
    status: str  # RESOLVED | NEEDS_REVIEW | UNRESOLVED
    entity_id: uuid.UUID | None = None
    mapped_product_id: uuid.UUID | None = None
    reason: str | None = None


def resolve_subject_company(
    uow: Any, *, security_id: uuid.UUID | None, name_hint: str | None
) -> ResolutionResult:
    """证券锚定优先；无锚定时（无凭据不猜名称）进审核。"""
    if security_id is not None:
        row = uow._conn.execute(  # noqa: SLF001
            """
            SELECT c.id FROM master.company c
            JOIN master.company_security cs ON cs.company_id = c.id
            WHERE cs.security_id = %s ORDER BY cs.recorded_at DESC LIMIT 1
            """,
            (security_id,),
        ).fetchone()
        if row is not None:
            return ResolutionResult(status="RESOLVED", entity_id=row[0])
        return ResolutionResult(
            status="NEEDS_REVIEW", reason="security has no anchored company yet"
        )
    return ResolutionResult(
        status="UNRESOLVED",
        reason="subject resolution requires an anchored security; name hints are not trusted",
    )


def resolve_product_alias(
    uow: Any, *, raw_term: str, threshold: float = MAP_CONFIDENCE_THRESHOLD
) -> ResolutionResult:
    """产品别名 → master.product_term_mapping；不唯一/低置信度 → 审核。"""
    normalized = raw_term.strip().lower()
    rows = uow._conn.execute(  # noqa: SLF001
        """
        SELECT product_id, confidence, review_status
        FROM master.product_term_mapping
        WHERE normalized_term = %s AND review_status = 'ACCEPTED'
        ORDER BY confidence DESC
        """,
        (normalized,),
    ).fetchall()
    if not rows:
        return ResolutionResult(status="UNRESOLVED", reason=f"no mapping for {raw_term!r}")
    if len(rows) > 1 and rows[0][1] == rows[1][1]:
        return ResolutionResult(
            status="NEEDS_REVIEW",
            reason=f"ambiguous mapping for {raw_term!r} (equal top confidence)",
        )
    top_confidence = float(rows[0][1])
    if top_confidence < threshold:
        return ResolutionResult(
            status="NEEDS_REVIEW",
            reason=f"mapping confidence {top_confidence} below threshold {threshold}",
        )
    return ResolutionResult(status="RESOLVED", mapped_product_id=rows[0][0])
