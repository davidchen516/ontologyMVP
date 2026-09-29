"""查询 API DB 测试（issue #9 验收：端到端、只读、降级、证据、财务、审计）。

覆盖独立审查确认的缺陷回归：
- BLOCKER 1：财务路径不再崩溃（公司有证券映射时正常返回）；
- BLOCKER 2：非法指标被白名单拒绝（422），FinancialCompiler 真正接线；
- BLOCKER 3：降级路径对 concept 过滤诚实返回空+说明，不伪造过滤结果；
- BLOCKER 4：「三年合计为正」与「连续三年均为正」不同 PeriodRule、不同结果。
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
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


class StubGraphExecutor:
    """确定性测试执行器：返回预设结果或抛错。"""

    def __init__(self, results: list[dict] | None = None,
                 error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.executed: list[tuple[str, dict]] = []

    def execute(self, template: str, params: dict[str, Any]) -> list[dict]:
        self.executed.append((template, params))
        if self.error:
            raise self.error
        return self.results


def seed_claim_with_evidence(uow_factory, *, company_name: str = "查询测试公司",
                             stage: str = "MASS_PRODUCTION") -> Any:
    """种子：公司 + 文档 + 证据 + ACCEPTED Claim。"""
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(
            canonical_name=company_name,
            unified_social_credit_code=f"USCC-{uuid.uuid4().hex[:12]}",
        )
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


def seed_financial_company(
    uow_factory, *, company_name: str, stage: str = "MASS_PRODUCTION",
    fy_values: list[tuple[int, float]], announced_at: str = "2025-04-30",
) -> dict:
    """种子：公司+证券映射+证据 Claim+若干完整财年经营现金流观察值。"""
    with uow_factory.transaction() as uow:
        conn = uow._conn  # noqa: SLF001
        company = uow.companies.insert(
            canonical_name=company_name,
            unified_social_credit_code=f"USCC-{uuid.uuid4().hex[:12]}",
        )
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="CNINFO",
            title="年报", content_hash=uuid.uuid4().hex * 2,
        )
        evidence = uow.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], quote_text="经营现金流口径",
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

        run = conn.execute(
            "INSERT INTO ops.ingest_run (dataset_name, source_system, status, "
            "trace_id) VALUES ('query-test', 'QUERYTEST', 'SUCCEEDED', %s) "
            "RETURNING id",
            (uuid.uuid4().hex,),
        ).fetchone()[0]
        # 指标目录（financial_metric_catalog 语义）：白名单指标先注册
        conn.execute(
            "INSERT INTO finance.financial_metric "
            "(metric_code, name, statement_type, unit_type, "
            "default_aggregation) VALUES "
            "('NET_CF_OPERATING', '经营活动现金流净额', 'CASHFLOW', 'CNY', 'SUM') "
            "ON CONFLICT (metric_code) DO NOTHING"
        )
        src = conn.execute(
            "INSERT INTO raw.source_record (source_system, api_name, "
            "payload_hash, raw_payload, ingest_run_id) "
            "VALUES ('QUERYTEST', 'cashflow', %s, '{}', %s) RETURNING id",
            (uuid.uuid4().hex + uuid.uuid4().hex, run),
        ).fetchone()[0]
        exchange = conn.execute(
            "INSERT INTO master.exchange (code, name) VALUES (%s, 'T') "
            "RETURNING id",
            (f"QEX-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0]
        security = conn.execute(
            "INSERT INTO master.security (ts_code, symbol, name, exchange_id, "
            "status) VALUES (%s, '000001', '测试证券', %s, 'ACTIVE') "
            "RETURNING id",
            (f"{uuid.uuid4().hex[:10]}.SZ", exchange),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO master.company_security (company_id, security_id, "
            "recorded_at, source_record_id) VALUES (%s, %s, now(), %s)",
            (company["id"], security, src),
        )
        for year, value in fy_values:
            conn.execute(
                "INSERT INTO finance.financial_observation "
                "(security_id, metric_code, period_end, report_type, value, "
                "currency, announced_at, source_record_id) "
                "VALUES (%s, 'NET_CF_OPERATING', %s, '1', %s, 'CNY', %s, %s)",
                (security, f"{year}-12-31", value, announced_at, src),
            )
        return {"company": company, "claim": claim, "evidence": evidence,
                "security": security}


# ---- 基础端到端 ----


def test_semantic_screen_returns_grounded_results(uow_factory, main_dsn) -> None:
    """验收 10：每个命中公司返回 Claim/阶段/证据/推理路径/新鲜度。"""
    seeded = seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None  # 确定性：PG 降级路径

    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
        "evidence_required": True,
        "max_results": 10,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"  # 图不可用 → 明确降级，非 SUCCEEDED 伪装
    assert body["degraded"] is True
    assert any("graph" in note for note in body["degradation_notes"])
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["company_id"] == str(seeded["company"]["id"])
    assert result["claim_ids"]
    assert result["business_stage"] == "MASS_PRODUCTION"
    assert result["evidence_ids"]
    assert result["reasoning_path"]
    assert result["data_freshness"].startswith("pg-only")


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
    client.app.state.query_graph_executor = None

    response = client.post("/api/v1/screen", json={
        "evidence_required": True, "max_results": 50,
    })
    body = response.json()
    all_names = [r["company_name"] for r in body["results"]]
    assert "无证据公司" not in all_names
    assert "查询测试公司" in all_names
    assert any("无证据公司" in item for item in body["unknowns"])


def test_query_endpoint_executes_plan(uow_factory, main_dsn) -> None:
    """POST /api/v1/query 端点可执行受控 QueryPlan（走财务路径）。"""
    seed_financial_company(
        uow_factory, company_name="计划执行公司",
        fy_values=[(2024, 100.0), (2023, 200.0), (2022, 300.0)],
    )
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "semantic_filter": {"business_stage": "MASS_PRODUCTION"},
            "numeric_filter": {
                "metric_code": "NET_CF_OPERATING",
                "operator": "TOTAL_POSITIVE",
                "period_rule": "LAST_3_FY",
                "fiscal_years": 3,
            },
            "evidence_required": True,
            "max_results": 10,
        }
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert len(body["results"]) == 1
    assert body["results"][0]["financial_value"] == 600.0  # 100+200+300
    assert body["period_rule"] == "LAST_3_FY"


# ---- BLOCKER 1/4 回归：财务路径 + PeriodRule 语义 ----


def test_financial_filter_period_rule_semantics(uow_factory, main_dsn) -> None:
    """验收 5：「三年合计为正」与「连续三年均为正」不同 PeriodRule、不同结果。"""
    # A：三年均为正 → 两种口径都通过
    seed_financial_company(
        uow_factory, company_name="连续为正公司",
        fy_values=[(2024, 100.0), (2023, 200.0), (2022, 300.0)],
    )
    # B：一年为负 → 合计为正通过，连续为正不通过
    seed_financial_company(
        uow_factory, company_name="一年为负公司",
        fy_values=[(2024, 100.0), (2023, -50.0), (2022, 200.0)],
    )
    # C：不足 3 个完整 FY → 数据不足，两种口径都排除（不把缺失当 0）
    seed_financial_company(
        uow_factory, company_name="数据不足公司",
        fy_values=[(2024, 100.0), (2023, 200.0)],
    )
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    total = client.post("/api/v1/screen", json={
        "metric_code": "NET_CF_OPERATING",
        "operator": "TOTAL_POSITIVE",
    }).json()
    consecutive = client.post("/api/v1/screen", json={
        "metric_code": "NET_CF_OPERATING",
        "operator": "CONSECUTIVE_POSITIVE",
    }).json()

    # 不同 PeriodRule 口径随响应返回
    assert total["period_rule"] == "LAST_3_FY_TOTAL_POSITIVE"
    assert consecutive["period_rule"] == "CONSECUTIVE_3_FY_POSITIVE"

    total_names = {r["company_name"] for r in total["results"]}
    consecutive_names = {r["company_name"] for r in consecutive["results"]}
    assert "连续为正公司" in total_names
    assert "一年为负公司" in total_names  # 合计 250 > 0
    assert "连续为正公司" in consecutive_names
    assert "一年为负公司" not in consecutive_names  # -50 打破连续性
    assert "数据不足公司" not in total_names
    assert "数据不足公司" not in consecutive_names
    assert any("数据不足公司" in e for e in total["excluded"])
    assert "insufficient_complete_fiscal_years" in str(total["excluded"])

    # 合计口径的值与报告期/明细
    a_total = next(r for r in total["results"]
                   if r["company_name"] == "连续为正公司")
    assert a_total["financial_value"] == 600.0
    assert a_total["currency"] == "CNY"
    assert a_total["report_period"] == "2022-12-31..2024-12-31"
    assert len(a_total["financial_detail"]) == 3
    # 连续口径的值 = 窗口内最弱年份（判定口径）
    a_consec = next(r for r in consecutive["results"]
                    if r["company_name"] == "连续为正公司")
    assert a_consec["financial_value"] == 100.0
    assert any("financial filter applied" in step
               for step in a_consec["reasoning_path"])


def test_financial_path_no_crash_with_security_mapping(uow_factory, main_dsn) -> None:
    """BLOCKER 1 回归：公司有 company_security 映射时财务路径不崩溃。

    纯财务筛选（无语义过滤）不需要图 → SUCCEEDED 而非降级。
    """
    seed_financial_company(
        uow_factory, company_name="证券映射公司",
        fy_values=[(2024, 10.0), (2023, 20.0), (2022, 30.0)],
    )
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    response = client.post("/api/v1/screen", json={
        "metric_code": "NET_CF_OPERATING", "operator": "TOTAL_POSITIVE",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
    assert response.json()["results"]


# ---- BLOCKER 2 回归：白名单真正接线 ----


def test_fake_metric_rejected_422(uow_factory, main_dsn) -> None:
    """验收 2：非法指标被白名单拒绝（422），不静默返回 200。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    r1 = client.post("/api/v1/screen", json={
        "metric_code": "TOTALLY_FAKE_METRIC", "operator": "TOTAL_POSITIVE",
    })
    assert r1.status_code == 422
    assert "TOTALLY_FAKE_METRIC" in r1.json()["detail"]

    r2 = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "numeric_filter": {
                "metric_code": "TOTALLY_FAKE_METRIC",
                "operator": "TOTAL_POSITIVE",
            },
        }
    })
    assert r2.status_code == 422

    r3 = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "numeric_filter": {
                "metric_code": "NET_CF_OPERATING",
                "operator": "TOTAL_POSITIVE",
                "period_rule": "BOGUS_RULE",
            },
        }
    })
    assert r3.status_code == 422
    assert "BOGUS_RULE" in r3.json()["detail"]


