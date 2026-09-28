"""Schema-guided 抽取端口与规则抽取器（issue #7 范围项 2）。

- Extractor 端口：按文档片段产出 CandidateClaim；
  模型供应商可替换（生产 LLM 适配器后续接入；本 Issue 提供规则抽取器，
  测试全部 Fixture 驱动，无真实模型费用）；
- 模型输出永远先落到 ExtractorOutput——自由文本/工具指令
  一律被拒收为数据，端口没有执行路径；
- 幂等缓存键：(document_version_id, fragment checksum 集, extraction_version)
  ——同一文档与抽取版本重复执行命中缓存，不重复产生候选（不重复费用）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Protocol

from src.claims.schemas import (
    BusinessStage,
    CandidateClaim,
    EvidenceState,
    ExtractorOutput,
    GroundedQuote,
)

EXTRACTION_VERSION = "rules-extractor-0.1.0"

# 阶段与证据状态的触发词（证据驱动的保守规则；模型版可替换同端口）
_STAGE_PATTERNS: list[tuple[BusinessStage, EvidenceState, str]] = [
    (BusinessStage.MASS_PRODUCTION, EvidenceState.PRODUCT_DISCLOSED,
     r"mass production|规模化量产|已?实现量产|批量生产"),
    (BusinessStage.SMALL_BATCH, EvidenceState.PRODUCT_DISCLOSED,
     r"small[- ]batch|小批量"),
    (BusinessStage.SAMPLE_VALIDATION, EvidenceState.PRODUCT_DISCLOSED,
     r"sample validation|送样|客户验证|样品验证"),
    (BusinessStage.PROTOTYPE, EvidenceState.PRODUCT_DISCLOSED,
     r"prototype|样机|原型"),
    (BusinessStage.RESEARCH, EvidenceState.PRODUCT_DISCLOSED,
     r"research|研发|研制"),
    (BusinessStage.TECHNOLOGY_RESERVE, EvidenceState.PRODUCT_DISCLOSED,
     r"technology reserve|技术储备"),
    (BusinessStage.UNKNOWN, EvidenceState.REVENUE_DISCLOSED,
     r"revenue|营业收入|营业收入稳定增长|形成收入"),
    (BusinessStage.UNKNOWN, EvidenceState.COMPANY_DENIAL,
     r"denies involvement|否认|未参与|不涉及"),
    (BusinessStage.UNKNOWN, EvidenceState.EVIDENCE_INSUFFICIENT,
     r"insufficient evidence|证据不足|暂无明确信息"),
]

# 提升阻断：条件/否定/计划/未来时态词——同句出现时不得提升为当前事实。
# 覆盖英文完整变体（expects to / is going to / aims to / intends to / would /
# shall / scheduled to / upcoming / plans to / expected to / anticipates）+
# 中文常见时态（计划/拟/将于/即将/预计/未来/尚未/暂未/如果/若/一旦）
_HEDGES = re.compile(
    r"\bif\b|\bunless\b|\bcondition(al)? on\b|"
    r"\bplans? to\b|\bplans\b|\bplanning\b|"
    r"\bexpects? to\b|\bexpected to\b|\banticipat(es|ed|ing)\b|"
    r"\bis going to\b|\baims? to\b|\bintends? to\b|"
    r"\bwould\b|\bshall\b|\bwill\b|"
    r"\bscheduled to\b|\bupcoming\b|\bforthcoming\b|"
    r"\bmight\b|\bmay\b|\bcould\b|"
    r"计划|拟|将于|即将|预计|未来|尚未|暂未|如果|若|一旦|待|或有望",
    re.IGNORECASE,
)

# Prompt 注入样例：只作为文本，命中时记录拒绝理由（绝不产生工具调用）
_INJECTION = re.compile(
    r"ignore (all )?(previous |system )?instructions|"
    r"send (an )?email|访问url|execute .* command|invoke tool|"
    r"忽略(全部)?(之前的?)?(系统)?指令|发送邮件|执行命令|访问链接",
    re.IGNORECASE,
)


class Extractor(Protocol):
    extraction_version: str

    def extract(
        self, fragments: list[dict[str, Any]], *, ontology_version: str
    ) -> ExtractorOutput: ...


def candidate_idempotency_key(
    document_version_id: uuid.UUID,
    fragment_checksums: list[str],
    extraction_version: str,
    claim: CandidateClaim,
) -> str:
    """候选幂等键：DocumentVersion + Evidence + 抽取版本 + 规范化内容 Hash。"""
    payload = json.dumps(
        {
            "document_version_id": str(document_version_id),
            "fragments": sorted(fragment_checksums),
            "extraction_version": extraction_version,
            "claim": {
                "subject": [claim.subject_entity_type, str(claim.subject_entity_id)],
                "predicate": claim.predicate_code,
                "object_entity": str(claim.object_entity_id) if claim.object_entity_id else None,
                "object_value": claim.object_value,
                "stage": claim.business_stage.value,
                "evidence": claim.evidence_state.value,
                "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
                "valid_to": claim.valid_to.isoformat() if claim.valid_to else None,
                "quotes": [q.quote_text for q in claim.quotes],
            },
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class RulesExtractor:
    """证据驱动的规则抽取器：满足端口契约，模型可替换。"""

    extraction_version = EXTRACTION_VERSION

    def extract(
        self, fragments: list[dict[str, Any]], *, ontology_version: str
    ) -> ExtractorOutput:
        claims: list[CandidateClaim] = []
        rejected: list[str] = []

        for fragment in fragments:
            text = str(fragment.get("quote_text") or fragment.get("text") or "")
            checksum = str(fragment.get("checksum") or "")
            fragment_id = fragment.get("id")
            version_id = fragment.get("document_version_id")
            page = int(fragment.get("page_number") or 1)
            char_start = int(fragment.get("char_start") or 0)
            char_end = int(fragment.get("char_end") or len(text))

            if fragment_id is None or version_id is None:
                rejected.append(f"fragment missing id/version: {checksum[:12]}")
                continue
            if _INJECTION.search(text):
                # Prompt 注入内容按数据对待：记录拒绝，绝不执行
                rejected.append(f"injection content treated as data: {checksum[:12]}")
                continue

            for stage, evidence_state, pattern in _STAGE_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    hedged = bool(_HEDGES.search(text))
                    quote = GroundedQuote(
                        evidence_fragment_id=fragment_id,
                        document_version_id=version_id,
                        page_number=page,
                        char_start=char_start,
                        char_end=char_end,
                        quote_text=text[:2000],
                    )
                    claims.append(
                        CandidateClaim(
                            subject_entity_type="Company",
                            subject_entity_id=fragment["company_id"],
                            predicate_code=(
                                "DENIES_INVOLVEMENT"
                                if evidence_state == EvidenceState.COMPANY_DENIAL
                                else "PRODUCES"
                            ),
                            object_value={"stage": stage.value, "hedged": hedged},
                            business_stage=stage,
                            evidence_state=evidence_state,
                            confidence=0.5 if hedged else 0.85,
                            quotes=[quote],
                            extraction_version=self.extraction_version,
                            model_id="rules",
                            prompt_version="n/a",
                            ontology_version=ontology_version,
                        )
                    )
                    break  # 一个片段只产一个候选（保守）

        return ExtractorOutput(
            claims=claims, rejected_notes=rejected,
            extraction_version=self.extraction_version,
        )
