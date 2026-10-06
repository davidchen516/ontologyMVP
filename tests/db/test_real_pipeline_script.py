"""issue #56 真实全市场管道编排测试：stub transport 全流程（无网络/无 token）。

真实全量运行是人工证据（runbook/issue 评论）；本测试锁定编排逻辑：
分页采集 → 证券标准化 → 逐只 stock_company（共享限流）→ 财务/主营构成
标准化 → 真实主营构成产品物化 → full_rebuild 投影 + 主营构成边 → 报告。
规模不变量（≥4000 证券等）为真实运行判据——stub 下如实退出 3。
"""

from __future__ import annotations

from psycopg.conninfo import conninfo_to_dict
from scripts.real_data_pipeline import run

SECURITY_A = "000001.SZ"
SECURITY_B = "600519.SH"

STUB_ROWS = {
    # 全量分页形态：单页（<1000 行）短页终止
    "stock_basic": {
        "fields": ["ts_code", "symbol", "name"],
        "items": [[SECURITY_A, "000001", "公司A"], [SECURITY_B, "600519", "公司B"]],
    },
    "stock_company": {
        "fields": ["ts_code", "com_name", "introduction", "main_business"],
        "items": [[SECURITY_A, "公司A股份有限公司", "公司A主营研发。",
                   "智能设备的研发、生产与销售。"]],
    },
    "income_vip": {
        "fields": ["ts_code", "end_date", "ann_date", "report_type", "comp_type",
                   "update_flag", "total_revenue", "n_income"],
        "items": [[SECURITY_A, "20251231", "20260327", "1", "1", "0",
                   "100000000.00", "20000000.00"]],
    },
    "cashflow_vip": {
        "fields": ["ts_code", "end_date", "n_cashflow_act"],
        "items": [[SECURITY_A, "20251231", "30000000.00"]],
    },
    "fina_indicator_vip": {
        "fields": ["ts_code", "end_date", "roe", "debt_to_assets"],
        "items": [[SECURITY_A, "20251231", "12.5", "45.0"]],
    },
    # 主营构成：SECURITY_A 两个真实形态产品段（P 类）
    "fina_mainbz_vip": {
        "fields": ["ts_code", "end_date", "bz_item", "bz_code", "bz_sales",
                   "bz_cost", "bz_profit", "curr_type", "update_flag"],
        "items": [
            [SECURITY_A, "20251231", "智能终端设备", "P", "60.00", "40.00",
             "20.00", "CNY", "0"],
            [SECURITY_A, "20251231", "工业机器人", "P", "40.00", "25.00",
             "15.00", "CNY", "0"],
            [SECURITY_A, "20251231", "高端装备行业", "I", "100.00", "65.00",
             "35.00", "CNY", "0"],
        ],
    },
}


def _stub_transport(request: dict) -> dict:
    """参数感知 stub：stock_company 按 ts_code 过滤（逐只循环形态）。"""
    api = request["api_name"]
    shape = STUB_ROWS[api]
    params = request.get("params") or {}
    ts = params.get("ts_code")
    if api == "stock_company" and ts:
        items = [row for row in shape["items"] if row[0] == ts]
        if not items:
            items = [[ts, f"公司{ts.split('.')[0]}股份有限公司",
                      "公司主营研发。", "智能设备研发。"]]
        return {"code": 0, "msg": "", "data": {"fields": shape["fields"],
                                               "items": items}}
    return {"code": 0, "msg": "",
            "data": {"fields": shape["fields"], "items": shape["items"]}}


def test_real_pipeline_orchestration(uow_factory, main_dsn, monkeypatch) -> None:
    """编排全流程：分页采集→逐只公司→标准化→产品物化→投影+主营构成边。"""
    from src.projection.projector import FakeGraphExecutor

    params = conninfo_to_dict(main_dsn)
    monkeypatch.setenv("POSTGRES_HOST", params["host"])
    monkeypatch.setenv("POSTGRES_PORT", str(params.get("port") or 5432))
    monkeypatch.setenv("POSTGRES_DB", params["dbname"])
    monkeypatch.setenv("POSTGRES_USER", params["user"])
    monkeypatch.setenv("POSTGRES_PASSWORD", params["password"])
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-password")
    monkeypatch.setenv("TUSHARE_TOKEN", "stub-token-not-real")

    report, failures, exit_code = run(
        company_limit=10,
        uow_factory=uow_factory,
        transport=_stub_transport,
        graph_executor=FakeGraphExecutor(),
    )

    # 编排正确性（stub 数据全链路落地）
    assert report["ingest"]["datasets"]["stock_basic"]["rows_inserted"] == 2
    assert report["normalize"]["stock_basic"]["rows_written"] == 2
    # 逐只 stock_company：2 只证券各 1 请求，公司落库
    assert report["meta"]["company_attempted"] == 2
    assert report["ingest"]["datasets"]["stock_company"]["request_count"] == 2
    assert report["master"]["master_company_total"] == 2
    assert report["master"]["company_security_total"] == 2
    # 财务 + 主营构成标准化
    assert report["master"]["financial_observation_total"] >= 3
    assert report["master"]["business_segment_total"] == 3
    # 产品物化：2 个 P 类段名（I 类不物化）
    assert report["products_materialized"]["product_names"] == 2
    assert report["master"]["master_product_total"] == 2
    # 投影：实体 + 主营构成边（claim 为 0——#56 非目标的既定代价）
    assert report["projection"]["entities_rebuilt"]["Company"] == 2
    assert report["projection"]["entities_rebuilt"]["Security"] == 2
    assert report["projection"]["entities_rebuilt"]["Product"] == 2
    assert report["projection"]["mainbz_edges"] == 2
    assert report["master"]["claim_total"] == 0
    # 规模不变量为真实运行判据——stub 下如实退出 3 并列出差距
    assert exit_code == 3
    assert any("master.security = 2" in f for f in failures)
    assert any("financial_observation" in f for f in failures)
    assert not any("master.product" in f for f in failures)  # 产品链路 OK
    assert not any("经营边为 0" in f for f in failures)


def test_real_pipeline_no_token_exits_2(monkeypatch) -> None:
    """负向：缺 token → 退出码 2（不触碰数据库/网络）。"""
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "ontology")
    monkeypatch.setenv("POSTGRES_USER", "ontology")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unit-test-password")
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-password")
    monkeypatch.setenv("TUSHARE_TOKEN", "")

    report, failures, exit_code = run()
    assert exit_code == 2
    assert failures == ["TUSHARE_TOKEN not set"]
