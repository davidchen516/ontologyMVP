"""issue #31 新增受控只读端点测试：公司列表/搜索 + 首页统计 + 最近运行。"""

from __future__ import annotations

import uuid

import psycopg

from tests.db.test_query_api import make_client, seed_claim_with_evidence


def test_list_companies_default(uow_factory, main_dsn) -> None:
    seed_claim_with_evidence(uow_factory, company_name="列表公司甲")
    seed_claim_with_evidence(uow_factory, company_name="列表公司乙",
                             stage="RESEARCH")
    client = make_client(main_dsn)

    response = client.get("/api/v1/companies")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    names = [c["canonical_name"] for c in body["companies"]]
    assert names == sorted(names)  # 确定性排序
    row = body["companies"][0]
    assert "security_code" in row and "produces_claims" in row
    # PRODUCES 谓词计数（两个 seed 均为 PRODUCES）
    by_name = {c["canonical_name"]: c for c in body["companies"]}
    assert by_name["列表公司甲"]["produces_claims"] == 1
    assert by_name["列表公司乙"]["produces_claims"] == 1


def test_list_companies_search_escapes_like_wildcards(uow_factory, main_dsn) -> None:
    """搜索词中的 %/_ 按字面匹配（不充当 SQL 通配符）。"""
    with uow_factory.transaction() as uow:
        for name in ("搜索%百分号公司", "搜索_下划线公司", "搜索普通公司"):
            uow._conn.execute(  # noqa: SLF001
                "INSERT INTO master.company (canonical_name, "
                "unified_social_credit_code) VALUES (%s, %s)",
                (name, f"USCC-{uuid.uuid4().hex[:10]}"),
            )
    client = make_client(main_dsn)

    # % 字面搜索：只命中含 % 的公司，不匹配任意串
    body = client.get("/api/v1/companies", params={"q": "搜索%"}).json()
    assert body["total"] == 1
    assert body["companies"][0]["canonical_name"] == "搜索%百分号公司"

    # _ 字面搜索
    body = client.get("/api/v1/companies", params={"q": "搜索_下"}).json()
    assert body["total"] == 1
    assert body["companies"][0]["canonical_name"] == "搜索_下划线公司"

    # 普通包含搜索
    body = client.get("/api/v1/companies", params={"q": "普通"}).json()
    assert body["total"] == 1


def test_list_companies_stage_filter_and_pagination(
    uow_factory, main_dsn
) -> None:
    seed_claim_with_evidence(uow_factory, company_name="分页量产01")
    seed_claim_with_evidence(uow_factory, company_name="分页量产02")
    seed_claim_with_evidence(uow_factory, company_name="分页研发03",
                             stage="RESEARCH")
    client = make_client(main_dsn)

    # stage 过滤：只有量产公司命中（研发公司 stage=RESEARCH 不匹配）
    body = client.get(
        "/api/v1/companies", params={"stage": "MASS_PRODUCTION"}
    ).json()
    assert body["total"] == 2
    names = {c["canonical_name"] for c in body["companies"]}
    assert "分页研发03" not in names
    assert names == {"分页量产01", "分页量产02"}

    # 分页：按 canonical_name 排序（"研" U+7814 < "量" U+91CF → 研发03 在前）
    page = client.get(
        "/api/v1/companies", params={"limit": 1, "offset": 1}
    ).json()
    assert page["total"] == 3
    assert len(page["companies"]) == 1
    assert page["companies"][0]["canonical_name"] == "分页量产01"

    # 边界校验
    assert client.get(
        "/api/v1/companies", params={"limit": 0}
    ).status_code == 422
    assert client.get(
        "/api/v1/companies", params={"offset": -1}
    ).status_code == 422


def test_list_companies_read_only(main_dsn) -> None:
    """端点不产生事实层写入（#9 验收 8 延续）。"""
    from src.query.api import _connect_read_only

    with _connect_read_only(main_dsn) as conn:
        import pytest

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO master.company (canonical_name, "
                "unified_social_credit_code) VALUES "
                "('列表写入测试', 'USCC-RO-TEST')"
            )


def test_overview_stats_real_counts(uow_factory, main_dsn) -> None:
    seed_claim_with_evidence(uow_factory)  # 1 公司 + 1 claim + 1 evidence
    client = make_client(main_dsn)

    body = client.get("/api/v1/overview/stats").json()
    assert body["companies"] >= 1
    assert body["accepted_claims"] >= 1
    assert body["evidence_fragments"] >= 1
    # 新鲜度时间戳真实存在
    assert body["data_freshness"]["latest_claim_at"] is not None
    # 快照说明（Epic #29：统计携带说明，不伪装真实数据）
    assert "合成快照" in body["snapshot_note"]


def test_overview_recent_runs(uow_factory, main_dsn) -> None:
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "INSERT INTO ops.ingest_run (dataset_name, source_system, "
            "status, trace_id, finished_at) VALUES "
            "('tushare:stock_basic', 'TUSHARE', 'SUCCEEDED', 't-1', now())"
        )
        uow._conn.execute(  # noqa: SLF001
            "INSERT INTO ops.normalization_run (dataset_name, "
            "source_system, mapping_version, status, finished_at, trace_id) "
            "VALUES ('stock_basic', 'TUSHARE', 'v1', 'SUCCEEDED', now(), "
            "'t-norm-1')"
        )
    client = make_client(main_dsn)

    body = client.get("/api/v1/overview/recent-runs").json()
    assert len(body["ingest_runs"]) >= 1
    assert body["ingest_runs"][0]["dataset_name"] == "tushare:stock_basic"
    assert len(body["normalization_runs"]) >= 1
    # limit 边界
    assert client.get(
        "/api/v1/overview/recent-runs", params={"limit": 0}
    ).status_code == 422


def test_company_detail_has_evidence_summary(uow_factory, main_dsn) -> None:
    """公司详情端点（既有）在 #31 语境下的核心要素回归。"""
    seeded = seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)

    r = client.get(f"/api/v1/companies/{seeded['company']['id']}")
    assert r.status_code == 200
    assert r.json()["canonical_name"] == "查询测试公司"

    claims = client.get(
        f"/api/v1/companies/{seeded['company']['id']}/claims"
    ).json()
    assert claims["count"] >= 1
    assert claims["claims"][0]["claim_status"] == "ACCEPTED"
