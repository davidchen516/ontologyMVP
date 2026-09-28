"""候选 Claim 的严格 Pydantic Schema（issue #7 范围项 1）。

红线：
- 阶段（business_stage）与证据状态（evidence_state）严格分离；
- LLM 输出只能填充本 Schema 字段，任何"工具指令/URL/代码"内容
  被视作数据并在 Grounding/校验层拒绝，绝不执行；
- 证据不足（UNKNOWN/EVIDENCE_INSUFFICIENT）不生成否定 Claim——
  evidence_state=EVIDENCE_INSUFFICIENT 的候选只能表达"证据不足"，
  不得携带否定性谓词语义。
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BusinessStage(StrEnum):
    TECHNOLOGY_RESERVE = "TECHNOLOGY_RESERVE"
    RESEARCH = "RESEARCH"
    PROTOTYPE = "PROTOTYPE"
    SAMPLE_VALIDATION = "SAMPLE_VALIDATION"
    SMALL_BATCH = "SMALL_BATCH"
    MASS_PRODUCTION = "MASS_PRODUCTION"
    UNKNOWN = "UNKNOWN"


class EvidenceState(StrEnum):
    PLATFORM_CLASSIFICATION_ONLY = "PLATFORM_CLASSIFICATION_ONLY"
    PRODUCT_DISCLOSED = "PRODUCT_DISCLOSED"
    REVENUE_DISCLOSED = "REVENUE_DISCLOSED"
    COMPANY_DENIAL = "COMPANY_DENIAL"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"


class GroundedQuote(BaseModel):
    """原文定位：必须映射回 DocumentVersion + EvidenceFragment + 页码/范围。"""

    model_config = ConfigDict(frozen=True)

    evidence_fragment_id: uuid.UUID
    document_version_id: uuid.UUID
    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    quote_text: str = Field(min_length=1)

    @field_validator("char_end")
    @classmethod
    def _end_after_start(cls, value: int, info: Any) -> int:
        start = info.data.get("char_start")
        if start is not None and value < start:
            raise ValueError("char_end must be >= char_start")
        return value


class CandidateClaim(BaseModel):
    """候选 Claim：抽取器输出 → 校验/映射 → 状态机入库的唯一形态。"""

    model_config = ConfigDict(frozen=True)

    subject_entity_type: str = Field(pattern="^(Company|Security)$")
    subject_entity_id: uuid.UUID
    predicate_code: str = Field(min_length=2, max_length=64)
    object_entity_type: str | None = None
    object_entity_id: uuid.UUID | None = None
    object_value: dict[str, Any] | None = None
    business_stage: BusinessStage
    evidence_state: EvidenceState
    valid_from: dt.datetime | None = None
    valid_to: dt.datetime | None = None
    temporal_precision: str = Field(default="UNKNOWN", max_length=20)
    quotes: list[GroundedQuote] = Field(min_length=0, max_length=20)
    confidence: float = Field(ge=0, le=1)
    extraction_version: str = Field(min_length=1, max_length=64)
    model_id: str | None = None
    prompt_version: str | None = None
    ontology_version: str = Field(min_length=1, max_length=32)
    mapping_version: str | None = None

    @field_validator("predicate_code")
    @classmethod
    def _deny_negation_semantics(cls, value: str) -> str:
        lowered = value.lower()
        if any(word in lowered for word in ("deny", "not_", "no_", "refute")):
            raise ValueError(
                "negation must use DENIES_INVOLVEMENT predicate + COMPANY_DENIAL "
                "evidence state, not a negative predicate"
            )
        return value

    @field_validator("object_value")
    @classmethod
    def _object_xor(cls, value: Any, info: Any) -> Any:
        object_id = info.data.get("object_entity_id")
        if value is None and object_id is None:
            raise ValueError("claim requires an object entity or object value")
        return value


class ExtractorOutput(BaseModel):
    """Schema-guided 抽取端口的输出：候选列表 + 拒绝理由（不产生工具指令）。"""

    model_config = ConfigDict(frozen=True)

    claims: list[CandidateClaim] = Field(default_factory=list)
    rejected_notes: list[str] = Field(default_factory=list)
    extraction_version: str = Field(min_length=1)
