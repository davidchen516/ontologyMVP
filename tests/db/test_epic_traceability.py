"""Epic 端到端追溯测试（issue #12 验收 5）。

证明一条链路：PostgreSQL 事实（fact.claim + fact.evidence_fragment）
→ Neo4j 投影（Claim 节点 + 带 claim_id 的经营边）
→ API 响应（GroundedResult.claim_ids / evidence_quotes）
→ 回链 PostgreSQL（evidence_quotes 的 fragment 在事实库可定位）。

任一环断裂该测试失败——这是"PostgreSQL 事实、Neo4j 投影、API 响应和
Evidence 之间可以端到端追溯"的自动化证据。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

_NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "")
_NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "")


@pytest.fixture()
def trace_neo4j():
    """Epic 追溯测试专用真实 Neo4j（CI service；本地无则跳过）。"""
    if not (_NEO4J_URI and _NEO4J_PASSWORD):
        pytest.skip("NEO4J_TEST_URI/NEO4J_TEST_PASSWORD not set; CI-only")
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        _NEO4J_URI, auth=("neo4j", _NEO4J_PASSWORD), connection_timeout=10,
    )
    driver.verify_connectivity()
    yield driver
    driver.close()


def _seed_traceable_fact(uow_factory) -> dict[str, Any]:
    """种子：公司 → 文档/版本/证据片段 → ACCEPTED PRODUCES Claim。"""
    from src.domain.enums import ClaimStatus

    with uow_factory.transaction() as uow:
        company = uow.companies.insert(
            canonical_name="追溯测试公司",
            unified_social_credit_code=f"USCC-TRACE-{uuid.uuid4().hex[:10]}",
        )
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="TRACE_SRC",
            title="追溯年报", content_hash=uuid.uuid4().hex * 2,
        )
        version = uow.document_versions.create_version(
            document_id=document["id"],
            source_url="file://trace/report.pdf",
            content_hash=uuid.uuid4().hex,
        )
        evidence = uow.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], page_number=3,
            quote_text="谐波减速器已实现量产，报告期内批量生产并交付客户。",
            checksum=uuid.uuid4().hex,
        )
        uow._conn.execute(  # noqa: SLF001
            "UPDATE fact.evidence_fragment SET document_version_id = %s, "
            "char_start = 0, char_end = 30 WHERE id = %s",
            (version["id"], evidence["id"]),
        )
        product_id = uow._conn.execute(  # noqa: SLF001
            "INSERT INTO master.product (iri, canonical_name, ontology_version) "
            "VALUES (%s, '谐波减速器', '0.1.0') "
            "ON CONFLICT (iri) DO UPDATE SET canonical_name = EXCLUDED.canonical_name "
            "RETURNING id",
            (f"https://ontology.example.com/product#TRACE_{uuid.uuid4().hex[:10]}",),
        ).fetchone()[0]
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="rules", ontology_version="0.1.0",
            confidence=0.85, status=ClaimStatus.ACCEPTED,
            object_entity_type="Product", object_entity_id=product_id,
            object_value={"stage": "MASS_PRODUCTION"},
            business_stage="MASS_PRODUCTION",
        )
        uow.claim_evidence.link(
            claim_id=claim["id"], evidence_id=evidence["id"],
            support_type="SUPPORTS",
        )
        return {"company": company, "claim": claim, "evidence": evidence,
                "version": version}


def test_epic_end_to_end_traceability(
    uow_factory, main_dsn, trace_neo4j,
) -> None:
    """验收 5：PG 事实 ↔ Neo4j 投影 ↔ API 响应 ↔ Evidence 全链路追溯。"""
    from src.projection.dispatcher import EventDispatcher
    from src.projection.projector import Neo4jProjector

    seeded = _seed_traceable_fact(uow_factory)
    company_id = str(seeded["company"]["id"])
    claim_id = str(seeded["claim"]["id"])
    evidence_id = str(seeded["evidence"]["id"])

    # ---- 第 1 环：PG 事实存在且可定位 ----
    with psycopg.connect(main_dsn) as conn:
        fact_row = conn.execute(
            "SELECT c.claim_status, f.quote_text, f.page_number, "
            "d.source_system FROM fact.claim c "
            "JOIN fact.claim_evidence ce ON ce.claim_id = c.id "
            "JOIN fact.evidence_fragment f ON f.id = ce.evidence_id "
            "JOIN fact.document d ON d.id = f.document_id "
            "WHERE c.id = %s",
            (seeded["claim"]["id"],),
        ).fetchone()
    assert fact_row is not None, "fact layer missing"
    assert fact_row[0] == "ACCEPTED"
    assert "谐波减速器" in fact_row[1]
    assert fact_row[2] == 3  # 可定位页码

    # ---- 第 2 环：Neo4j 投影（Claim 节点 + 带 claim_id 的边）----
    class _DriverExecutor:
        def __init__(self, driver: Any) -> None:
            self._driver = driver

        def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            with self._driver.session() as session:
                return [dict(r) for r in session.run(template, params)]

    executor = _DriverExecutor(trace_neo4j)
    projector = Neo4jProjector(executor)
    projector.project_entity("Company", company_id,
                             {"canonical_name": "追溯测试公司"})
    with uow_factory.transaction() as uow:
        product_id = uow._conn.execute(  # noqa: SLF001
            "SELECT object_entity_id FROM fact.claim WHERE id = %s",
            (seeded["claim"]["id"],),
        ).fetchone()[0]
    projector.project_entity("Product", str(product_id), {"name": "谐波减速器"})
    with uow_factory.transaction() as uow:
        EventDispatcher(projector).dispatch(uow, {
            "event_type": "CLAIM_ACCEPTED",
            "aggregate_type": "Claim",
            "aggregate_id": seeded["claim"]["id"],
            "payload": {},
        })
    node = executor.execute(
        "MATCH (n:Claim {id: $id}) RETURN n.claim_status AS status",
        {"id": claim_id},
    )
    assert node and node[0]["status"] == "ACCEPTED", "Claim node not projected"

    # ---- 第 3 环：API 响应回链 Claim 与 Evidence ----
    from apps.api.app import create_app
    from psycopg.conninfo import conninfo_to_dict

    from tests.helpers import make_settings

    params = conninfo_to_dict(main_dsn)
    app = create_app(make_settings(
        postgres_host=params["host"],
        postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"],
        postgres_user=params["user"],
        postgres_password=params["password"],
    ))
    executor_attr = getattr(app.state, "query_graph_executor", None)
    if executor_attr is not None:
        executor_attr.close()
        app.state.query_graph_executor = None  # 确定性：PG 候选路径
    client = TestClient(app)
    body = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "semantic_filter": {"business_stage": "MASS_PRODUCTION"},
            "evidence_required": True,
            "max_results": 10,
        }
    }).json()
    matched = next(
        (r for r in body["results"] if r["company_name"] == "追溯测试公司"),
        None,
    )
    assert matched is not None, "company missing from API results"
    assert claim_id in [str(c) for c in matched["claim_ids"]], (
        "API response claim_ids do not trace back to fact.claim"
    )
    quote_match = next(
        (q for q in matched["evidence_quotes"] if q["evidence_id"] == evidence_id),
        None,
    )
    assert quote_match is not None, "evidence_quotes do not trace back"
    assert quote_match["page_number"] == 3

    # ---- 第 4 环：Neo4j 边携带 claim_id 且可回 PG ----
    edge = executor.execute(
        "MATCH (:Company {id: $s})-[r:PRODUCES {claim_id: $cid}]->() "
        "RETURN r.claim_id AS cid LIMIT 1",
        {"s": company_id, "cid": claim_id},
    )
    assert edge and edge[0]["cid"] == claim_id, (
        "business edge missing claim_id or not traceable"
    )