def test_invalid_intent_rejected_by_schema(uow_factory, main_dsn) -> None:
    """验收 2：非法 Intent 被 Schema 拒绝（422）。"""
    client = make_client(main_dsn)
    response = client.post("/api/v1/query", json={
        "plan": {"intent": "DROP_ALL_TABLES"}
    })
    assert response.status_code == 422


# ---- BLOCKER 3 回归：降级路径诚实 ----


def test_concept_filter_degraded_returns_no_results(uow_factory, main_dsn) -> None:
    """验收 3/降级：concept 在 PG 降级路径不可评估 → 诚实空结果+说明。

    未识别/不可评估实体绝不猜测执行，绝不把未过滤结果伪装成已过滤。
    """
    seed_claim_with_evidence(uow_factory)
    seed_claim_with_evidence(uow_factory, company_name="另一家公司",
                             stage="RESEARCH")
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None  # 无图 → 降级

    response = client.post("/api/v1/screen", json={
        "concept_name": "根本不存在的概念XYZ",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert body["results"] == []  # 不返回未过滤公司
    assert any("not evaluable" in note
               for note in body["degradation_notes"])
    # 推理路径不伪造 concept 过滤
    for note in body["degradation_notes"]:
        assert "concept filter 根本不存在" not in note


def test_graph_stale_degrades_explicitly(uow_factory, main_dsn) -> None:
    """图可查询但投影滞后（图空/PG 有候选）→ 明确 stale 降级。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(results=[])

    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
    })
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert body["degraded"] is True
    assert any("stale" in note for note in body["degradation_notes"])
    names = {r["company_name"] for r in body["results"]}
    assert names == {"查询测试公司"}
    assert body["results"][0]["data_freshness"] == "pg-only (graph stale)"


def test_graph_executor_success_path(uow_factory, main_dsn) -> None:
    """图执行器可用 → SUCCEEDED + 图候选 + graph+pg 新鲜度（生产接线验证）。"""
    seeded = seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(results=[{
        "company_id": str(seeded["company"]["id"]),
        "company_name": "查询测试公司",
        "claim_id": str(seeded["claim"]["id"]),
        "stage": "MASS_PRODUCTION",
    }])

    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
    })
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert not body["degraded"]
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["data_freshness"] == "graph+pg"
    assert any("graph stage filter applied" in step
               for step in result["reasoning_path"])
    # 执行器收到的是白名单参数化模板
    executor: StubGraphExecutor = client.app.state.query_graph_executor
    template, params = executor.executed[0]
    assert "$business_stage" in template
    assert params == {"business_stage": "MASS_PRODUCTION"}


def test_graph_executor_unreachable_degrades(uow_factory, main_dsn) -> None:
    """图执行器抛错 → 明确降级到 PG（不 500）。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(
        error=RuntimeError("connection refused")
    )

    response = client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert any("unreachable" in note for note in body["degradation_notes"])
    assert body["results"]  # PG 降级仍可返回 Grounded 结果


# ---- EXPLAIN_RELATION ----


def test_explain_relation_requires_both_entities(uow_factory, main_dsn) -> None:
    """EXPLAIN_RELATION 缺 subject/object → 受控 422，不猜测执行。"""
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "EXPLAIN_RELATION",
            "subject": {"entity_type": "Company", "entity_id": str(uuid.uuid4())},
        }
    })
    assert response.status_code == 422
    assert "subject and object" in response.json()["detail"]


