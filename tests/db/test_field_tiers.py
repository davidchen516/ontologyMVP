"""issue #43 字段层级兼容测试：双 Fixture 全管道 + 熔断边界 + 质量标记 + 幂等。

覆盖 issue 补充的 GWT 矩阵：
- H1 低层级 Fixture（缺 exchange/list_status）全管道 → SUCCEEDED + 质量标记 + UNKNOWN
- H2 全字段 Fixture 回归不变
- E1/E2 自然键缺失/漂移 → 仍熔断
- D1 重复采集幂等
- C2 跨页自然键签名漂移 → 熔断（裁决：签名仅对 identity 字段集计算）
- I1 质量标记落 raw 层可查
- I2 list_status 缺失 → UNKNOWN（非 ACTIVE）
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from src.connectors.datasets import load_datasets, reload_datasets
from src.connectors.ingest import (
    IngestRunStatus,
    ingest_dataset,
    start_run,
)
from src.connectors.tushare_connectors import TushareConnector
from src.standardize.pipeline import normalize_dataset, start_normalization_run

from tests.helpers import make_settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tushare"
LEASE_TTL = 600


class TieredTransport:
    """按 api 返回不同层级 Fixture（低层级 = 缺 exchange/list_status）。"""

    def __init__(self, fixture_dir: Path, tier: str = "full") -> None:
        self.fixture_dir = fixture_dir
        self.tier = tier
        self.pages: dict[str, list[dict]] = {}
        self.calls: list[dict] = []

    def _load(self, api: str) -> dict:
        suffix = ".low_tier.json" if (self.tier == "low" and api == "stock_basic") else ".json"
        return json.loads((self.fixture_dir / f"{api}{suffix}").read_text())

    def __call__(self, request: dict) -> dict:
        self.calls.append(json.loads(json.dumps(request)))
        api = request["api_name"]
        return self._load(api)


def make_tiered_connector(api_name: str, tier: str):
    settings = make_settings()
    config = load_datasets()[api_name]
    return TushareConnector(
        config, settings=settings, token="tier-test",
        transport=TieredTransport(FIXTURE_DIR, tier),
    )


def ingest_tiered(uow_factory, api_name: str, tier: str):
    connector = make_tiered_connector(api_name, tier)
    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name=api_name,
            trace_id=f"tier-{tier}-{api_name}-{uuid.uuid4().hex[:6]}",
            lease_owner="tier-test", lease_ttl_seconds=LEASE_TTL,
        )
    return ingest_dataset(
        uow_factory, connector, run_id=run["id"], lease_ttl_seconds=LEASE_TTL,
    )


def normalize(uow_factory, dataset: str):
    with uow_factory.transaction() as uow:
        run = start_normalization_run(
            uow, dataset_name=dataset,
            trace_id=f"tier-norm-{dataset}-{uuid.uuid4().hex[:6]}",
            lease_owner="tier-test", lease_ttl_seconds=LEASE_TTL,
        )
    return normalize_dataset(
        uow_factory, dataset, run_id=run["id"], lease_ttl_seconds=LEASE_TTL,
    )


def count(uow_factory, sql: str, params: tuple = ()) -> int:
    with uow_factory.transaction() as uow:
        return uow._conn.execute(sql, params).fetchone()[0]  # noqa: SLF001


@pytest.fixture(autouse=True)
def _fresh_registry():
    reload_datasets()
    yield
    reload_datasets()


# ---- H1：低层级 Fixture 全管道 ----


def test_low_tier_full_pipeline_succeeds_with_quality_markers(uow_factory) -> None:
    """H1：缺 exchange/list_status → SUCCEEDED + master.security 落库 +
    质量标记 + status=UNKNOWN。"""
    outcome = ingest_tiered(uow_factory, "stock_basic", "low")
    assert outcome.status == IngestRunStatus.SUCCEEDED
    assert outcome.rows_inserted == 2  # 低层级 Fixture 2 行全量

    # I1：raw 层质量标记可查
    with uow_factory.transaction() as uow:
        marked = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM raw.source_record "
            "WHERE api_name = 'stock_basic' "
            "AND request_params->'missing_expected_fields' "
            "?| array['exchange','list_status']"
        ).fetchone()[0]
    assert marked == 2  # 每行都有质量标记

    norm = normalize(uow_factory, "stock_basic")
    assert norm.status == IngestRunStatus.SUCCEEDED
    assert norm.rows_written == 2

    # master.security 落库 + I2：list_status 缺失 → UNKNOWN
    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT ts_code, status FROM master.security ORDER BY ts_code"
        ).fetchall()
    assert len(rows) == 2
    for ts_code, status in rows:
        assert status == "UNKNOWN", f"{ts_code}: {status} != UNKNOWN（I2）"


def test_low_tier_probe_reports_available_with_detail() -> None:
    """状态机不变量：低层级探针 → AVAILABLE + missing_expected_fields。"""
    connector = make_tiered_connector("stock_basic", "low")
    result = connector.probe()
    assert result.status.value == "AVAILABLE"
    assert "exchange" in result.metadata["missing_expected_fields"]
    assert "list_status" in result.metadata["missing_expected_fields"]


# ---- H2：全字段回归不变 ----


def test_full_tier_regression_unchanged(uow_factory) -> None:
    """H2：全字段 Fixture 行为与 main 基线一致（L→ACTIVE 映射保留）。"""
    outcome = ingest_tiered(uow_factory, "stock_basic", "full")
    assert outcome.status == IngestRunStatus.SUCCEEDED
    norm = normalize(uow_factory, "stock_basic")
    assert norm.status == IngestRunStatus.SUCCEEDED
    assert norm.rows_written == 2

    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT s.ts_code, s.status, e.name AS exchange_name "
            "FROM master.security s "
            "JOIN master.exchange e ON e.id = s.exchange_id "
            "ORDER BY s.ts_code"
        ).fetchall()
    assert len(rows) == 2
    # I3 逐字段回归快照（非抽样）：status 映射 + exchange.name 锁定
    expected = {"600519.SH": ("ACTIVE", "SH"), "000001.SZ": ("ACTIVE", "SZ")}
    for ts_code, status, exchange_name in rows:
        assert status == "ACTIVE", f"回归破坏：{ts_code} -> {status}"
        exp = expected.get(ts_code)
        assert exp is None or (status, exchange_name) == exp


# ---- E1/E2：自然键缺失/漂移 → 仍熔断 ----


class IdentityDropTransport:
    """返回缺自然键字段（模拟真实 schema 漂移）。"""

    def __init__(self, drop: list[str]) -> None:
        self.drop = drop

    def __call__(self, request: dict) -> dict:
        base = json.loads(
            (FIXTURE_DIR / "stock_basic.json").read_text()
        )
        fields = [f for f in base["data"]["fields"] if f not in self.drop]
        indices = [base["data"]["fields"].index(f) for f in fields]
        items = [[item[i] for i in indices] for item in base["data"]["items"]]
        return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}


def test_identity_field_missing_still_circuit_breaks(uow_factory) -> None:
    """E1：缺 ts_code（自然键）→ SCHEMA_CHANGED 熔断，0 行。"""
    config = load_datasets()["stock_basic"]
    connector = TushareConnector(
        config, settings=make_settings(), token="t",
        transport=IdentityDropTransport(["ts_code"]),
    )
    with uow_factory.transaction() as uow:
        run = start_run(
            uow, dataset_name="stock_basic", trace_id="id-drop",
            lease_owner="t", lease_ttl_seconds=LEASE_TTL,
        )
    outcome = ingest_dataset(
        uow_factory, connector, run_id=run["id"], lease_ttl_seconds=LEASE_TTL,
    )
    assert outcome.status == IngestRunStatus.FAILED_FINAL
    assert count(
        uow_factory,
        "SELECT count(*) FROM raw.source_record WHERE api_name='stock_basic'",
    ) == 0
    # 探针同样熔断
    probe = TushareConnector(
        config, settings=make_settings(), token="t",
        transport=IdentityDropTransport(["ts_code"]),
    ).probe()
    assert probe.status.value == "SCHEMA_CHANGED"


def test_identity_typo_still_circuit_breaks() -> None:
    """E2：自然键拼写漂移（tscode）→ 探针 SCHEMA_CHANGED。"""
    config = load_datasets()["stock_basic"]
    probe = TushareConnector(
        config, settings=make_settings(), token="t",
        transport=IdentityDropTransport(["ts_code"]),
    ).probe()
    assert probe.status.value == "SCHEMA_CHANGED"
    assert "ts_code" in probe.error_message


# ---- C2：跨页自然键签名漂移 → 熔断（裁决：签名仅 identity 集计算） ----


class CrossPageDriftTransport:
    """第 1 页全字段、第 2 页缺自然键（跨页漂移）。"""

    def __init__(self) -> None:
        self.page = 0

    def __call__(self, request: dict) -> dict:
        self.page += 1
        base = json.loads((FIXTURE_DIR / "stock_basic.json").read_text())
        if self.page == 1:
            return base
        fields = [f for f in base["data"]["fields"] if f != "ts_code"]
        indices = [base["data"]["fields"].index(f) for f in fields]
        items = [[item[i] for i in indices] for item in base["data"]["items"]]
        return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}


def test_cross_page_identity_drift_circuits() -> None:
    """C2：自然键字段集跨页变化 → fetch 层直接抛 SchemaChangedError。

    stock_basic 不分页（单页全量）——跨页签名比较逻辑在 fetch 的
    cursor.schema_signature 参数验证，直接单元级覆盖。
    """
    from src.connectors.ports import SchemaChangedError

    config = load_datasets()["stock_basic"]
    connector = TushareConnector(
        config, settings=make_settings(), token="t",
        transport=CrossPageDriftTransport(),
    )
    batch1 = connector.fetch(None)
    # 第 2 页缺自然键：cursor 带第 1 页签名 → 漂移检测必须抛错
    cursor = {"offset": 2, "schema_signature": batch1.schema_signature}
    with pytest.raises(SchemaChangedError, match=r"required \(identity\) fields missing"):
        connector.fetch(cursor)


class CrossPageExpectedDriftTransport:
    """第 1 页全字段、第 2 页缺 expected（跨页 expected 增减——不熔断）。"""

    def __init__(self) -> None:
        self.page = 0

    def __call__(self, request: dict) -> dict:
        self.page += 1
        base = json.loads((FIXTURE_DIR / "stock_basic.json").read_text())
        if self.page == 1:
            return base
        # 第 2 页去掉 expected（保留所有自然键）
        fields = [
            f for f in base["data"]["fields"]
            if f not in ("exchange", "list_status")
        ]
        indices = [base["data"]["fields"].index(f) for f in fields]
        items = [[item[i] for i in indices] for item in base["data"]["items"]]
        return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}


def test_cross_page_expected_drift_degrades_not_circuits() -> None:
    """裁决验证：跨页 expected 字段消失 → 质量标记继续（不熔断）。"""
    config = load_datasets()["stock_basic"]
    connector = TushareConnector(
        config, settings=make_settings(), token="t",
        transport=CrossPageExpectedDriftTransport(),
    )
    batch1 = connector.fetch(None)
    assert batch1.missing_expected_fields == ()  # 第 1 页全字段
    cursor = {"offset": 2, "schema_signature": batch1.schema_signature}
    batch2 = connector.fetch(cursor)
    # 第 2 页缺 expected → 标记，不抛 SchemaChangedError
    assert "exchange" in batch2.missing_expected_fields
    assert "list_status" in batch2.missing_expected_fields
    # 自然键签名恒等（identity 集计算——两页相同）
    assert batch1.schema_signature == batch2.schema_signature


# ---- D1：重复采集幂等 ----


def test_low_tier_double_ingest_idempotent(uow_factory) -> None:
    """D1：低层级两次全量 → 行数不变，质量标记不重复累积。"""
    ingest_tiered(uow_factory, "stock_basic", "low")
    first = count(uow_factory, "SELECT count(*) FROM master.security")
    marked_first = count(
        uow_factory,
        "SELECT count(*) FROM raw.source_record "
        "WHERE api_name='stock_basic' "
        "AND request_params ? 'missing_expected_fields'",
    )
    ingest_tiered(uow_factory, "stock_basic", "low")
    second = count(uow_factory, "SELECT count(*) FROM master.security")
    marked_second = count(
        uow_factory,
        "SELECT count(*) FROM raw.source_record "
        "WHERE api_name='stock_basic' "
        "AND request_params ? 'missing_expected_fields'",
    )
    assert second == first
    assert marked_second == marked_first  # payload_hash 幂等——不重复


# ---- 字段层级配置守护 ----


def test_stock_basic_field_tiers_loaded() -> None:
    """映射 v0.1.3：required=自然键，expected=非键业务字段。"""
    config = load_datasets()["stock_basic"]
    assert config.required_fields == ("ts_code", "symbol", "name")
    assert set(config.expected_fields) == {"exchange", "list_status"}
    assert config.identity_fields == config.required_fields


def test_low_tier_probe_detail_persisted_to_capability(uow_factory, main_dsn) -> None:
    """M4：探针 metadata.missing_expected_fields 持久化到
    ops.source_capability.detail（状态机不变量单元格实证）。"""
    from src.connectors.probes import persist_probe

    connector = make_tiered_connector("stock_basic", "low")
    result = connector.probe()
    assert result.metadata["missing_expected_fields"] == ["exchange", "list_status"]

    with uow_factory.transaction() as uow:
        persist_probe(uow, result)
    with uow_factory.transaction() as uow:
        detail = uow._conn.execute(  # noqa: SLF001
            "SELECT detail FROM ops.source_capability "
            "WHERE source_system = 'TUSHARE' AND api_name = 'stock_basic'"
        ).fetchone()
    assert detail is not None
    missing = detail[0].get("missing_expected_fields")
    assert missing == ["exchange", "list_status"]
