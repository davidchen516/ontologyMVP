"""标准化管道 DB 测试（issue #4 验收逐条 + 管道不变量）。"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

import pytest
from src.connectors.ingest import ingest_dataset, start_run
from src.connectors.testing import FixtureTransport
from src.connectors.tushare_connectors import TushareConnector
from src.db.uow import UnitOfWorkFactory
from src.domain.enums import IngestRunStatus
from src.standardize.financial import recent_three_fy_operating_cashflow
from src.standardize.pipeline import (
    ActiveNormalizationExistsError,
    normalize_dataset,
    recover_stale_normalizations,
    start_normalization_run,
)
from src.standardize.processors import build_processors
from src.standardize.repositories import (
    NormalizationEventRepository,
    NormalizationRunRepository,
)
from src.standardize.transforms import mapping_version

from tests.helpers import make_settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tushare"
LEASE_TTL = 600
# 模块自管一次性种子（standardized 夹具），跳过逐测试清库
PRESERVE_STANDARDIZATION_DATA = True

# 标准化依赖顺序：证券先于公司/行业/概念/财务
DATASET_ORDER = [
    "stock_basic",
    "stock_company",
    "namechange",
    "index_classify",
    "index_member_all",
    "ths_index",
    "ths_member",
    "dc_index",
    "dc_member",
    "fina_mainbz_vip",
    "income_vip",
    "cashflow_vip",
    "fina_indicator_vip",
    "top10_holders",
]


def fixture_response(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def make_connector(api_name: str, transport, **config_overrides):
    settings = make_settings()
    from src.connectors.datasets import load_datasets

    config = load_datasets()[api_name]
    if config_overrides:
        config = dataclasses.replace(config, **config_overrides)
    return TushareConnector(config, settings=settings, token="std-token", transport=transport)


def ingest_fixture(uow_factory: UnitOfWorkFactory, api_name: str) -> None:
    """用 #3 的采集管道把 Fixture 装入 Raw 层（狗粮：真实链路）。"""
    transport = FixtureTransport(FIXTURE_DIR)
    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name=api_name, trace_id=f"seed-{api_name}-{uuid.uuid4().hex[:6]}",
            lease_owner="test-seeder", lease_ttl_seconds=LEASE_TTL,
        )
    outcome = ingest_dataset(
        uow_factory, make_connector(api_name, transport),
        run_id=run["id"], lease_ttl_seconds=LEASE_TTL,
    )
    assert outcome.status in (IngestRunStatus.SUCCEEDED, IngestRunStatus.PARTIAL_SUCCESS)


def begin_norm(uow_factory: UnitOfWorkFactory, dataset: str, **kwargs):
    with uow_factory.transaction() as uow:
        run = start_normalization_run(
            uow, dataset_name=dataset,
            trace_id=f"norm-{dataset}-{uuid.uuid4().hex[:6]}",
            lease_owner="test-normalizer", lease_ttl_seconds=LEASE_TTL,
            **kwargs,
        )
    return run


def normalize(uow_factory: UnitOfWorkFactory, dataset: str, **start_kwargs):
    run = begin_norm(uow_factory, dataset, **start_kwargs)
    outcome = normalize_dataset(
        uow_factory, dataset, run_id=run["id"], lease_ttl_seconds=LEASE_TTL
    )
    return run, outcome


def count(uow_factory, sql: str, params: tuple = ()) -> int:
    with uow_factory.transaction() as uow:
        return uow._conn.execute(sql, params).fetchone()[0]  # noqa: SLF001


def _isolated_security(uow_factory) -> str:
    """FY 场景专用：自建隔离证券并注册指标，返回 security id（字符串形式避免精度问题）。"""
    with uow_factory.transaction() as uow:
        conn = uow._conn  # noqa: SLF001
        exchange = conn.execute(
            "INSERT INTO master.exchange (code, name) "
            "VALUES (%s, 'TEST') ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id",
            ("TSE",),
        ).fetchone()[0]
        ts_code = f"{uuid.uuid4().hex[:6]}.SH"
        security = conn.execute(
            "INSERT INTO master.security (ts_code, symbol, name, exchange_id, status) "
            "VALUES (%s, %s, %s, %s, 'ACTIVE') RETURNING id",
            (ts_code, ts_code[:6], "FY测试证券", exchange),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO finance.financial_metric "
            "(metric_code, name, statement_type, unit_type, default_aggregation) "
            "VALUES ('NET_CF_OPERATING', '经营活动现金流', 'CASHFLOW', 'CNY', 'SUM') "
            "ON CONFLICT (metric_code) DO NOTHING"
        )
        raw_record = conn.execute(
            "INSERT INTO ops.ingest_run (dataset_name, source_system, trace_id) "
            "VALUES ('test', 'TEST', 't') RETURNING id"
        ).fetchone()[0]
    return security, raw_record