def test_explain_relation_returns_graph_path(uow_factory, main_dsn) -> None:
    """EXPLAIN_RELATION：图路径 + 两端公司 Grounded 结果。"""
    a = seed_claim_with_evidence(uow_factory, company_name="甲公司")
    b = seed_claim_with_evidence(uow_factory, company_name="乙公司")
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(results=[{
        "path_nodes": ["甲公司", "减速器产品", "乙公司"],
        "path_rels": ["PRODUCES", "SUPPLIES_TO"],
    }])

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "EXPLAIN_RELATION",
            "subject": {"entity_type": "Company",
                        "entity_id": str(a["company"]["id"])},
            "object_entity": {"entity_type": "Company",
                              "entity_id": str(b["company"]["id"])},
            "semantic_filter": {"max_hops": 2},
        }
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert len(body["results"]) == 2
    assert any("graph path" in step
               for step in body["results"][0]["reasoning_path"])
    # 模板渲染：max_hops 内联为 int（Neo4j 不支持参数化变长上界）
    executor: StubGraphExecutor = client.app.state.query_graph_executor
    template, params = executor.executed[0]
    assert "*1..2" in template
    assert params["source_id"] == str(a["company"]["id"])
    assert params["target_id"] == str(b["company"]["id"])


def test_explain_relation_no_path_honest(uow_factory, main_dsn) -> None:
    """图无路径 → unknowns 明确说明（不虚构关系）。"""
    a = seed_claim_with_evidence(uow_factory, company_name="甲公司")
    b = seed_claim_with_evidence(uow_factory, company_name="乙公司")
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(results=[])

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "EXPLAIN_RELATION",
            "subject": {"entity_type": "Company",
                        "entity_id": str(a["company"]["id"])},
            "object_entity": {"entity_type": "Company",
                              "entity_id": str(b["company"]["id"])},
        }
    })
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["results"] == []
    assert any("no relation path" in u for u in body["unknowns"])


