"""探针持久化与管理只读接口 DB 测试。"""

from __future__ import annotations

import json

from apps.api.app import create_app
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from src.connectors.ingest import ingest_dataset, start_run
from src.connectors.probes import probe_all, probe_connector
from src.connectors.tushare_connectors import TushareConnector

from tests.db.test_ingest import FIXTURE_DIR, LEASE_TTL, make_connector
from tests.helpers import make_settings

LEASE_TTL_SECONDS = LEASE_TTL


def settings_for(main_dsn: str):
    params = conninfo_to_dict(main_dsn)
    return make_settings(
        postgres_host=params["host"],
        postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"],
        postgres_user=params["user"],
        postgres_password=params["password"],
    )


def test_probe_connector_persists_sanitized_status(uow_factory) -> None:
    def leaky(request):
        # Fixture 中伪造了 token=SHOULDNOTAPPEAR：探针必须脱敏后落库
        return json.loads(
            (FIXTURE_DIR / "error_unknown.json").read_text(encoding="utf-8")
        )

    connector = make_connector("stock_basic", leaky)
    result = probe_connector(uow_factory, connector)
    assert result.status.value == "UNKNOWN"

    with uow_factory.transaction() as uow:
        capability = uow.source_capabilities.get_status("TUSHARE", "stock_basic")
    assert capability["status"] == "UNKNOWN"
    assert "SHOULDNOTAPPEAR" not in json.dumps(capability["detail"])


def test_probe_all_covers_every_registered_dataset(uow_factory) -> None:
    from src.connectors.datasets import load_datasets
    from src.connectors.testing import FixtureTransport

    settings = make_settings()
    transport = FixtureTransport(FIXTURE_DIR)
    connectors = [
        TushareConnector(config, settings=settings, token="probe-token", transport=transport)
        for config in load_datasets().values()
    ]
    results = probe_all(uow_factory, connectors)

    assert len(results) == len(load_datasets())
    with uow_factory.transaction() as uow:
        stored = {row["api_name"]: row for row in uow.source_capabilities.list_all()}
    for api_name in load_datasets():
        assert api_name in stored
        assert stored[api_name]["status"] == "AVAILABLE"
        assert stored[api_name]["checked_at"] is not None


def test_admin_endpoints_readonly_shape_and_no_secrets(uow_factory, main_dsn) -> None:
    # 种子数据：一次能力探测 + 一次成功采集运行
    probe_connector(uow_factory, make_connector("stock_basic", _fixture_transport()))

    run = _begin_stock_basic_run(uow_factory)
    ingest_dataset(
        uow_factory, make_connector("stock_basic", _fixture_transport()),
        run_id=run["id"], lease_ttl_seconds=LEASE_TTL_SECONDS,
    )

    app = create_app(settings_for(main_dsn))
    client = TestClient(app)

    capabilities = client.get("/admin/capabilities")
    assert capabilities.status_code == 200
    body = capabilities.json()
    assert any(row["api_name"] == "stock_basic" and row["status"] == "AVAILABLE"
               for row in body)
    assert "SHOULDNOTAPPEAR" not in capabilities.text
    assert "db-test-token" not in capabilities.text  # 实际使用的 token 不得出现

    runs = client.get("/admin/ingest-runs", params={"dataset": "stock_basic"})
    assert runs.status_code == 200
    runs_body = runs.json()
    assert runs_body["count"] == 1
    detail_row = runs_body["runs"][0]
    for key in ("id", "dataset_name", "status", "request_count", "rows_received",
                "rows_inserted", "rows_rejected", "cursor_state", "error_detail"):
        assert key in detail_row
    assert detail_row["status"] == "SUCCEEDED"

    detail = client.get(f"/admin/ingest-runs/{detail_row['id']}")
    assert detail.status_code == 200
    assert detail.json()["rows_inserted"] == 2

    missing = client.get(f"/admin/ingest-runs/{'0' * 32}")
    assert missing.status_code == 404

    freshness = client.get("/admin/data-freshness")
    assert freshness.status_code == 200
    assert "stock_basic" in freshness.json()["datasets"]


def test_admin_endpoints_are_readonly(uow_factory, main_dsn) -> None:
    """管理接口只提供 GET：POST/DELETE 不被接受。"""
    app = create_app(settings_for(main_dsn))
    client = TestClient(app)
    assert client.post("/admin/capabilities").status_code == 405
    assert client.delete("/admin/ingest-runs").status_code == 405


def _fixture_transport():
    from src.connectors.testing import FixtureTransport
    return FixtureTransport(FIXTURE_DIR)


def _begin_stock_basic_run(uow_factory):
    import uuid

    with uow_factory.transaction() as uow:
        run = start_run(
            uow,
            dataset_name="stock_basic",
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            lease_owner="admin-test-worker",
            lease_ttl_seconds=LEASE_TTL_SECONDS,
        )
    return run