def _insert_observation(uow_factory, security, raw_record, year, value, *,
                        announced="2025-04-30", update_flag=None) -> None:
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            """
            INSERT INTO finance.financial_observation
                (security_id, metric_code, period_end, report_type, value, currency,
                 announced_at, update_flag, source_record_id)
            SELECT %s, 'NET_CF_OPERATING', %s, '1', %s, 'CNY', %s, %s, id
            FROM raw.source_record LIMIT 1
            """,
            (security, f"{year}-12-31", value, announced, update_flag),
        )


@pytest.fixture(scope="module")
def standardized(main_dsn):
    """模块级种子：全部数据集 Raw → 标准（一次装填，多个断言测试复用）。"""
    from src.db.uow import UnitOfWorkFactory as _Factory

    uow_factory = _Factory(main_dsn)
    for api in DATASET_ORDER:
        ingest_fixture(uow_factory, api)
        run, outcome = normalize(uow_factory, api)
        assert outcome.rows_rejected == 0, (
            f"{api} 标准化有拒绝行: {outcome.rows_rejected}"
        )
        assert outcome.status == IngestRunStatus.SUCCEEDED
    yield uow_factory


def test_full_pipeline_generates_all_standard_entities(standardized) -> None:
    uow_factory = standardized
    assert count(uow_factory, "SELECT count(*) FROM master.security") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.exchange") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.company") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.company_security") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.company_profile") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.security_name_history") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.industry") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.company_industry_membership") == 2
    assert count(uow_factory, "SELECT count(*) FROM master.concept") == 4  # THS×2 + DC×2
    assert count(uow_factory, "SELECT count(*) FROM master.company_concept_membership") >= 2
    assert count(uow_factory, "SELECT count(*) FROM finance.business_segment_observation") == 2
    assert count(uow_factory, "SELECT count(*) FROM finance.financial_observation") >= 10
    assert count(uow_factory, "SELECT count(*) FROM fact.holding_observation") == 2


def test_security_company_relation_traceable_to_raw(standardized) -> None:
    uow_factory = standardized
    with uow_factory.transaction() as uow:
        row = uow._conn.execute(  # noqa: SLF001
            """
            SELECT s.ts_code, s.source_record_id, cs.source_record_id AS link_source
            FROM master.security s
            JOIN master.company_security cs ON cs.security_id = s.id
            LIMIT 1
            """
        ).fetchone()
        assert row[0].endswith((".SH", ".SZ", ".BJ"))
        assert row[1] is not None  # 证券可追溯 Raw
        assert row[2] is not None  # 发行关系可追溯 Raw


def test_companies_same_name_not_merged(uow_factory) -> None:
    """同名不同证券的两家公司不得合并（受控复合键）。"""
    # 手工 Raw 种子：同名公司、不同 ts_code
    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="stock_company", source_system="TUSHARE", trace_id="t-merge"
        )
        for ts_code in ("000099.SZ", "600099.SH"):
            payload = {"ts_code": ts_code, "name": "同名测试公司"}
            import hashlib

            payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            uow.source_records.insert_idempotent(
                source_system="TUSHARE", api_name="stock_company",
                payload_hash=payload_hash, raw_payload=payload, ingest_run_id=run["id"],
                source_key=ts_code,
            )
        for ts_code in ("000099.SZ", "600099.SH"):
            uow.source_records.insert_idempotent(
                source_system="TUSHARE", api_name="stock_basic",
                payload_hash=hashlib.sha256(
                    json.dumps({"ts_code": ts_code, "symbol": ts_code[:6],
                                "name": "同名测试公司"}, sort_keys=True).encode()
                ).hexdigest(),
                raw_payload={"ts_code": ts_code, "symbol": ts_code[:6],
                             "name": "同名测试公司", "list_status": "L"},
                ingest_run_id=run["id"], source_key=ts_code,
            )

    normalize(uow_factory, "stock_basic")
    normalize(uow_factory, "stock_company")

    assert count(
        uow_factory,
        "SELECT count(*) FROM master.company c "
        "JOIN master.company_security cs ON cs.company_id = c.id "
        "WHERE c.canonical_name = '同名测试公司'",
    ) == 2  # 同名两家公司，独立存在
    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT unified_social_credit_code FROM master.company "
            "WHERE canonical_name = '同名测试公司'"
        ).fetchall()
        # 受控占位互不相同，绝不按简称合并
        assert len({r[0] for r in rows}) == 2