# ---- 双时态（as_of / known_at）----


def test_as_of_filters_claims_and_financials(uow_factory, main_dsn) -> None:
    """验收 4：as_of 分别作用于 Claim 业务有效时间与财务报告期。"""
    # 公司 5 个 FY（2020..2024）均为正
    seeded = seed_financial_company(
        uow_factory, company_name="双时态公司",
        fy_values=[(2024, 100.0), (2023, 200.0), (2022, 300.0),
                   (2021, 400.0), (2020, 500.0)],
    )
    company_id = seeded["company"]["id"]
    # 另一条 Claim：2023 年内有效（2024 起已替代）——直接 SQL 带 valid_from/to
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "INSERT INTO fact.claim (subject_entity_type, subject_entity_id, "
            "predicate_code, claim_status, content_hash, extraction_method, "
            "ontology_version, confidence, object_value, business_stage, "
            "valid_from, valid_to) "
            "VALUES ('Company', %s, 'DEVELOPS', 'ACCEPTED'::claim_status, %s, "
            "'manual', '0.1.0', 0.9, '{\"x\": 1}', 'RESEARCH', "
            "'2023-01-01', '2024-01-01')",
            (company_id, uuid.uuid4().hex),
        )
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    # as_of 早于全部财务数据 → 不足 3 FY → 排除（数据不足，不当 0）
    early = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "numeric_filter": {
                "metric_code": "NET_CF_OPERATING",
                "operator": "TOTAL_POSITIVE",
            },
            "as_of": "2021-06-30T00:00:00Z",
            "max_results": 10,
        }
    }).json()
    assert early["results"] == []
    assert "insufficient" in str(early["excluded"])

    # as_of=2023-06-30：FY2020..2022 共 3 个完整财年 → 通过
    mid = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "numeric_filter": {
                "metric_code": "NET_CF_OPERATING",
                "operator": "TOTAL_POSITIVE",
            },
            "as_of": "2023-06-30T00:00:00Z",
            "max_results": 10,
        }
    }).json()
    assert len(mid["results"]) == 1
    assert mid["results"][0]["financial_value"] == 1200.0  # 2020-2022: 500+400+300
    # as_of=2023-06-30 时 2023 年内有效的 DEVELOPS Claim 也在窗口内
    assert len(mid["results"][0]["claim_ids"]) >= 1

    # known_at 早于所有 claim recorded_at → 无已知 claim → 排除
    unknown = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "known_at": "2020-01-01T00:00:00Z",
            "max_results": 10,
        }
    }).json()
    assert unknown["results"] == []
    assert any("no matching claims" in e for e in unknown["excluded"])


