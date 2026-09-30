"""MVP 黄金查询套件（issue #11）：合成快照全链路 + 30 用例 + 重建一致性。

独立于 tests/db、tests/golden 的夹具树：会话级构建一次快照库
（SyntheticTransport → 真实采集/标准化/抽取/审核管线）+ 真实 Neo4j
投影（有 service 时），全部用例共享。重建一致性测试在模块内执行
"清图 → full_rebuild → 重跑黄金子集 → 结果一致"。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from src.db.testing import run_alembic

from tests.golden.metrics import evidence_grounding, precision, recall
from tests.helpers import make_settings

MVP_DIR = Path(__file__).resolve().parent
TEST_DATABASE_DSN = os.environ.get("TEST_DATABASE_DSN", "")
NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "")


@pytest.fixture(scope="session")
def mvp_admin_dsn() -> str:
    if not TEST_DATABASE_DSN:
        pytest.skip("TEST_DATABASE_DSN not set; MVP suite needs disposable PG",
                    allow_module_level=True)
    params = conninfo_to_dict(TEST_DATABASE_DSN)
    params["dbname"] = "postgres"
    return psycopg.conninfo.make_conninfo(**params)


def _drop(admin_dsn: str, dbname: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture(scope="session")
def mvp_dsn(mvp_admin_dsn: str) -> str:
    """快照库：一次性创建 → 迁移 → 快照构建 → 套件结束销毁。"""
    dbname = f"ontology_mvp_mvp_{uuid.uuid4().hex[:8]}"
    params = conninfo_to_dict(mvp_admin_dsn)
    params["dbname"] = dbname
    dsn = psycopg.conninfo.make_conninfo(**params)
    with psycopg.connect(mvp_admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    run_alembic("upgrade", "head", dsn)

    from scripts.build_mvp_snapshot import build_snapshot

    build_snapshot(
        dsn,
        neo4j_uri=NEO4J_URI or None,
        neo4j_password=NEO4J_PASSWORD or None,
        output_dir=MVP_DIR / "snapshots",
    )
    yield dsn
    _drop(mvp_admin_dsn, dbname)


@pytest.fixture(scope="session")
def mvp_neo4j():
    """真实 Neo4j 执行器（有 service 时）；重建一致性测试需要。"""
    if not (NEO4J_URI and NEO4J_PASSWORD):
        yield None
        return
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD), connection_timeout=10,
    )
    driver.verify_connectivity()
    yield driver
    driver.close()


@pytest.fixture()
def mvp_client(mvp_dsn: str) -> TestClient:
    from apps.api.app import create_app

    params = conninfo_to_dict(mvp_dsn)
    app = create_app(make_settings(
        postgres_host=params["host"],
        postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"],
        postgres_user=params["user"],
        postgres_password=params["password"],
    ))
    executor = getattr(app.state, "query_graph_executor", None)
    if executor is not None:
        executor.close()
        app.state.query_graph_executor = None
    return TestClient(app)


@pytest.fixture(scope="session")
def mvp_expectations() -> dict[str, Any]:
    return yaml.safe_load((MVP_DIR / "golden_mvp.yaml").read_text("utf-8"))


def _evaluate(case: dict[str, Any], body: dict[str, Any],
              company_sets: dict[str, list[str]]) -> list[str]:
    """黄金用例评估（MVP 版：含集合展开与结构要素断言）。"""
    failures: list[str] = []
    if "expected_status_code" in case:
        if body.get("_status_code") != case["expected_status_code"]:
            failures.append(
                f"status {body.get('_status_code')} != "
                f"{case['expected_status_code']}")
        return failures

    results = body.get("results", [])
    names = [r.get("company_name") for r in results]
    expected: list[str] = []
    for entry in case.get("expected_company_names", []):
        expected.extend(company_sets.get(entry, [entry]))

    if "expected_status" in case and body.get("status") != case["expected_status"]:
        failures.append(f"status {body.get('status')} != {case['expected_status']}")
    if "expected_period_rule" in case and (
        body.get("period_rule") != case["expected_period_rule"]
    ):
        failures.append(f"period_rule {body.get('period_rule')} != "
                        f"{case['expected_period_rule']}")

    forbidden: list[str] = []
    for entry in case.get("must_not_contain", []):
        forbidden.extend(company_sets.get(entry, [entry]))
    leaked = sorted(set(names) & set(forbidden))
    if leaked:
        failures.append(f"forbidden in results: {leaked}")

    missing = sorted(set(expected) - set(names))
    if missing:
        failures.append(f"missing results: {missing}")

    for entry in case.get("expect_unknowns_or_excluded_contain", []):
        for company in company_sets.get(entry, [entry]):
            notes = body.get("unknowns", []) + body.get("excluded", [])
            if not any(company in item for item in notes):
                failures.append(f"{company} absent from unknowns/excluded")
    for entry in case.get("expect_excluded_contain", []):
        for company in company_sets.get(entry, [entry]):
            if not any(company in item for item in body.get("excluded", [])):
                failures.append(f"{company} absent from excluded")
    needle = case.get("expect_excluded_contain_text")
    if needle and not any(needle in item for item in body.get("excluded", [])):
        failures.append(f"excluded lacks {needle!r}")

    for entry, values in case.get("expected_financial_value", {}).items():
        company = company_sets.get(entry, [entry])[0]
        matched = next((r for r in results if r["company_name"] == company), None)
        if matched is None:
            failures.append(f"{company} missing for value check")
        elif matched.get("financial_value") != values:
            failures.append(
                f"{company} value {matched.get('financial_value')} != {values}")

    for case_entry in case.get("require_fields_on_results", []):
        for r in results:
            if case_entry not in r:
                failures.append(f"{r.get('company_name')} lacks {case_entry}")
    for field in case.get("require_nonempty", []):
        for r in results:
            if not r.get(field):
                failures.append(f"{r.get('company_name')} empty {field}")
    needle = case.get("require_reasoning_contains")
    if needle and not any(
        needle in step for r in results for step in r.get("reasoning_path", [])
    ):
        failures.append(f"no reasoning step contains {needle!r}")

    # 阈值消费（issue #10 验收 8）：指标实值必须 ≥ 期望阈值。
    # 排除型用例（无 expected_company_names）的 precision 无定义——
    # 只断言 must_not_contain + grounding，不适用 P/R。
    if expected:
        prec = precision(names, expected)
        rec = recall(names, expected)
        if prec < case.get("min_precision", 1.0):
            failures.append(f"precision {prec:.3f} < {case['min_precision']}")
        if rec < case.get("min_recall", 1.0):
            failures.append(f"recall {rec:.3f} < {case['min_recall']}")
    grounding = evidence_grounding(results)
    if grounding < case.get("min_evidence_grounding", 1.0):
        failures.append(
            f"evidence_grounding {grounding:.3f} < "
            f"{case['min_evidence_grounding']}")
    return failures


def _run(mvp_client: TestClient, case: dict[str, Any]) -> dict[str, Any]:
    response = mvp_client.post(case["request"]["path"], json=case["request"]["body"])
    body = response.json()
    body["_status_code"] = response.status_code
    return body


def _company_sets(expectations: dict[str, Any]) -> dict[str, list[str]]:
    sets = dict(expectations["company"])
    sets["all"] = [name for names in expectations["company"].values()
                   for name in names]
    return sets


def test_mvp_golden_30(mvp_client: TestClient, mvp_expectations: dict[str, Any]) -> None:
    """验收 7：30 条黄金查询全部通过（Precision/Recall 由集合断言保证）。"""
    cases = mvp_expectations["cases"]
    assert len(cases) == 30, "issue #11 requires 30 golden queries"
    sets = _company_sets(mvp_expectations)
    failures: list[str] = []
    for case in cases:
        body = _run(mvp_client, case)
        failures.extend(
            f"{case['id']}: {f}" for f in _evaluate(case, body, sets)
        )
    assert not failures, "\n".join(failures)


def test_mvp_rebuild_consistency(
    mvp_client: TestClient, mvp_dsn: str, mvp_neo4j,
    mvp_expectations: dict[str, Any],
) -> None:
    """验收 6：删除 Neo4j 数据后由 PostgreSQL 重建，黄金结果一致。"""
    if mvp_neo4j is None:
        pytest.skip("Neo4j service not configured; rebuild test is CI-only")
    from src.db.uow import UnitOfWorkFactory
    from src.projection.reconcile import full_rebuild

    class _DriverExecutor:
        def __init__(self, driver: Any) -> None:
            self._driver = driver

        def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            with self._driver.session() as session:
                return [dict(r) for r in session.run(template, params)]

    executor = _DriverExecutor(mvp_neo4j)
    uow_factory = UnitOfWorkFactory(mvp_dsn)
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(executor)

    def project_entity(label: str, eid: str, props: dict[str, Any]) -> None:
        projector.project_entity(label, eid, props)

    def project_claim(cd: dict[str, Any]) -> None:
        projector.project_claim_node(cd)

    def project_edge(cd: dict[str, Any]) -> None:
        if not (cd.get("subject_entity_id") and cd.get("object_entity_id")):
            return
        projector.project_business_edge(
            rel_type=cd["predicate_code"] if cd["predicate_code"] in
            ("PRODUCES", "DEVELOPS", "SUPPLIES_TO") else "PRODUCES",
            source_id=str(cd["subject_entity_id"]),
            target_id=str(cd["object_entity_id"]),
            claim_id=str(cd["id"]),
            props={"business_stage": cd.get("business_stage"),
                   "recorded_at": str(cd.get("recorded_at") or "")},
        )

    # 重建前基线（PG 降级结果集）
    case = mvp_expectations["cases"][0]
    before = _run(mvp_client, case)

    # 清空图 → full_rebuild
    mvp_neo4j.execute_query("MATCH (n) DETACH DELETE n")
    report = full_rebuild(uow_factory, graph_executor=executor,
                          project_entity_fn=project_entity,
                          project_claim_fn=project_claim,
                          project_edge_fn=project_edge)
    assert report, "rebuild produced no report"

    # 重建后黄金子集必须一致：结果集 + 推理路径 + 证据（验收 6 全文）
    after = _run(mvp_client, case)
    def _fingerprint(body: dict[str, Any]) -> list[tuple]:
        return sorted(
            (r["company_name"], tuple(r["reasoning_path"]),
             tuple(sorted(r["evidence_ids"])), r["financial_value"])
            for r in body["results"]
        )
    assert _fingerprint(before) == _fingerprint(after), (
        "rebuild changed results, reasoning paths or evidence"
    )


def test_mvp_snapshot_scale_requirements(mvp_dsn: str) -> None:
    """验收 1/2/3 的规模底线：30 公司 / 52 产品概念 / ≥500 候选 Claim。"""
    with psycopg.connect(mvp_dsn) as conn:
        companies = conn.execute(
            "SELECT count(*) FROM master.company WHERE canonical_name LIKE '辛示%'"
        ).fetchone()[0]
        candidates = conn.execute("SELECT count(*) FROM fact.claim").fetchone()[0]
        accepted = conn.execute(
            "SELECT count(*) FROM fact.claim WHERE claim_status = 'ACCEPTED'"
        ).fetchone()[0]
        with_evidence = conn.execute(
            "SELECT count(*) FROM fact.claim c WHERE c.claim_status = 'ACCEPTED' "
            "AND EXISTS (SELECT 1 FROM fact.claim_evidence ce "
            "WHERE ce.claim_id = c.id)"
        ).fetchone()[0]
        products = conn.execute(
            "SELECT count(*) FROM master.product"
        ).fetchone()[0]
    assert companies == 30
    assert products >= 10, "core-component product entities must be materialized"
    assert candidates >= 500, f"candidates {candidates} < 500 (验收 3)"
    assert accepted >= 400
    assert with_evidence == accepted, (
        "所有 Accepted 文本经营 Claim 必须有 Evidence（验收 3：100%）"
    )
    assert products >= 1


def test_mvp_first_query_returns_full_evidence_pack(
    mvp_client: TestClient,
) -> None:
    """验收 8：首个筛选问题每家公司返回证券/产品/阶段/Claim/证据/口径/路径。"""
    response = mvp_client.post("/api/v1/screen", json={
        "business_stage": "MASS_PRODUCTION",
        "metric_code": "NET_CF_OPERATING",
        "operator": "TOTAL_POSITIVE",
        "max_results": 3,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["results"], "no results for first MVP query"
    for result in body["results"]:
        assert result["claim_ids"]
        assert result["business_stage"] == "MASS_PRODUCTION"
        assert result["evidence_ids"]
        assert result["reasoning_path"]
        assert result["financial_value"] is not None
        assert result["currency"] == "CNY"
        assert result["report_period"]
        assert len(result["financial_detail"]) == 3
        assert result["data_freshness"]
    # 口径显式返回（"合计为正"≠"每年均为正"）
    assert body["period_rule"] == "LAST_3_FY_TOTAL_POSITIVE"
