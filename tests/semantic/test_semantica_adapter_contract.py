"""Semantica 0.7.0 适配器契约测试（ADR-0003 唯一允许导入 semantica 的测试位置）。

锁定真实框架行为：版本、SHACL 场景、冲突、受控查询、双时态往返。
Semantica 升级必须先过这里的契约，再谈版本变更。
"""

from __future__ import annotations

import datetime as dt

import pytest
import semantica
from src.semantic.claim_rdf import claim_to_graph
from src.semantic.ports import (
    ConflictKind,
    GraphQueryRequest,
    ProvenanceInput,
    QueryTemplate,
    SemanticCapabilityError,
    TemporalFact,
)
from src.semantic.semantica_adapter import (
    CYPHER_TEMPLATES,
    SEMANTICA_LOCKED_VERSION,
    SemanticaRuntimeAdapter,
)

SHAPES = "ontology/shapes.ttl"
ASSETS = [
    "ontology/stock-core.ttl",
    "ontology/stock-relations.ttl",
    "ontology/product-skos.ttl",
    "ontology/shapes.ttl",
]
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def make_adapter(**overrides) -> SemanticaRuntimeAdapter:
    kwargs = {"shapes_path": SHAPES, "provenance": None}
    kwargs.update(overrides)
    return SemanticaRuntimeAdapter(**kwargs)


def claim(**overrides) -> dict:
    base = {
        "id": "claim-1", "subject_entity_id": "company-1",
        "predicate_code": "PRODUCES", "claim_status": "VALIDATED",
        "confidence": 0.9, "recorded_at": NOW, "ontology_version": "0.1.0",
        "object_entity_id": "product-1",
    }
    base.update(overrides)
    return base


# ---- 版本契约 ----


def test_semantica_version_locked():
    assert semantica.__version__ == SEMANTICA_LOCKED_VERSION == "0.7.0"


def test_adapter_rejects_version_drift(monkeypatch):
    monkeypatch.setattr(semantica, "__version__", "0.7.1")
    with pytest.raises(SemanticCapabilityError, match="version drift"):
        make_adapter()


# ---- validate_ontology ----


def test_validate_ontology_assets_pass():
    adapter = make_adapter()
    report = adapter.validate_ontology(ASSETS)
    assert report.conforms, [v.message for v in report.violations]


def test_validate_ontology_syntax_failure(tmp_path):
    broken = tmp_path / "broken.ttl"
    broken.write_text("this is << not turtle", encoding="utf-8")
    report = make_adapter().validate_ontology([str(broken)])
    assert not report.conforms


def test_validate_ontology_quality_gate_failure(tmp_path):
    # 语法正确但缺 domain/range：质量门禁必须失败，不得静默通过
    source = tmp_path / "incomplete.ttl"
    source.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@prefix ex: <http://example.com/#> .\n"
        "ex:hasPart a owl:ObjectProperty .\n",
        encoding="utf-8",
    )
    report = make_adapter().validate_ontology([str(source)])
    assert not report.conforms
    assert any(v.constraint == "MISSING_DOMAIN" for v in report.violations)


# ---- validate_claim：SHACL 场景矩阵（issue #5 验收） ----


def test_valid_claim_conforms():
    report = make_adapter().validate_claim(claim_to_graph(claim()))
    assert report.conforms, [v.message for v in report.violations]


def test_subject_object_type_enforced():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(object_entity_id="theme-1", object_entity_type="Theme"))
    )
    assert not report.conforms
    assert any("对象必须是Product" in (v.message or "") for v in report.violations)


def test_predicate_enum_enforced():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(predicate_code="FABRICATED_PREDICATE"))
    )
    assert not report.conforms
    assert any("白名单" in (v.message or "") for v in report.violations)


def test_business_stage_enum_enforced():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(business_stage="GOD_MODE_STAGE"))
    )
    assert not report.conforms
    assert any("业务阶段" in (v.message or "") for v in report.violations)


def test_time_interval_must_be_ordered():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(
            valid_from=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
            valid_to=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        ))
    )
    assert not report.conforms
    assert any("validTo必须晚于validFrom" in (v.message or "") for v in report.violations)


def test_superseded_after_recorded():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(
            superseded_at=dt.datetime(2025, 12, 31, tzinfo=dt.UTC)
        ))
    )
    assert not report.conforms
    assert any("supersededAt" in (v.message or "") for v in report.violations)


def test_accepted_claim_requires_evidence():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(claim_status="ACCEPTED"))
    )
    assert not report.conforms
    assert any("证据" in (v.message or "") for v in report.violations)


def test_accepted_with_structured_source_is_allowed():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(claim_status="ACCEPTED",
                             evidence_state="STRUCTURED_SOURCE"))
    )
    assert report.conforms, [v.message for v in report.violations]


