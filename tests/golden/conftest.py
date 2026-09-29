"""黄金查询回归夹具：一次性库 + 固定种子场景（期望基线见 expectations.yaml）。

独立于 tests/db 的 conftest（pytest fixture 作用域按目录树隔离）：
golden 套件自建一次性库，种子在 session 级写入一次，全部用例共享。
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

from tests.helpers import make_settings

GOLDEN_DIR = Path(__file__).resolve().parent

TEST_DATABASE_DSN = os.environ.get("TEST_DATABASE_DSN", "")

# 固定种子名（期望文件按名断言；修改需同步 expectations.yaml）
GOLDEN_PRODUCTION_A = "黄金量产公司甲"
GOLDEN_PRODUCTION_B = "黄金量产公司乙"
GOLDEN_RESEARCH = "黄金研发公司"
GOLDEN_NO_EVIDENCE = "黄金无证据公司"
GOLDEN_FINANCE_OK = "黄金财务公司"        # 3FY: 100/200/300
GOLDEN_FINANCE_LOSS = "黄金亏损公司"      # 3FY: 100/-50/200
GOLDEN_FINANCE_SHORT = "黄金财年不足公司"  # 仅 2FY


def _seed_claim(
    conn: Any, *, company_id: Any, stage: str, with_evidence: bool
) -> None:
    document_id = None
    evidence_id = None
    if with_evidence:
        document_id = conn.execute(
            "INSERT INTO fact.document (document_type, source_system, title, "
            "content_hash) VALUES ('ANNUAL_REPORT', 'CNINFO', '年报', %s) "
            "RETURNING id",
            (uuid.uuid4().hex * 2,),
        ).fetchone()[0]
    claim_id = conn.execute(
        "INSERT INTO fact.claim (subject_entity_type, subject_entity_id, "
        "predicate_code, claim_status, content_hash, extraction_method, "
        "ontology_version, confidence, object_value, business_stage) "
        "VALUES ('Company', %s, 'PRODUCES', 'ACCEPTED'::claim_status, %s, "
        "'manual', '0.1.0', 0.9, '{\"x\": 1}', %s) RETURNING id",
        (company_id, uuid.uuid4().hex, stage),
    ).fetchone()[0]
    if with_evidence and document_id is not None:
        evidence_id = conn.execute(
            "INSERT INTO fact.evidence_fragment (document_id, quote_text, "
            "checksum) VALUES (%s, '黄金证据', %s) RETURNING id",
            (document_id, uuid.uuid4().hex),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO fact.claim_evidence (claim_id, evidence_id, "
            "support_type, source_weight) VALUES (%s, %s, 'SUPPORTS', 1.0)",
            (claim_id, evidence_id),
        )


def _seed_company(conn: Any, name: str) -> Any:
    return conn.execute(
        "INSERT INTO master.company (canonical_name, unified_social_credit_code) "
        "VALUES (%s, %s) RETURNING id",
        (name, f"USCC-GOLDEN-{uuid.uuid4().hex[:12]}"),
    ).fetchone()[0]


def _seed_financials(conn: Any, company_id: Any, fy_values: dict[int, float]) -> None:
    run = conn.execute(
        "INSERT INTO ops.ingest_run (dataset_name, source_system, status, "
        "trace_id) VALUES ('golden', 'GOLDEN', 'SUCCEEDED', %s) RETURNING id",
        (uuid.uuid4().hex,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO finance.financial_metric (metric_code, name, "
        "statement_type, unit_type, default_aggregation) VALUES "
        "('NET_CF_OPERATING', '经营现金流', 'CASHFLOW', 'CNY', 'SUM') "
        "ON CONFLICT (metric_code) DO NOTHING"
    )
    src = conn.execute(
        "INSERT INTO raw.source_record (source_system, api_name, payload_hash, "
        "raw_payload, ingest_run_id) VALUES ('GOLDEN', 'cashflow', %s, '{}', %s) "
        "RETURNING id",
        (uuid.uuid4().hex + uuid.uuid4().hex, run),
    ).fetchone()[0]
    exchange = conn.execute(
        "INSERT INTO master.exchange (code, name) VALUES (%s, 'T') "
        "RETURNING id", (f"GEX-{uuid.uuid4().hex[:8]}",),
    ).fetchone()[0]
    security = conn.execute(
        "INSERT INTO master.security (ts_code, symbol, name, exchange_id, "
        "status) VALUES (%s, 'g', '黄金证券', %s, 'ACTIVE') RETURNING id",
        (f"{uuid.uuid4().hex[:10]}.SZ", exchange),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO master.company_security (company_id, security_id, "
        "recorded_at, source_record_id) VALUES (%s, %s, now(), %s)",
        (company_id, security, src),
    )
    for year, value in fy_values.items():
        conn.execute(
            "INSERT INTO finance.financial_observation (security_id, "
            "metric_code, period_end, report_type, value, currency, "
            "announced_at, source_record_id) "
            "VALUES (%s, 'NET_CF_OPERATING', %s, '1', %s, 'CNY', "
            "'2025-04-30', %s)",
            (security, f"{year}-12-31", value, src),
        )


def _seed_all(conn: Any) -> None:
    # 语义场景：两家量产有证据；研发公司无证据（evidence_exclusion 场景
    # 依赖它进 unknowns）；纯无证据公司
    for name, stage, with_ev in (
        (GOLDEN_PRODUCTION_A, "MASS_PRODUCTION", True),
        (GOLDEN_PRODUCTION_B, "MASS_PRODUCTION", True),
        (GOLDEN_RESEARCH, "RESEARCH", False),
    ):
        company_id = _seed_company(conn, name)
        _seed_claim(conn, company_id=company_id, stage=stage,
                    with_evidence=with_ev)
    no_ev = _seed_company(conn, GOLDEN_NO_EVIDENCE)
    _seed_claim(conn, company_id=no_ev, stage="RESEARCH", with_evidence=False)
    # 财务场景：达标 5FY（时态场景裁窗口用）/ 亏损 3FY / 不足 2FY——
    # 财务公司 Claim 阶段 RESEARCH 防止串入语义场景的结果集
    fin_ok = _seed_company(conn, GOLDEN_FINANCE_OK)
    _seed_claim(conn, company_id=fin_ok, stage="RESEARCH", with_evidence=True)
    _seed_financials(conn, fin_ok,
                     {2024: 100.0, 2023: 200.0, 2022: 300.0,
                      2021: 400.0, 2020: 500.0})
    fin_loss = _seed_company(conn, GOLDEN_FINANCE_LOSS)
    _seed_claim(conn, company_id=fin_loss, stage="RESEARCH", with_evidence=True)
    _seed_financials(conn, fin_loss, {2024: 100.0, 2023: -50.0, 2022: 200.0})
    fin_short = _seed_company(conn, GOLDEN_FINANCE_SHORT)
    _seed_claim(conn, company_id=fin_short, stage="RESEARCH", with_evidence=True)
    _seed_financials(conn, fin_short, {2024: 100.0, 2023: 200.0})


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    if not TEST_DATABASE_DSN:
        pytest.skip(
            "TEST_DATABASE_DSN not set; golden suite needs a disposable "
            "PostgreSQL+pgvector",
            allow_module_level=True,
        )
    params = conninfo_to_dict(TEST_DATABASE_DSN)
    params["dbname"] = "postgres"
    return psycopg.conninfo.make_conninfo(**params)


@pytest.fixture(scope="session")
def golden_dsn(admin_dsn: str) -> str:
    """黄金套件一次性库：创建 → 迁移 → 固定种子 → 全套结束销毁。"""
    from tests.db.conftest import drop_db, fresh_db_dsn

    dsn, dbname = fresh_db_dsn(admin_dsn, prefix="ontology_mvp_golden")
    run_alembic("upgrade", "head", dsn)
    with psycopg.connect(dsn) as conn:
        _seed_all(conn)
        conn.commit()
    yield dsn
    drop_db(admin_dsn, dbname)


@pytest.fixture()
def golden_client(golden_dsn: str) -> TestClient:
    """无图执行器的黄金客户端（PG 降级是基线锁定的行为）。"""
    from apps.api.app import create_app

    params = conninfo_to_dict(golden_dsn)
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


@pytest.fixture()
def expectations() -> list[dict[str, Any]]:
    data = yaml.safe_load((GOLDEN_DIR / "expectations.yaml").read_text("utf-8"))
    return data["cases"]
