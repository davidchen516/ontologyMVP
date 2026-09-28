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