def test_platform_label_cannot_masquerade_as_mass_production():
    report = make_adapter().validate_claim(
        claim_to_graph(claim(
            claim_status="ACCEPTED", business_stage="MASS_PRODUCTION",
            evidence_state="PLATFORM_CLASSIFICATION_ONLY",
            evidence_ids=["ev-1"],  # 即使有片段证据，平台标签状态仍是冒充
        ))
    )
    assert not report.conforms
    assert any("量产" in (v.message or "") for v in report.violations)


def test_confidence_range_enforced():
    report = make_adapter().validate_claim(claim_to_graph(claim(confidence=2.0)))
    assert not report.conforms


# ---- 冲突检测 ----


def test_contradictory_stages_with_overlap_conflict():
    adapter = make_adapter()
    claims = [
        {"id": "c-a", "subject_entity_id": "company-1", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "business_stage": "MASS_PRODUCTION",
         "valid_from": "2024-01-01", "valid_to": "2026-12-31"},
        {"id": "c-b", "subject_entity_id": "company-1", "predicate_code": "PRODUCES",
         "claim_status": "NEEDS_REVIEW", "evidence_state": "COMPANY_DENIAL",
         "valid_from": "2025-01-01", "valid_to": "2026-12-31"},
    ]
    findings = adapter.detect_conflicts(claims)
    overlap = [f for f in findings if f.kind == ConflictKind.TEMPORAL_OVERLAP]
    assert overlap, "DENIED 与 MASS_PRODUCTION 重叠有效期必须生成冲突"
    assert set(overlap[0].claim_ids) == {"c-a", "c-b"}
    assert overlap[0].auto_resolved is False  # 检测层绝不自动裁决


def test_no_conflict_without_overlap():
    adapter = make_adapter()
    claims = [
        {"id": "c-a", "subject_entity_id": "company-1", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "business_stage": "MASS_PRODUCTION",
         "valid_from": "2020-01-01", "valid_to": "2021-12-31"},
        {"id": "c-b", "subject_entity_id": "company-1", "predicate_code": "PRODUCES",
         "claim_status": "NEEDS_REVIEW", "evidence_state": "COMPANY_DENIAL",
         "valid_from": "2024-01-01", "valid_to": "2025-12-31"},
    ]
    overlap = [f for f in adapter.detect_conflicts(claims)
               if f.kind == ConflictKind.TEMPORAL_OVERLAP]
    assert not overlap


# ---- Provenance 能力边界 ----


def test_provenance_without_storage_is_capability_error():
    with pytest.raises(SemanticCapabilityError, match="InMemoryProvenance"):
        make_adapter().register_provenance(ProvenanceInput(
            entity_id="e1", entity_type="Claim", activity_id="a1"
        ))
    with pytest.raises(SemanticCapabilityError):
        make_adapter().trace_lineage("e1")


# ---- 受控图查询 ----


def test_query_without_backend_is_capability_error():
    with pytest.raises(SemanticCapabilityError, match="graph backend not configured"):
        make_adapter().query_graph(GraphQueryRequest(
            template=QueryTemplate.COMPANIES_BY_CONCEPT, params={"concept": "x"}
        ))


def test_free_form_cypher_rejected():
    adapter = make_adapter(graph_query_executor=lambda cypher, params: [])
    bogus = GraphQueryRequest(template="FREE_FORM_MATCH_ALL", params={})
    with pytest.raises(SemanticCapabilityError, match="whitelisted"):
        adapter.query_graph(bogus)


def test_whitelisted_template_with_executor():
    captured = {}

    def executor(cypher, params):
        captured["cypher"] = cypher
        captured["params"] = params
        return [{"company": "000001.SZ"}]

    adapter = make_adapter(graph_query_executor=executor)
    result = adapter.query_graph(GraphQueryRequest(
        template=QueryTemplate.COMPANIES_BY_CONCEPT, params={"concept": "robot"}
    ))
    assert result["rows"] == [{"company": "000001.SZ"}]
    assert captured["cypher"] == CYPHER_TEMPLATES[QueryTemplate.COMPANIES_BY_CONCEPT]
    assert captured["params"] == {"concept": "robot"}


def test_backend_failure_is_classified_error():
    def exploding(cypher, params):
        raise ConnectionError("neo4j down")

    adapter = make_adapter(graph_query_executor=exploding)
    with pytest.raises(SemanticCapabilityError, match="graph backend failure"):
        adapter.query_graph(GraphQueryRequest(
            template=QueryTemplate.COMPANIES_BY_CONCEPT, params={}
        ))


# ---- 双时态往返（TemporalFactMapper 契约） ----