def test_concept_memberships_carry_platform_and_snapshot(uow_factory) -> None:
    """概念成员必须带来源平台与快照日期；不产生任何经营 Claim。"""
    ingest_fixture(uow_factory, "stock_basic")
    ingest_fixture(uow_factory, "stock_company")
    ingest_fixture(uow_factory, "ths_index")
    ingest_fixture(uow_factory, "ths_member")
    normalize(uow_factory, "stock_basic")
    normalize(uow_factory, "stock_company")
    normalize(uow_factory, "ths_index")
    normalize(uow_factory, "ths_member")

    with uow_factory.transaction() as uow:
        row = uow._conn.execute(  # noqa: SLF001
            """
            SELECT cm.snapshot_date, c.source_platform
            FROM master.company_concept_membership cm
            JOIN master.concept c ON c.id = cm.concept_id
            LIMIT 1
            """
        ).fetchone()
        assert row[0] is not None
        assert row[1] == "THS"
    # 红线：概念绝不生成经营 Claim
    assert count(uow_factory, "SELECT count(*) FROM fact.claim") == 0


def test_business_segment_preserves_raw_bz_item(standardized) -> None:
    uow_factory = standardized
    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT raw_segment_name, segment_type, revenue, currency, mapping_status "
            "FROM finance.business_segment_observation"
        ).fetchall()
    for raw_name, seg_type, revenue, _currency, mapping_status in rows:
        assert raw_name in ("机器人精密减速器", "伺服系统")  # 原始披露口径原样保留
        assert seg_type == "PRODUCT"
        assert revenue is not None
        assert mapping_status == "UNMAPPED"  # 产品语义映射不越权


def test_financial_restatement_and_scope_retained(uow_factory) -> None:
    """重述/口径均保留；当前值按明示规则选择，不覆盖历史。"""
    ingest_fixture(uow_factory, "stock_basic")
    normalize(uow_factory, "stock_basic")
    security = None
    with uow_factory.transaction() as uow:
        security = uow._conn.execute(  # noqa: SLF001
            "SELECT id FROM master.security WHERE ts_code = '000001.SZ'"
        ).fetchone()[0]

    with uow_factory.transaction() as uow:
        # 原始公告（旧值）+ 重述（新公告日期）同财年同口径
        uow._conn.execute(
            """
            INSERT INTO finance.financial_observation
                (security_id, metric_code, period_end, report_type, value, currency,
                 announced_at, update_flag, source_record_id)
            SELECT %s, 'NET_CF_OPERATING', '2024-12-31', '1', 100.0, 'CNY',
                   '2025-04-30', NULL, id FROM raw.source_record LIMIT 1
            """,
            (security,),
        )
        uow._conn.execute(
            """
            INSERT INTO finance.financial_observation
                (security_id, metric_code, period_end, report_type, value, currency,
                 announced_at, update_flag, source_record_id)
            SELECT %s, 'NET_CF_OPERATING', '2024-12-31', '1', 333.0, 'CNY',
                   '2026-04-25', '1', id FROM raw.source_record LIMIT 1
            """,
            (security,),
        )

        result = recent_three_fy_operating_cashflow(uow, security_id=security)
        assert result["policy"]["retain_all_versions"] is True
        # 当前值取最新公告的重述值
        matched = [
            fy for fy in result["fiscal_years"] if fy["period_end"] == "2024-12-31"
        ]
        assert matched and matched[0]["value"] == 333.0

    # 全部版本保留：原始旧值仍可按 known_at 查询
    with uow_factory.transaction() as uow:
        kept = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM finance.financial_observation "
            "WHERE security_id = %s AND metric_code = 'NET_CF_OPERATING' "
            "AND period_end = '2024-12-31'",
            (security,),
        ).fetchone()[0]
    assert kept == 2


