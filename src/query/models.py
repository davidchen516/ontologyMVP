"""受控 QueryPlan 模型：EntityRef、SemanticFilter、NumericFilter（issue #9）。

红线：
- 所有 SQL/Cypher 来自白名单模板（编译器强制）；
- max_hops <= 4、max_results <= 100；
- as_of（业务有效时间）与 known_at（系统已知时间）分离；
- Intent 白名单——非法 Intent/Operator/Path 被拒绝，不猜测执行。
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueryIntent(StrEnum):
    ENTITY_LOOKUP = "ENTITY_LOOKUP"
    SEMANTIC_SCREEN = "SEMANTIC_SCREEN"
    EXPLAIN_RELATION = "EXPLAIN_RELATION"
    TIMELINE = "TIMELINE"
    COMPARE = "COMPARE"
    EVIDENCE_SEARCH = "EVIDENCE_SEARCH"


class PathOperator(StrEnum):
    TOTAL_POSITIVE = "TOTAL_POSITIVE"
    CONSECUTIVE_POSITIVE = "CONSECUTIVE_POSITIVE"
    MIN_VALUE = "MIN_VALUE"
    MAX_VALUE = "MAX_VALUE"


class EntityRef(BaseModel):
    """实体引用：ID 或名称+类型，不信任用户输入的自由表名/列名。"""

    model_config = ConfigDict(frozen=True)

    entity_type: str = Field(pattern="^(Company|Security|Product|Theme|Concept|Industry)$")
    entity_id: UUID | None = None
    name_hint: str | None = Field(default=None, max_length=200)


class SemanticFilter(BaseModel):
    """语义筛选：本体概念/关系/阶段——只使用白名单谓词。"""

    model_config = ConfigDict(frozen=True)

    concept_name: str | None = Field(default=None, max_length=200)
    relation_type: str | None = Field(default=None, max_length=64)
    business_stage: str | None = Field(default=None, max_length=50)
    max_hops: int = Field(default=2, ge=1, le=4)


class NumericFilter(BaseModel):
    """财务筛选：只使用 financial_metric_catalog 白名单中的指标。

    PeriodRule 与 operator/fiscal_years 必须一致（验收 5 的口径约束）：
    - LAST_N_FY            ⇒ fiscal_years == N；
    - LAST_3_FY_TOTAL_POSITIVE ⇒ operator=TOTAL_POSITIVE 且 fiscal_years=3；
    - CONSECUTIVE_3_FY_POSITIVE ⇒ operator=CONSECUTIVE_POSITIVE 且 fiscal_years=3。
    """

    model_config = ConfigDict(frozen=True)

    metric_code: str = Field(min_length=2, max_length=64)
    operator: PathOperator
    threshold: float | None = None
    period_rule: str = Field(default="LAST_3_FY", max_length=50)
    fiscal_years: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _period_rule_consistent(self) -> NumericFilter:
        rule = self.period_rule
        if self.operator in (PathOperator.MIN_VALUE, PathOperator.MAX_VALUE) \
                and self.threshold is None:
            raise ValueError(
                f"operator {self.operator.value} requires threshold"
            )
        if rule == "LAST_1_FY" and self.fiscal_years != 1:
            raise ValueError("LAST_1_FY requires fiscal_years=1")
        if rule == "LAST_3_FY" and self.fiscal_years != 3:
            raise ValueError("LAST_3_FY requires fiscal_years=3")
        if rule == "LAST_5_FY" and self.fiscal_years != 5:
            raise ValueError("LAST_5_FY requires fiscal_years=5")
        if rule == "LAST_3_FY_TOTAL_POSITIVE" and (
            self.operator is not PathOperator.TOTAL_POSITIVE
            or self.fiscal_years != 3
        ):
            raise ValueError(
                "LAST_3_FY_TOTAL_POSITIVE requires operator=TOTAL_POSITIVE "
                "and fiscal_years=3"
            )
        if rule == "CONSECUTIVE_3_FY_POSITIVE" and (
            self.operator is not PathOperator.CONSECUTIVE_POSITIVE
            or self.fiscal_years != 3
        ):
            raise ValueError(
                "CONSECUTIVE_3_FY_POSITIVE requires "
                "operator=CONSECUTIVE_POSITIVE and fiscal_years=3"
            )
        return self


class QueryPlan(BaseModel):
    """受控查询计划：Planner 输出的唯一合法形态。"""

    model_config = ConfigDict(frozen=True)

    plan_id: UUID = Field(default_factory=uuid4)
    intent: QueryIntent
    subject: EntityRef | None = None
    object_entity: EntityRef | None = None  # EXPLAIN_RELATION 的目标实体
    semantic_filter: SemanticFilter | None = None
    numeric_filter: NumericFilter | None = None
    evidence_required: bool = True
    as_of: dt.datetime | None = None  # 业务有效时间过滤
    known_at: dt.datetime | None = None  # 系统已知时间过滤
    max_results: int = Field(default=50, ge=1, le=100)
    timeout_seconds: int = Field(default=30, ge=1, le=60)

    @field_validator("intent")
    @classmethod
    def _intent_whitelist(cls, value: QueryIntent) -> QueryIntent:
        allowed = {QueryIntent.ENTITY_LOOKUP, QueryIntent.SEMANTIC_SCREEN,
                   QueryIntent.EXPLAIN_RELATION, QueryIntent.TIMELINE,
                   QueryIntent.COMPARE, QueryIntent.EVIDENCE_SEARCH}
        if value not in allowed:
            raise ValueError(f"intent {value!r} not supported")
        return value

    @field_validator("semantic_filter")
    @classmethod
    def _no_injection(cls, value: SemanticFilter | None) -> SemanticFilter | None:
        if value is None:
            return None
        # 深度防御：SQL/Cypher 关键字黑名单（真正的保障是参数化+白名单模板，
        # 黑名单用于尽早拒绝可疑输入并给出受控 422）
        dangerous = ("DROP", "DELETE", "INSERT", "UPDATE", "CREATE", "ALTER",
                     "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "PG_SLEEP",
                     "MERGE", "DETACH", "CALL", "LOAD CSV", "COPY", ";", "//", "/*")
        for field_value in (value.concept_name, value.relation_type,
                            value.business_stage):
            if field_value:
                upper = field_value.upper()
                for pattern in dangerous:
                    if pattern in upper:
                        raise ValueError(
                            "potentially unsafe input detected in filter"
                        )
        return value


class QueryStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"


class GroundedResult(BaseModel):
    """每个命中公司的结构化证据包（验收 10）。"""

    model_config = ConfigDict(frozen=True)

    company_id: UUID
    company_name: str
    claim_ids: list[UUID] = Field(default_factory=list)
    business_stage: str | None = None
    evidence_state: str | None = None
    financial_value: float | None = None
    report_period: str | None = None
    currency: str | None = None
    financial_detail: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    reasoning_path: list[str] = Field(default_factory=list)
    data_freshness: str | None = None


class QueryResponse(BaseModel):
    """Grounded 结构化响应：不添加结构化结果中不存在的任何实体。"""

    model_config = ConfigDict(frozen=True)

    query_id: UUID
    status: QueryStatus
    intent: QueryIntent
    results: list[GroundedResult] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)  # 被排除的候选+原因
    unknowns: list[str] = Field(default_factory=list)  # 证据不足的候选
    conflicts: list[str] = Field(default_factory=list)  # 冲突提示
    period_rule: str | None = None  # 财务口径
    degraded: bool = False  # 是否降级
    degradation_notes: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    error: str | None = None