# ---- 只读 + 审计（验收 8 + 幂等）----


def test_query_paths_do_not_write(uow_factory, main_dsn) -> None:
    """验收 8：/query 与 /screen 不修改 Fact/Finance/Graph（除查询审计）。"""
    seed_financial_company(
        uow_factory, company_name="只读验证公司",
        fy_values=[(2024, 100.0), (2023, 200.0), (2022, 300.0)],
    )
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    tables = {
        "fact.claim": "SELECT count(*) FROM fact.claim",
        "fact.evidence_fragment": "SELECT count(*) FROM fact.evidence_fragment",
        "finance.financial_observation":
            "SELECT count(*) FROM finance.financial_observation",
        "ops.graph_outbox": "SELECT count(*) FROM ops.graph_outbox",
        "master.company": "SELECT count(*) FROM master.company",
    }
    with psycopg.connect(main_dsn) as conn:
        before = {name: conn.execute(sql).fetchone()[0]
                  for name, sql in tables.items()}

    r1 = client.post("/api/v1/screen", json={
        "metric_code": "NET_CF_OPERATING", "operator": "TOTAL_POSITIVE",
    })
    r2 = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "semantic_filter": {"business_stage": "MASS_PRODUCTION"},
            "numeric_filter": {
                "metric_code": "NET_CF_OPERATING",
                "operator": "TOTAL_POSITIVE",
            },
        }
    })
    assert r1.status_code == 200
    assert r2.status_code == 200

    with psycopg.connect(main_dsn) as conn:
        after = {name: conn.execute(sql).fetchone()[0]
                 for name, sql in tables.items()}
    assert after == before  # 唯一允许的写入是 query.query_audit


def test_read_only_transaction_rejects_writes(main_dsn) -> None:
    """只读事务在服务端强制：任何写入被 PostgreSQL 拒绝。"""
    from src.query.api import _connect_read_only

    with _connect_read_only(main_dsn) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO master.company (canonical_name, "
                "unified_social_credit_code) VALUES ('RO', 'RO-X')"
            )


def test_statement_timeout_applied(main_dsn) -> None:
    """timeout_seconds → 事务级 statement_timeout（不留长期查询）。"""
    from src.query.api import _apply_statement_timeout, _connect_read_only
    from src.query.models import QueryIntent, QueryPlan

    plan = QueryPlan(intent=QueryIntent.SEMANTIC_SCREEN, timeout_seconds=5)
    with _connect_read_only(main_dsn) as conn:
        _apply_statement_timeout(conn, plan)
        value = conn.execute(
            "SELECT current_setting('statement_timeout')"
        ).fetchone()[0]
    # PostgreSQL 会归一化单位（5s / 5000ms 均表示 5 秒）
    assert value in ("5s", "5000ms")