@pytest.mark.parametrize(
    "years,expected_sufficient",
    [((2022, 2023, 2024), True), ((2023, 2024), False)],
)
def test_three_fy_cashflow_sufficiency(uow_factory, years, expected_sufficient) -> None:
    security, _ = _isolated_security(uow_factory)
    for year in years:
        _insert_observation(uow_factory, security, None, year, 100.0)

    with uow_factory.transaction() as uow:
        result = recent_three_fy_operating_cashflow(uow, security_id=security)
    assert result["sufficient"] is expected_sufficient
    assert len(result["fiscal_years"]) == min(3, len(years))
    if not expected_sufficient:
        assert result["reason"] == "insufficient_complete_fiscal_years"


def test_null_value_year_is_not_a_valid_fy(uow_factory) -> None:
    """含 NULL 值的财年不算有效观察（缺失≠0）。"""
    security, _ = _isolated_security(uow_factory)
    for year, value in ((2022, 100.0), (2023, 120.0), (2024, None)):
        _insert_observation(uow_factory, security, None, year, value)

    with uow_factory.transaction() as uow:
        result = recent_three_fy_operating_cashflow(uow, security_id=security)
    assert result["sufficient"] is False  # 2024 为 NULL，不伪装成 0 或有效年


def test_restandardize_same_raw_twice_no_duplicates(standardized) -> None:
    """同一批 Raw 重复标准化：主数据/Observation 不增。"""
    uow_factory = standardized
    before = {
        table: count(uow_factory, f"SELECT count(*) FROM {table}")
        for table in (
            "master.security", "master.company", "master.company_security",
            "master.company_concept_membership", "finance.business_segment_observation",
            "finance.financial_observation", "fact.holding_observation",
        )
    }
    for dataset in DATASET_ORDER:
        normalize(uow_factory, dataset)

    for table, value in before.items():
        assert count(uow_factory, f"SELECT count(*) FROM {table}") == value, table


def test_concept_snapshot_crash_leaves_no_partial_snapshot(uow_factory, monkeypatch) -> None:
    """同快照日期的两条成员记录：中途崩溃 → 该快照零行（不暴露半个快照）。"""
    ingest_fixture(uow_factory, "stock_basic")
    ingest_fixture(uow_factory, "stock_company")
    ingest_fixture(uow_factory, "ths_index")
    normalize(uow_factory, "stock_basic")
    normalize(uow_factory, "stock_company")
    normalize(uow_factory, "ths_index")

    # 手工 Raw：同一快照日期两条成员记录
    import hashlib

    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="ths_member", source_system="TUSHARE", trace_id="t-snap"
        )
        for ts_code in ("000001.SZ", "600519.SH"):
            payload = {"ts_code": ts_code, "con_code": "883911", "in_date": "20260101"}
            uow.source_records.insert_idempotent(
                source_system="TUSHARE", api_name="ths_member",
                payload_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                raw_payload=payload, ingest_run_id=run["id"], source_key=ts_code,
            )

    processors = build_processors()
    original_process = processors["ths_member"].process
    calls = {"n": 0}

    def exploding(self, ctx, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-snapshot")
        return original_process(ctx, record)

    monkeypatch.setattr(type(processors["ths_member"]), "process", exploding)
    run = begin_norm(uow_factory, "ths_member")
    with pytest.raises(RuntimeError):
        normalize_dataset(
            uow_factory, "ths_member", run_id=run["id"],
            lease_ttl_seconds=LEASE_TTL, processor=processors["ths_member"],
        )

    assert count(
        uow_factory,
        "SELECT count(*) FROM master.company_concept_membership cm "
        "JOIN master.concept c ON c.id = cm.concept_id "
        "WHERE c.source_platform = 'THS' AND cm.snapshot_date = '2026-01-01'",
    ) == 0  # 半个快照不可见

    # 水位未推进 → 重跑整组原子提交
    monkeypatch.setattr(type(processors["ths_member"]), "process", original_process)
    outcome = normalize_dataset(
        uow_factory, "ths_member", run_id=run["id"],
        lease_ttl_seconds=LEASE_TTL, processor=processors["ths_member"],
    )
    assert outcome.status == IngestRunStatus.SUCCEEDED
    assert count(
        uow_factory,
        "SELECT count(*) FROM master.company_concept_membership cm "
        "JOIN master.concept c ON c.id = cm.concept_id "
        "WHERE c.source_platform = 'THS' AND cm.snapshot_date = '2026-01-01'",
    ) == 2


def test_rejected_rows_recorded_and_run_is_partial(uow_factory, monkeypatch) -> None:
    """无法解析的日期进拒绝事件（不静默纠正）；运行只能 PARTIAL_SUCCESS。"""
    ingest_fixture(uow_factory, "stock_basic")
    normalize(uow_factory, "stock_basic")

    import hashlib

    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="namechange", source_system="TUSHARE", trace_id="t-reject"
        )
        payload = {"ts_code": "000001.SZ", "name": "坏日期记录", "start_date": "31/12/2025"}
        uow.source_records.insert_idempotent(
            source_system="TUSHARE", api_name="namechange",
            payload_hash=hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
            raw_payload=payload, ingest_run_id=run["id"], source_key="000001.SZ",
        )

    norm_run, outcome = normalize(uow_factory, "namechange")
    assert outcome.status == IngestRunStatus.PARTIAL_SUCCESS
    assert outcome.rows_rejected >= 1

    with uow_factory.transaction() as uow:
        events = NormalizationEventRepository(uow._conn).list_rejections(  # noqa: SLF001
            run_id=norm_run["id"]
        )
    assert any("unparseable date" in e["detail"]["reason"] for e in events)
    # 版本事件可审计
    with uow_factory.transaction() as uow:
        run_events = NormalizationEventRepository(uow._conn).list_for_run(  # noqa: SLF001
            norm_run["id"]
        )
    version_events = [e for e in run_events if e["event_type"] == "VERSION_APPLIED"]
    assert version_events
    assert version_events[0]["detail"]["mapping_version"] == mapping_version()


