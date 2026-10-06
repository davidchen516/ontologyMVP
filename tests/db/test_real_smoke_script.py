"""issue #45 冒烟脚本编排测试：stub transport 全管道（无网络/无真实 token）。

真实账户运行是人工证据（issue 评论 + docs 报告）；本测试锁定编排逻辑：
低层级字段形态（缺 exchange/list_status）→ 探针 AVAILABLE + 缺失记录 →
默认+钉住采集 → 标准化 → 真实文本证据/Claim → screen 断言全部不变量。
"""

from __future__ import annotations

from apps.api.app import create_app
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from scripts.real_account_smoke import run

from tests.helpers import make_settings

INTRO = "公司专注于工业机器人核心部件的研发、生产与销售，产品广泛应用于制造业。"

# 默认探针参数（无 ts_code）返回的"任意第一行"——模拟低层级真实账户
# list_status=L&limit=1 实测返回 920202.BJ 且与候选公司证券不同的事实，
# 验证钉住路径（ts_code 参数）的连接闭环设计。
DEFAULT_SECURITY = ["920202.BJ", "920202", "安达股份"]


def _stub_transport(request: dict) -> dict:
    """参数感知 stub：钉住 ts_code 的请求返回对应证券/公司的真实形态行。"""
    api = request["api_name"]
    params = request.get("params") or {}
    ts = params.get("ts_code")

    if api == "stock_basic":
        # 低层级形态：缺 exchange/list_status（expected 层级——不熔断）
        row = list(DEFAULT_SECURITY) if not ts else [
            ts, ts.split(".")[0], f"公司{ts.split('.')[0]}",
        ]
        return {"code": 0, "msg": "",
                "data": {"fields": ["ts_code", "symbol", "name"],
                         "items": [row]}}
    if api == "stock_company":
        row_ts = ts or "000001.SZ"
        return {"code": 0, "msg": "",
                "data": {"fields": [
                    "ts_code", "name", "fullname", "introduction",
                    "main_business", "chairman", "reg_capital",
                ], "items": [[
                    row_ts, f"公司{row_ts.split('.')[0]}",
                    f"公司{row_ts.split('.')[0]}股份有限公司", INTRO,
                    "工业机器人核心部件的研发、生产与销售。", "董事长", "100000.0",
                ]]}}
    # 财务 vip（period+ts_code 钉住；无 ts_code 时同形返回）
    row_ts = ts or "000001.SZ"
    shapes = {
        "income_vip": ["ts_code", "end_date", "ann_date", "report_type",
                       "comp_type", "update_flag", "total_revenue", "n_income"],
        "balancesheet_vip": ["ts_code", "end_date", "total_assets",
                             "total_liab"],
        "cashflow_vip": ["ts_code", "end_date", "n_cashflow_act"],
        "fina_indicator_vip": ["ts_code", "end_date", "roe",
                               "debt_to_assets"],
    }
    items_map = {
        "income_vip": [row_ts, "20251231", "20260327", "1", "1", "0",
                       "360000000.00", "85000000.00"],
        "balancesheet_vip": [row_ts, "20251231", "5800000000.00",
                             "5200000000.00"],
        "cashflow_vip": [row_ts, "20251231", "420000000.00"],
        "fina_indicator_vip": [row_ts, "20251231", "8.5", "89.6"],
    }
    return {"code": 0, "msg": "",
            "data": {"fields": shapes[api], "items": [items_map[api]]}}


