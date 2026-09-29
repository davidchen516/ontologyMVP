"""QueryPlan Schema 单元测试：白名单、注入、约束（issue #9 验收 2/3）。"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError
from src.query.models import (
    EntityRef,
    NumericFilter,
    QueryIntent,
    QueryPlan,
    SemanticFilter,
)


def test_intent_whitelist():
    plan = QueryPlan(intent=QueryIntent.ENTITY_LOOKUP)
    assert plan.intent == QueryIntent.ENTITY_LOOKUP
    with pytest.raises(ValidationError):
        QueryPlan(intent="MALICIOUS_INTENT")


def test_max_hops_bounded():
    with pytest.raises(ValidationError):
        SemanticFilter(max_hops=5)  # > 4
    with pytest.raises(ValidationError):
        SemanticFilter(max_hops=0)  # < 1
    assert SemanticFilter(max_hops=4).max_hops == 4


def test_max_results_bounded():
    with pytest.raises(ValidationError):
        QueryPlan(intent=QueryIntent.SEMANTIC_SCREEN, max_results=200)
    assert QueryPlan(intent=QueryIntent.SEMANTIC_SCREEN, max_results=100).max_results == 100


def test_sql_injection_in_filter_rejected():
    with pytest.raises(ValidationError, match="unsafe"):
        QueryPlan(
            intent=QueryIntent.SEMANTIC_SCREEN,
            semantic_filter=SemanticFilter(
                concept_name="'; DROP TABLE fact.claim; --"
            ),
        )


def test_cypher_injection_rejected():
    with pytest.raises(ValidationError, match="unsafe"):
        QueryPlan(
            intent=QueryIntent.SEMANTIC_SCREEN,
            semantic_filter=SemanticFilter(
                business_stage="DETACH DELETE n"
            ),
        )


def test_entity_ref_type_whitelist():
    assert EntityRef(entity_type="Company").entity_type == "Company"
    with pytest.raises(ValidationError):
        EntityRef(entity_type="pg_catalog.pg_tables")


def test_as_of_and_known_at_independent():
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    plan = QueryPlan(
        intent=QueryIntent.SEMANTIC_SCREEN,
        as_of=now, known_at=now,
    )
    assert plan.as_of == now
    assert plan.known_at == now
    # 各自可选
    plan2 = QueryPlan(intent=QueryIntent.SEMANTIC_SCREEN, as_of=now)
    assert plan2.known_at is None


def test_numeric_filter_metric_must_be_string():
    nf = NumericFilter(
        metric_code="NET_CF_OPERATING",
        operator="TOTAL_POSITIVE",
    )
    assert nf.metric_code == "NET_CF_OPERATING"


def test_plan_id_is_uuid():
    plan = QueryPlan(intent=QueryIntent.ENTITY_LOOKUP)
    assert isinstance(plan.plan_id, uuid.UUID)


# ---- 审查修复后的新增覆盖 ----


def test_injection_blacklist_extended():
    """TRUNCATE/GRANT/EXECUTE/pg_sleep/分号等均在黑名单内。"""
    for payload in (
        "TRUNCATE TABLE fact.claim; --",
        "GRANT ALL ON fact.claim TO public",
        "EXECUTE sp_evil",
        "SELECT pg_sleep(10)--",
        "abc; DROP TABLE x",
    ):
        with pytest.raises(ValidationError, match="unsafe"):
            QueryPlan(
                intent=QueryIntent.SEMANTIC_SCREEN,
                semantic_filter=SemanticFilter(concept_name=payload),
            )


def test_period_rule_consistency():
    """PeriodRule 与 operator/fiscal_years 一致性（验收 5 口径约束）。"""
    # 合法：3FY 合计为正
    NumericFilter(
        metric_code="NET_CF_OPERATING", operator="TOTAL_POSITIVE",
        period_rule="LAST_3_FY_TOTAL_POSITIVE", fiscal_years=3,
    )
    # 合法：3FY 连续为正
    NumericFilter(
        metric_code="NET_CF_OPERATING", operator="CONSECUTIVE_POSITIVE",
        period_rule="CONSECUTIVE_3_FY_POSITIVE", fiscal_years=3,
    )
    # 非法：口径与算子不匹配
    with pytest.raises(ValidationError, match="TOTAL_POSITIVE"):
        NumericFilter(
            metric_code="NET_CF_OPERATING", operator="CONSECUTIVE_POSITIVE",
            period_rule="LAST_3_FY_TOTAL_POSITIVE", fiscal_years=3,
        )
    # 非法：窗口长度不匹配
    with pytest.raises(ValidationError, match="fiscal_years"):
        NumericFilter(
            metric_code="NET_CF_OPERATING", operator="TOTAL_POSITIVE",
            period_rule="LAST_5_FY", fiscal_years=3,
        )
    # 非法：MIN/MAX 需要 threshold
    with pytest.raises(ValidationError, match="threshold"):
        NumericFilter(
            metric_code="NET_CF_OPERATING", operator="MIN_VALUE",
            period_rule="LAST_3_FY", fiscal_years=3,
        )


def test_explain_relation_fields():
    """object_entity 可选字段 + frozen 计划。"""
    plan = QueryPlan(
        intent=QueryIntent.EXPLAIN_RELATION,
        subject=EntityRef(entity_type="Company", entity_id=uuid.uuid4()),
        object_entity=EntityRef(entity_type="Company", entity_id=uuid.uuid4()),
    )
    assert plan.object_entity is not None
    assert plan.subject is not None
    assert plan.subject.entity_id != plan.object_entity.entity_id


def test_graph_template_whitelist_renders():
    """explain 模板渲染：max_hops 内联 int；占位符版本不在允许集合。"""
    from src.query.compiler import (
        ALLOWED_GRAPH_QUERIES,
        GRAPH_TEMPLATES,
        render_explain_template,
    )

    rendered = render_explain_template(2)
    assert "*1..2" in rendered
    assert rendered in ALLOWED_GRAPH_QUERIES
    # 所有 1..4 变体都在允许集合
    for hops in (1, 2, 3, 4):
        assert render_explain_template(hops) in ALLOWED_GRAPH_QUERIES
    # 原始占位符模板不在允许集合（真实驱动无法解析）
    assert GRAPH_TEMPLATES["explain_relation"] not in ALLOWED_GRAPH_QUERIES
    # 非法 hops 拒绝
    with pytest.raises(ValueError, match="max_hops"):
        render_explain_template(5)
    with pytest.raises(ValueError, match="max_hops"):
        render_explain_template(0)


def test_financial_compiler_whitelist():
    """FinancialCompiler 白名单：非法指标/规则/算子组合抛 ValueError。"""
    from src.query.compiler import FinancialCompiler
    from src.query.models import NumericFilter as NF

    def plan_with(**kwargs):
        return QueryPlan(
            intent=QueryIntent.SEMANTIC_SCREEN,
            numeric_filter=NF(**kwargs),
        )

    good = plan_with(
        metric_code="NET_CF_OPERATING", operator="TOTAL_POSITIVE",
    )
    assert FinancialCompiler.compile(good) is not None

    with pytest.raises(ValueError, match="not in allowed catalog"):
        FinancialCompiler.compile(plan_with(
            metric_code="TOTALLY_FAKE_METRIC", operator="TOTAL_POSITIVE",
        ))
    with pytest.raises(ValueError, match="not supported"):
        FinancialCompiler.compile(plan_with(
            metric_code="NET_CF_OPERATING", operator="TOTAL_POSITIVE",
            period_rule="BOGUS_RULE",
        ))


def test_graph_compiler_validation():
    """EXPLAIN_RELATION/TIMELINE 缺实体 → ValueError（API 层转 422）。"""
    from src.query.compiler import GraphCompiler

    with pytest.raises(ValueError, match="subject and object"):
        GraphCompiler.compile(QueryPlan(
            intent=QueryIntent.EXPLAIN_RELATION,
            subject=EntityRef(entity_type="Company", entity_id=uuid.uuid4()),
        ))
    with pytest.raises(ValueError, match="subject entity_id"):
        GraphCompiler.compile(QueryPlan(intent=QueryIntent.TIMELINE))


def test_reasoning_path_honesty():
    """推理路径只描述实际执行步骤（不伪造 concept 过滤）。"""
    from src.query.compiler import QueryOrchestrator

    path = QueryOrchestrator._reasoning_path(
        QueryPlan(intent=QueryIntent.SEMANTIC_SCREEN,
                  semantic_filter=SemanticFilter(concept_name="人形机器人")),
        candidate_source="pg-unavailable",
        claims=[], evidence_ids=[], fin=None,
    )
    assert not any("concept filter" in step for step in path)
    assert any("PG fallback" in step for step in path)