def test_watermark_resume_processes_only_new_records(standardized) -> None:
    uow_factory = standardized
    _, first = normalize(uow_factory, "stock_basic", replay_from_scratch=True)
    assert first.rows_read >= 2  # 显式全量重放（映射回退重建场景）
    _, second = normalize(uow_factory, "stock_basic")
    assert second.rows_read == 0  # 默认续传：水位之后无新记录

    # 追加一条新 Raw → 重跑只处理新增
    import hashlib

    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="stock_basic", source_system="TUSHARE", trace_id="t-wm"
        )
        payload = {"ts_code": "300750.SZ", "symbol": "300750",
                   "name": "新增公司", "list_status": "L"}
        uow.source_records.insert_idempotent(
            source_system="TUSHARE", api_name="stock_basic",
            payload_hash=hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
            raw_payload=payload, ingest_run_id=run["id"], source_key="300750.SZ",
        )
    _, third = normalize(uow_factory, "stock_basic")
    assert third.rows_read == 1
    assert count(
        uow_factory, "SELECT count(*) FROM master.security WHERE ts_code = '300750.SZ'"
    ) == 1


def test_concurrent_normalization_locks_or_reuses(uow_factory) -> None:
    uow_a = uow_factory.open()
    start_normalization_run(
        uow_a, dataset_name="stock_basic", trace_id="norm-a",
        lease_owner="worker-a", lease_ttl_seconds=LEASE_TTL,
    )
    uow_b = uow_factory.open()
    with pytest.raises(ActiveNormalizationExistsError):
        start_normalization_run(
            uow_b, dataset_name="stock_basic", trace_id="norm-b",
            lease_owner="worker-b", lease_ttl_seconds=LEASE_TTL,
        )
    uow_b.rollback()
    uow_b.close()
    uow_a.commit()
    uow_a.close()
    # 测试自净：取消遗留的活跃运行，避免阻塞后续测试的租约守卫
    with uow_factory.transaction() as uow:
        row = uow._conn.execute(  # noqa: SLF001
            "SELECT id FROM ops.normalization_run "
            "WHERE dataset_name = 'stock_basic' AND status = 'RUNNING' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row:
            NormalizationRunRepository(uow._conn).transition(  # noqa: SLF001
                row[0], IngestRunStatus.CANCELLED.value, finished_at=None
            )


def test_stale_normalization_recovered_by_lease(uow_factory) -> None:
    run = begin_norm(uow_factory, "stock_basic")
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.normalization_run SET lease_expires_at = now() - interval '1 min' "
            "WHERE id = %s",
            (run["id"],),
        )
    recovered = recover_stale_normalizations(uow_factory)
    assert any(item["run_id"] == run["id"] for item in recovered)
    with uow_factory.transaction() as uow:
        detail = NormalizationRunRepository(uow._conn).get(run["id"])  # noqa: SLF001
    assert detail["status"] == "FAILED_RETRYABLE"
    # 断点续跑：重试运行继承水位并显式关联父运行
    retry = begin_norm(
        uow_factory, "stock_basic", parent_run_id=run["id"],
        initial_watermark=detail["watermark"],
    )
    assert str(retry["parent_run_id"]) == str(run["id"])