def test_real_smoke_orchestration_full_pipeline(uow_factory, main_dsn,
                                                monkeypatch) -> None:
    """全管道编排 + 端到端不变量：exit 0、零失败项。"""
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

    def screen_runner(smoke_db: str) -> dict:
        settings = make_settings(
            postgres_host=params["host"],
            postgres_port=int(params.get("port") or 5432),
            postgres_db=params["dbname"], postgres_user=params["user"],
            postgres_password=params["password"],
        )
        app = create_app(settings)
        executor = getattr(app.state, "query_graph_executor", None)
        if executor is not None:
            executor.close()
            app.state.query_graph_executor = None
        with TestClient(app) as client:
            resp = client.post("/api/v1/screen", json={"max_results": 10})
        body = resp.json()
        results = body.get("results", [])
        return {
            "http_status": resp.status_code,
            "query_status": body.get("status"),
            "result_count": len(results),
            "companies": [
                {
                    "company_name": r.get("company_name"),
                    "security_code": r.get("security_code"),
                    "claim_ids": len(r.get("claim_ids") or []),
                    "evidence_quotes": len(r.get("evidence_quotes") or []),
                    "business_stage": r.get("business_stage"),
                }
                for r in results[:5]
            ],
        }

    report, failures, exit_code = run(
        transport=_stub_transport,
        uow_factory=uow_factory,
        screen_runner=screen_runner,
    )

    assert exit_code == 0, failures
    assert failures == []
    # 低层级形态 → 无 SCHEMA_CHANGED 熔断 + expected 缺失被记录
    assert report["probe"]["datasets"]["stock_basic"]["status"] == "AVAILABLE"
    assert report["probe"]["datasets"]["stock_basic"]["missing_expected_fields"] == [
        "exchange", "list_status",
    ]
    for api, entry in report["probe"]["datasets"].items():
        assert entry["status"] != "SCHEMA_CHANGED", api
    # 钉住路径：候选尝试 + 胜出者闭环（默认证券 920202.BJ ≠ 候选 → 连接靠钉住）
    assert report["ingest"]["candidates_tried"] == ["002230.SZ"]
    assert report["ingest"]["winner"] == "002230.SZ"
    # 采集与标准化（默认 + 钉住行都入 raw；标准化写入 ≥ 钉住公司）
    assert report["ingest"]["datasets"]["stock_basic"]["rows_inserted"] == 2
    assert report["ingest"]["datasets"]["stock_company"]["rows_inserted"] == 2
    assert report["normalize"]["datasets"]["stock_basic"]["rows_written"] >= 1
    assert report["normalize"]["datasets"]["stock_company"]["rows_written"] >= 1
    # 主数据 + #43 降级语义：list_status 缺失 → UNKNOWN 计数 ≥1
    assert report["master"]["master_security_total"] >= 1
    assert report["master"]["master_security_status_unknown"] >= 1
    assert report["master"]["master_company_total"] >= 1
    assert report["master"]["financial_observation_total"] >= 1
    # 证据/Claim（真实文本 → 规则抽取 → auto-accept）
    assert report["claims"]["candidates"] >= 1
    assert report["claims"]["accepted"] >= 1
    # screen 返回真实公司（带 claim + evidence）
    assert report["screen"]["result_count"] >= 1
    assert report["screen"]["companies"][0]["claim_ids"] >= 1
    assert report["screen"]["companies"][0]["evidence_quotes"] >= 1
    # 额度有界：探针 6 + 采集 8（默认 2 + 候选 2 + 财务 4）
    assert report["probe"]["requests"] == 6
    assert report["ingest"]["requests"] == 8
    # 脱敏形态记录存在（字段名集合）
    assert "stock_basic" in report["master"]["raw_field_shapes"]


def test_real_smoke_no_token_fails_explicitly(uow_factory, monkeypatch) -> None:
    """负向：缺 token → 退出码 2 + 明确失败项（不假装成功，不触碰数据库）。"""
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "ontology")
    monkeypatch.setenv("POSTGRES_USER", "ontology")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unit-test-password")
    monkeypatch.setenv("TUSHARE_TOKEN", "")
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-password")

    report, failures, exit_code = run(uow_factory=uow_factory)
    assert exit_code == 2
    assert failures == ["TUSHARE_TOKEN not set"]
    assert report["meta"]["data_mode"] == "no-token"