@pytest.mark.parametrize(
    "scenario",
    ["open_end", "boundary_equal", "superseded"],
)
def test_bitemporal_roundtrip_preserves_semantics(scenario):
    adapter = make_adapter()
    if scenario == "open_end":
        fact = TemporalFact(
            subject="s", predicate="p",
            valid_from=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            valid_to=None,  # 开放结束
            recorded_at=dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
        )
    elif scenario == "boundary_equal":
        fact = TemporalFact(
            subject="s", predicate="p",
            valid_from=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            valid_to=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            recorded_at=dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
        )
    else:
        fact = TemporalFact(
            subject="s", predicate="p",
            valid_from=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            valid_to=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            recorded_at=dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
            superseded_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        )
    bifact = adapter.fact_to_bitemporal(fact)
    restored = adapter.bitemporal_to_fact(bifact, subject="s", predicate="p")
    assert restored == fact  # 往返后 valid_from/to、recorded_at、superseded_at 语义不变


# ---- 四类冲突契约（BLOCKER 修复回归） ----


def test_value_conflict_detected_with_all_source_claims():
    """同 (subject, predicate) 不同对象 → Semantica 值冲突，claim_ids 保留全部来源。"""
    adapter = make_adapter()
    findings = adapter.detect_conflicts([
        {"id": "v1", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "object_entity_id": "product-A"},
        {"id": "v2", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "object_entity_id": "product-B"},
    ])
    value_findings = [f for f in findings if f.kind == ConflictKind.VALUE]
    assert value_findings, "值冲突必须被 Semantica 检测器真实发现"
    assert set(value_findings[0].claim_ids) == {"v1", "v2"}
    assert value_findings[0].auto_resolved is False
    assert "product-A" in value_findings[0].detail


def test_type_conflict_detected():
    """同 (subject, predicate) 对象实体类型不一致 → TYPE 冲突。"""
    adapter = make_adapter()
    findings = adapter.detect_conflicts([
        {"id": "t1", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "object_entity_id": "p-1", "object_entity_type": "Product"},
        {"id": "t2", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "object_entity_id": "th-1", "object_entity_type": "Theme"},
    ])
    type_findings = [f for f in findings if f.kind == ConflictKind.TYPE]
    assert type_findings
    assert set(type_findings[0].claim_ids) == {"t1", "t2"}


def test_relation_conflict_detected():
    """同 (subject, object) 被断言多种关系 → RELATION 冲突。"""
    adapter = make_adapter()
    findings = adapter.detect_conflicts([
        {"id": "r1", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "object_entity_id": "p-1"},
        {"id": "r2", "subject_entity_id": "co-1", "predicate_code": "SUPPLIES_TO",
         "object_entity_id": "p-1"},
    ])
    relation_findings = [f for f in findings if f.kind == ConflictKind.RELATION]
    assert relation_findings
    assert set(relation_findings[0].claim_ids) == {"r1", "r2"}


def test_logical_conflict_denial_vs_affirmation():
    """明确否认与肯定断言并存 → LOGICAL 冲突，绝不自动选择一方。"""
    adapter = make_adapter()
    findings = adapter.detect_conflicts([
        {"id": "l1", "subject_entity_id": "co-1", "predicate_code": "DENIES_INVOLVEMENT",
         "claim_status": "ACCEPTED"},
        {"id": "l2", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "business_stage": "MASS_PRODUCTION"},
    ])
    logical = [f for f in findings if f.kind == ConflictKind.LOGICAL]
    assert logical
    assert {"l1", "l2"} <= set(logical[0].claim_ids)
    assert logical[0].auto_resolved is False


def test_no_value_conflict_when_objects_agree():
    adapter = make_adapter()
    findings = adapter.detect_conflicts([
        {"id": "a1", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "object_entity_id": "p-1"},
        {"id": "a2", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
         "object_entity_id": "p-1"},
    ])
    assert not [f for f in findings if f.kind == ConflictKind.VALUE]


# ---- CONCERN 修复回归 ----


def test_value_object_claim_conforms_for_revenue_predicate():
    """object_value 型 Claim（如 HAS_REVENUE_FROM）值节点类型化为 Product，
    满足 ProducesObjectShape。"""
    report = make_adapter().validate_claim(claim_to_graph(claim(
        predicate_code="HAS_REVENUE_FROM", object_entity_id=None,
        object_value={"amount": 100000},
    )))
    assert report.conforms, [v.message for v in report.violations]


def test_unknown_entity_type_is_rejected_loudly():
    from src.semantic.claim_rdf import UnknownEntityTypeError

    with pytest.raises(UnknownEntityTypeError):
        claim_to_graph(claim(object_entity_id="x-1", object_entity_type="GhostType"))
