"""端口一致性与本体服务单元测试（不依赖 Semantica/DB）。"""

from __future__ import annotations

import pytest
from rdflib import Literal
from rdflib.namespace import RDF
from src.semantic.claim_rdf import STOCK, claim_to_graph
from src.semantic.fake import FakeSemanticRuntime
from src.semantic.ontology_service import (
    OntologyQualityError,
    load_graphs,
    run_quality_gate,
    validate_assets,
)
from src.semantic.ports import (
    ProvenanceInput,
    QueryTemplate,
    SemanticRuntime,
    ValidationReport,
)
from src.semantic.semantica_adapter import SemanticaRuntimeAdapter

SHAPES = "ontology/shapes.ttl"
ASSETS = [
    "ontology/stock-core.ttl",
    "ontology/stock-relations.ttl",
    "ontology/product-skos.ttl",
    "ontology/shapes.ttl",
]


def test_fake_satisfies_port_protocol():
    # runtime_checkable 协议：业务层只依赖端口
    assert isinstance(FakeSemanticRuntime(), SemanticRuntime)


def test_adapter_satisfies_port_protocol():
    adapter = SemanticaRuntimeAdapter(shapes_path=SHAPES, provenance=None)
    assert isinstance(adapter, SemanticRuntime)


def test_port_has_no_destructive_clear():
    """破坏性 Provenance 操作刻意不在端口上：业务路径不可调用。"""
    assert not hasattr(SemanticRuntime, "clear")


# ---- OntologyService ----


def test_assets_pass_quality_gate():
    report = validate_assets()
    assert report.conforms, [v.message for v in report.violations]


def test_syntax_error_raises_structured_quality_error(tmp_path):
    broken = tmp_path / "bad.ttl"
    broken.write_text("@prefix ex: <http://x.com/#> .\nex:a ex:b", encoding="utf-8")
    with pytest.raises(OntologyQualityError) as excinfo:
        load_graphs([str(broken)])
    assert excinfo.value.issues
    assert excinfo.value.issues[0].constraint == "TURTLE_SYNTAX"


def test_missing_domain_range_reported(tmp_path):
    source = tmp_path / "partial.ttl"
    source.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix ex: <http://x.com/#> .\n"
        "ex:hasPart a owl:ObjectProperty .\n",
        encoding="utf-8",
    )
    graph = load_graphs([str(source)])
    report = run_quality_gate(graph)
    assert not report.conforms
    assert {v.constraint for v in report.violations} >= {"MISSING_DOMAIN", "MISSING_RANGE"}


def test_unresolved_target_class_reported(tmp_path):
    source = tmp_path / "shape.ttl"
    source.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix ex: <http://x.com/#> .\n"
        "ex:GhostShape a sh:NodeShape ; sh:targetClass ex:UndefinedClass .\n",
        encoding="utf-8",
    )
    graph = load_graphs([str(source)])
    report = run_quality_gate(graph)
    assert any(v.constraint == "UNRESOLVED_TARGET_CLASS" for v in report.violations)


def test_missing_preflabel_reported(tmp_path):
    source = tmp_path / "skos.ttl"
    source.write_text(
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        "@prefix ex: <http://x.com/#> .\n"
        "ex:NoLabelConcept a skos:Concept .\n",
        encoding="utf-8",
    )
    graph = load_graphs([str(source)])
    report = run_quality_gate(graph)
    assert any(v.constraint == "MISSING_PREFLABEL" for v in report.violations)


# ---- claim_rdf 输出语义 ----


def test_claim_graph_structure():
    graph = claim_to_graph({
        "id": "cc", "subject_entity_id": "co-1", "predicate_code": "PRODUCES",
        "claim_status": "VALIDATED", "confidence": 0.8, "business_stage": "MASS_PRODUCTION",
        "object_value": {"note": "literal-object"},
        "evidence_ids": ["ev-1", "ev-2"],
    })
    assert (None, RDF.type, STOCK.Claim) in graph
    assert (None, STOCK.claimStatus, Literal("VALIDATED")) in graph
    assert (None, STOCK.claimObject, None) in graph  # 值对象也有 IRI 对象
    assert len(list(graph.objects(None, STOCK.supportedBy))) == 2
    assert (None, STOCK.businessStage, Literal("MASS_PRODUCTION")) in graph


# ---- FakeRuntime 行为 ----


def test_fake_scriptable_and_port_shaped():
    fake = FakeSemanticRuntime()
    fake.queue_report(ValidationReport(conforms=False, violations=()))
    assert fake.validate_claim(object()).conforms is False
    record = fake.register_provenance(ProvenanceInput(
        entity_id="e", entity_type="Claim", activity_id="a"
    ))
    assert fake.trace_lineage("e") == [record]
    result = fake.query_graph(
        type("R", (), {"template": QueryTemplate.CLAIMS_FOR_SUBJECT, "params": {}})()
    )
    assert result["count"] == 0


def test_fake_provenance_unavailable_raises():
    fake = FakeSemanticRuntime()
    fake.provenance_configured = False
    from src.semantic.ports import SemanticCapabilityError

    with pytest.raises(SemanticCapabilityError):
        fake.register_provenance(ProvenanceInput(
            entity_id="e", entity_type="Claim", activity_id="a"
        ))