def test_holds_only_no_controls_claims(standardized) -> None:
    """股东数据只产生 HOLDS 观察候选；绝不产生 CONTROLS 或任何 Claim。"""
    uow_factory = standardized
    assert count(uow_factory, "SELECT count(*) FROM fact.holding_observation") == 2
    assert count(uow_factory, "SELECT count(*) FROM fact.claim") == 0


def test_standard_vs_raw_reconciliation_sampling(standardized) -> None:
    """标准层与 Raw 层抽样对账：证券 ts_code 集合与 Raw 载荷一致。"""
    uow_factory = standardized
    with uow_factory.transaction() as uow:
        raw_codes = {
            row[0]
            for row in uow._conn.execute(  # noqa: SLF001
                "SELECT DISTINCT raw_payload->>'ts_code' FROM raw.source_record "
                "WHERE api_name = 'stock_basic'"
            ).fetchall()
        }
        standard_codes = {
            row[0]
            for row in uow._conn.execute(  # noqa: SLF001
                "SELECT ts_code FROM master.security"
            ).fetchall()
        }
    # 对账方向：Raw 中每只证券都必须已进入标准层（标准层可能因其他测试更多）
    assert raw_codes <= standard_codes
    assert len(raw_codes) >= 2


def test_ingest_request_count_after_midrun_permission_failure(uow_factory) -> None:
    """审查遗留 MINOR 修复回归：首批成功 + 第 2 批权限失败 → DB 计数恰为 2。"""
    ingest_fixture(uow_factory, "stock_basic")  # Raw 已有 2 行

    pages = [fixture_response("stock_basic"),
             fixture_response("error_no_permission")]

    def paged(request):
        offset = (request.get("params") or {}).get("offset", 0)
        return pages[0] if offset in (None, 0) else pages[1]

    connector = make_connector("stock_basic", paged, page_size=2, paginated=True)
    run = None
    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name="stock_basic", trace_id="t-count",
            lease_owner="test", lease_ttl_seconds=LEASE_TTL,
        )
    outcome = ingest_dataset(
        uow_factory, connector, run_id=run["id"], lease_ttl_seconds=LEASE_TTL
    )
    assert outcome.status == IngestRunStatus.FAILED_FINAL
    with uow_factory.transaction() as uow:
        detail = uow.ingest_runs.get(run["id"])
    assert detail["request_count"] == 2  # 1 成功批 + 1 失败调用，不多不少


def test_admin_normalization_and_financial_endpoints(standardized, main_dsn) -> None:
    """管理接口可查询标准化运行/事件/财务当前值与近三年现金流（含策略输出）。"""
    from apps.api.app import create_app
    from fastapi.testclient import TestClient
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
    client = TestClient(app)

    runs = client.get("/admin/normalization-runs", params={"limit": 200})
    assert runs.status_code == 200
    body = runs.json()
    assert body["count"] >= len(DATASET_ORDER)
    sample = body["runs"][0]
    assert sample["mapping_version"].startswith("tushare-mappings-")

    detail = client.get(f"/admin/normalization-runs/{sample['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert any(e["event_type"] == "VERSION_APPLIED" for e in detail_body["events"])

    rejections = client.get("/admin/normalization/rejections", params={"limit": 10})
    assert rejections.status_code == 200
    assert "rejections" in rejections.json()

    security_id = None
    with standardized.transaction() as uow:
        security_id = uow._conn.execute(  # noqa: SLF001
            "SELECT id FROM master.security WHERE ts_code = '000001.SZ'"
        ).fetchone()[0]

    current = client.get(f"/admin/financial/current/{security_id}")
    assert current.status_code == 200
    assert current.json()["policy"]["retain_all_versions"] is True

    cashflow = client.get(f"/admin/financial/operating-cashflow/{security_id}")
    assert cashflow.status_code == 200
    cashflow_body = cashflow.json()
    assert "sufficient" in cashflow_body
    assert "policy" in cashflow_body

    missing = client.get("/admin/normalization-runs/" + "0" * 32)
    assert missing.status_code == 404