def test_query_audit_recorded_and_idempotent(uow_factory, main_dsn) -> None:
    """审计：原问题/QueryPlan/版本/口径/trace_id/状态；重试不产生矛盾记录。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    plan = {
        "intent": "SEMANTIC_SCREEN",
        "semantic_filter": {"business_stage": "MASS_PRODUCTION"},
        "numeric_filter": {
            "metric_code": "NET_CF_OPERATING",
            "operator": "TOTAL_POSITIVE",
        },
    }
    headers = {"x-trace-id": "trace-audit-001"}
    r1 = client.post("/api/v1/query", json={
        "plan": plan, "natural_question": "哪些公司经营现金流三年合计为正？",
    }, headers=headers)
    assert r1.status_code == 200
    query_id = r1.json()["query_id"]

    with psycopg.connect(main_dsn) as conn:
        rows = conn.execute(
            "SELECT natural_question, intent, status, error_category, "
            "trace_id, execution_version, period_rule, plan "
            "FROM query.query_audit WHERE query_id = %s",
            (query_id,),
        ).fetchall()
    assert len(rows) == 1
    (question, intent, status, error_category, trace_id, version,
     period_rule, plan_json) = rows[0]
    assert question == "哪些公司经营现金流三年合计为正？"
    assert intent == "SEMANTIC_SCREEN"
    assert status in ("SUCCEEDED", "DEGRADED")
    assert error_category is None
    assert trace_id == "trace-audit-001"
    assert version  # 执行版本已记录
    assert period_rule == "LAST_3_FY"  # 口径入审计
    assert plan_json["intent"] == "SEMANTIC_SCREEN"  # 规范化 QueryPlan 原文

    # 同一 query_id 重试：安全重执行 + 审计仍只有一条（不矛盾）
    r2 = client.post("/api/v1/query", json={
        "plan": plan, "natural_question": "哪些公司经营现金流三年合计为正？",
    }, headers=headers)
    assert r2.status_code == 200
    with psycopg.connect(main_dsn) as conn:
        count = conn.execute(
            "SELECT count(*) FROM query.query_audit WHERE query_id = %s",
            (query_id,),
        ).fetchone()[0]
    assert count == 1


def test_rejected_plan_audited(uow_factory, main_dsn) -> None:
    """计划被拒绝也留审计（REJECTED + 错误类别）。"""
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = None

    response = client.post("/api/v1/query", json={
        "plan": {
            "intent": "SEMANTIC_SCREEN",
            "numeric_filter": {
                "metric_code": "TOTALLY_FAKE_METRIC",
                "operator": "TOTAL_POSITIVE",
            },
        }
    })
    assert response.status_code == 422
    with psycopg.connect(main_dsn) as conn:
        row = conn.execute(
            "SELECT status, error_category FROM query.query_audit "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row[0] == "REJECTED"
    assert row[1] == "plan_validation"


# ---- 注入（验收 3）----


def test_sql_injection_rejected(uow_factory, main_dsn) -> None:
    """验收 3：SQL/Cypher 注入无法影响模板/数据库。"""
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    for payload in (
        "'; DROP TABLE fact.claim; --",
        "TRUNCATE TABLE fact.claim; --",
        "GRANT ALL ON fact.claim TO public",
        "SELECT pg_sleep(10)--",
    ):
        response = client.post("/api/v1/query", json={
            "plan": {
                "intent": "SEMANTIC_SCREEN",
                "semantic_filter": {"concept_name": payload},
            }
        })
        assert response.status_code == 422, payload

    # 注入不改变事实层
    with psycopg.connect(main_dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM fact.claim"
        ).fetchone()[0] >= 1


# ---- 只读实体端点 ----


def test_company_readonly_endpoints(uow_factory, main_dsn) -> None:
    """只读实体端点：公司 + Claim + 时间线 + limit 边界。"""
    seed = seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    company_id = str(seed["company"]["id"])
    r1 = client.get(f"/api/v1/companies/{company_id}")
    assert r1.status_code == 200
    assert r1.json()["canonical_name"] == "查询测试公司"

    r2 = client.get(f"/api/v1/companies/{company_id}/claims")
    assert r2.status_code == 200
    assert r2.json()["count"] >= 1

    # limit 下界校验（负数/0 不直通 SQL）
    assert client.get(
        f"/api/v1/companies/{company_id}/claims?limit=0"
    ).status_code == 422
    assert client.get(
        f"/api/v1/companies/{company_id}/claims?limit=-1"
    ).status_code == 422
    assert client.get(
        f"/api/v1/companies/{company_id}/claims?limit=500"
    ).status_code == 422

    r3 = client.get(f"/api/v1/companies/{company_id}/timeline")
    assert r3.status_code == 200
    assert r3.json()["count"] >= 1

    missing = client.get(f"/api/v1/companies/{uuid.uuid4()}")
    assert missing.status_code == 404
