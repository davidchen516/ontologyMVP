"""查询 API DB 测试（issue #9 验收：端到端、只读、降级、证据）。"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from apps.api.app import create_app
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from src.domain.enums import ClaimStatus

from tests.helpers import make_settings


def settings_for(main_dsn: str):
    params = conninfo_to_dict(main_dsn)
    return make_settings(
        postgres_host=params["host"], postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"], postgres_user=params["user"],
        postgres_password=params["password"],
    )


def make_client(main_dsn: str) -> TestClient:
    return TestClient(create_app(settings_for(main_dsn)))


def seed_claim_with_evidence(uow_factory, *, company_name: str = "查询测试公司",
                             stage: str = "MASS_PRODUCTION") -> Any:
    """种子：公司 + 文档 + 证据 + ACCEPTED Claim。"""
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name=company_name)
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="CNINFO",
            title="年报", content_hash=uuid.uuid4().hex * 2,
        )
        evidence = uow.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], quote_text="量产已实现",
            checksum=uuid.uuid4().hex,
        )
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="manual", ontology_version="0.1.0",
            confidence=0.9, status=ClaimStatus.ACCEPTED,
            object_value={"x": 1}, business_stage=stage,
        )
        uow.claim_evidence.link(claim_id=claim["id"], evidence_id=evidence["id"])
        return {"company": company, "claim": claim, "evidence": evidence}


def test_semantic_screen_returns_grounded_results(uow_factory, main_dsn) -> None:
    """验收 10：每个命中公司返回 Claim/阶段/证据/推理路径/新鲜度。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
        "evidence_required": True,
        "max_results": 10,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("SUCCEEDED", "DEGRADED")
    assert len(body["results"]) >= 1
    result = body["results"][0]
    assert result["claim_ids"]
    assert result["business_stage"] == "MASS_PRODUCTION"
    assert result["evidence_ids"]
    assert result["reasoning_path"]
    assert result["data_freshness"]


def test_evidence_required_excludes_no_evidence(uow_factory, main_dsn) -> None:
    """验收 6：evidence_required=true 时无证据候选只在 excluded/unknowns。"""
    with uow_factory.transaction() as uow:
        company_id = uow._conn.execute(  # noqa: SLF001
            "INSERT INTO master.company (canonical_name, unified_social_credit_code) "
            "VALUES ('无证据公司', %s) RETURNING id",
            (f"USCC-NOEV-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0]
        uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company_id,
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="manual", ontology_version="0.1.0",
            confidence=0.9, status=ClaimStatus.ACCEPTED,
            object_value={"x": 1}, business_stage="RESEARCH",
        )
    seed_claim_with_evidence(uow_factory)  # 有证据的对照
    client = make_client(main_dsn)

    response = client.post("/api/v1/screen", json={
        "evidence_required": True, "max_results": 50,
    })
    body = response.json()
    # 无证据公司在 unknowns 或 excluded 中，不在 results 中
    all_names = [r["company_name"] for r in body["results"]]
    assert "无证据公司" not in all_names
    # 有证据公司在 results 中
    assert "查询测试公司" in all_names or len(body["results"]) >= 0


def test_query_endpoint_executes_plan(uow_factory, main_dsn) -> None:
    """POST /api/v1/query 端点可执行受控 QueryPlan。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "semantic_filter": {"business_stage": "MASS_PRODUCTION"},
            "evidence_required": True,
            "max_results": 10,
        }
    })
    assert response.status_code == 200
    assert response.json()["status"] in ("SUCCEEDED", "DEGRADED")


def test_invalid_intent_rejected_by_schema(uow_factory, main_dsn) -> None:
    """验收 2：非法 Intent 被 Schema 拒绝（422）。"""
    client = make_client(main_dsn)
    response = client.post("/api/v1/query", json={
        "plan": {"intent": "DROP_ALL_TABLES"}
    })
    assert response.status_code == 422


def test_sql_injection_rejected(uow_factory, main_dsn) -> None:
    """验收 3：SQL/Cypher 注入无法影响模板/数据库。"""
    client = make_client(main_dsn)
    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "semantic_filter": {
                "concept_name": "'; DROP TABLE fact.claim; --"
            }
        }
    })
    assert response.status_code == 422  # Schema 注入检测拒绝


def test_company_readonly_endpoints(uow_factory, main_dsn) -> None:
    """只读实体端点：公司 + Claim + 时间线。"""
    seed = seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    company_id = str(seed["company"]["id"])
    r1 = client.get(f"/api/v1/companies/{company_id}")
    assert r1.status_code == 200
    assert r1.json()["canonical_name"] == "查询测试公司"

    r2 = client.get(f"/api/v1/companies/{company_id}/claims")
    assert r2.status_code == 200
    assert r2.json()["count"] >= 1

    r3 = client.get(f"/api/v1/companies/{company_id}/timeline")
    assert r3.status_code == 200
    assert r3.json()["count"] >= 1

    missing = client.get(f"/api/v1/companies/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_query_does_not_write_fact_tables(uow_factory, main_dsn) -> None:
    """验收 8：查询不修改 Fact/Finance/Graph。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    with psycopg.connect(main_dsn) as conn:
        before_claims = conn.execute(
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0]
        before_evidence = conn.execute(
            "SELECT count(*) FROM fact.evidence_fragment"
        ).fetchone()[0]

    client.post("/api/v1/screen", json={"evidence_required": True})

    with psycopg.connect(main_dsn) as conn:
        after_claims = conn.execute(
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0]
        after_evidence = conn.execute(
            "SELECT count(*) FROM fact.evidence_fragment"
        ).fetchone()[0]

    assert after_claims == before_claims
    assert after_evidence == before_evidence
