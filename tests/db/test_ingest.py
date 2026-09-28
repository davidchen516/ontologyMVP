"""Raw 采集运行器 DB 测试：幂等、断点、租约、取消、熔断、恢复与统计。"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

import pytest
from src.connectors.datasets import load_datasets
from src.connectors.ingest import (
    ActiveRunExistsError,
    DatasetFusedError,
    ingest_dataset,
    recover_stale_runs,
    start_run,
)
from src.connectors.testing import FixtureTransport
from src.connectors.tushare_connectors import TushareConnector
from src.db.uow import UnitOfWorkFactory
from src.domain.enums import IngestRunStatus

from tests.helpers import make_settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tushare"
LEASE_TTL = 600


def fixture_response(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def make_connector(
    api_name: str,
    transport,
    *,
    token="db-test-token",
    page_size=None,
    **settings_overrides,
):
    settings = make_settings(**settings_overrides)
    config = load_datasets()[api_name]
    if page_size is not None:
        # 小页测试：同时启用分页语义
        config = dataclasses.replace(config, page_size=page_size, paginated=True)
    return TushareConnector(config, settings=settings, token=token, transport=transport)


def begin_run(uow_factory: UnitOfWorkFactory, dataset: str, *, cursor=None, parent=None):
    with uow_factory.transaction() as uow:
        run = start_run(
            uow,
            dataset_name=dataset,
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            lease_owner="test-worker",
            lease_ttl_seconds=LEASE_TTL,
            initial_cursor=cursor,
            parent_run_id=parent,
        )
    return run


def test_happy_path_persists_rows_params_hash_signature_cursor(uow_factory) -> None:
    transport = FixtureTransport(FIXTURE_DIR)
    connector = make_connector("stock_basic", transport)
    run = begin_run(uow_factory, "stock_basic")

    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)

    assert outcome.status == IngestRunStatus.SUCCEEDED
    assert outcome.rows_received == 2
    assert outcome.rows_inserted == 2
    assert outcome.request_count == 1

    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(
            "SELECT source_system, api_name, source_key, request_params, payload_hash, "
            "schema_signature, ingest_run_id FROM raw.source_record"
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row[0] == "TUSHARE"
            assert row[1] == "stock_basic"
            assert row[3] and "list_status" in row[3]  # 请求参数落库
            assert row[4] and len(row[4]) == 64  # payload_hash
            assert row[5] and len(row[5]) == 64  # schema_signature
            assert str(row[6]) == str(run["id"])
        detail = uow.ingest_runs.get(run["id"])
        assert detail["status"] == "SUCCEEDED"
        assert detail["rows_inserted"] == 2
        assert detail["cursor_state"]["schema_signature"]


def test_replay_twice_no_duplicate_raw_records(uow_factory) -> None:
    connector = make_connector("stock_basic", FixtureTransport(FIXTURE_DIR))
    run = begin_run(uow_factory, "stock_basic")
    ingest_dataset(uow_factory, connector, run_id=run["id"], lease_ttl_seconds=LEASE_TTL)

    run2 = begin_run(uow_factory, "stock_basic")
    outcome = ingest_dataset(uow_factory, connector, run_id=run2["id"],
                             lease_ttl_seconds=LEASE_TTL)

    with uow_factory.transaction() as uow:
        count = uow._conn.execute("SELECT count(*) FROM raw.source_record").fetchone()[0]
    assert count == 2  # 重放不增加
    assert outcome.rows_inserted == 0


def _paged_transport(api_name: str, pages: list[dict]):
    """按 offset 顺序回放页；offset 超界返回空页。"""
    def transport(request):
        offset = (request.get("params") or {}).get("offset", 0)
        index = offset // 2 if isinstance(offset, int) else 0
        if 0 <= index < len(pages):
            return pages[index]
        return {"code": 0, "msg": "", "data": {"fields": pages[0]["data"]["fields"], "items": []}}
    return transport


def test_pagination_and_cursor_advance(uow_factory) -> None:
    page0 = fixture_response("income_vip")
    page0["data"]["items"] = [["000001.SZ", "20251231", 100.0], ["000001.SZ", "20250930", 90.0]]
    page1 = fixture_response("income_vip")
    page1["data"]["items"] = [["000006.SZ", "20251231", 10.0], ["000007.SZ", "20251231", 20.0]]
    connector = make_connector("income_vip", _paged_transport("income_vip", [page0, page1]),
                               page_size=2)
    run = begin_run(uow_factory, "income_vip")

    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)

    assert outcome.status == IngestRunStatus.SUCCEEDED
    assert outcome.request_count == 3  # 2 页数据 + 1 页空确认结束
    with uow_factory.transaction() as uow:
        count = uow._conn.execute("SELECT count(*) FROM raw.source_record").fetchone()[0]
        detail = uow.ingest_runs.get(run["id"])
    assert count == 4
    assert detail["request_count"] == 3


def test_crash_between_write_and_cursor_leaves_no_duplicates(uow_factory, monkeypatch) -> None:
    """写入批次后、游标更新前崩溃：事务回滚，重放无重复无丢失。"""
    from src.db.repositories import IngestRunRepository

    original = IngestRunRepository.update_counts

    def exploding(self, *args, **kwargs):
        raise RuntimeError("simulated crash after batch write, before cursor commit")

    connector = make_connector("stock_basic", FixtureTransport(FIXTURE_DIR))
    run = begin_run(uow_factory, "stock_basic")

    monkeypatch.setattr(IngestRunRepository, "update_counts", exploding)
    with pytest.raises(RuntimeError):
        ingest_dataset(uow_factory, connector, run_id=run["id"],
                       lease_ttl_seconds=LEASE_TTL)
    monkeypatch.setattr(IngestRunRepository, "update_counts", original)

    # 崩溃后：批次写入与游标同事务 → 全部回滚
    with uow_factory.transaction() as uow:
        count = uow._conn.execute("SELECT count(*) FROM raw.source_record").fetchone()[0]
        status = uow.ingest_runs.get(run["id"])["status"]
    assert count == 0
    assert status == "RUNNING"  # 崩溃不落终态，等待恢复器判定

    # 重放：从持久化游标（仍为空）继续，无重复、不丢批次
    monkeypatch.setattr(IngestRunRepository, "update_counts", original)
    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.SUCCEEDED
    with uow_factory.transaction() as uow:
        count = uow._conn.execute("SELECT count(*) FROM raw.source_record").fetchone()[0]
    assert count == 2


def test_rate_limited_after_retries_is_failed_retryable_no_storm(uow_factory) -> None:
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        return fixture_response("error_rate_limited")

    connector = make_connector(
        "stock_basic",
        transport,
        tushare_backoff_base_seconds=0.0,
        tushare_backoff_cap_seconds=0.0,
    )
    run = begin_run(uow_factory, "stock_basic")

    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.FAILED_RETRYABLE
    assert calls["n"] == connector._client._max_retries + 1  # 有界请求，无风暴
    with uow_factory.transaction() as uow:
        detail = uow.ingest_runs.get(run["id"])
        assert detail["status"] == "FAILED_RETRYABLE"
        assert "RateLimitedError" in detail["error_detail"]["reason"]


def test_permission_failure_is_failed_final_immediately(uow_factory) -> None:
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        return fixture_response("error_no_permission")

    connector = make_connector("stock_basic", transport)
    run = begin_run(uow_factory, "stock_basic")
    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.FAILED_FINAL
    assert calls["n"] == 1  # 权限错误不重试


def test_schema_change_fuses_dataset_and_blocks_next_run(uow_factory) -> None:
    pages = [fixture_response("stock_basic"),
             {"code": 0, "msg": "", "data": {"fields": ["totally_new_layout"], "items": [["x"]]}}]
    connector = make_connector("stock_basic", _paged_transport("stock_basic", pages), page_size=2)
    run = begin_run(uow_factory, "stock_basic")

    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)

    assert outcome.status == IngestRunStatus.FAILED_FINAL
    with uow_factory.transaction() as uow:
        capability = uow.source_capabilities.get_status("TUSHARE", "stock_basic")
    assert capability["status"] == "SCHEMA_CHANGED"

    # 熔断后：拒绝再采集（阻止下游标准化链路）
    with pytest.raises(DatasetFusedError):
        begin_run(uow_factory, "stock_basic")


def test_duplicate_page_stops_as_partial_success(uow_factory) -> None:
    same = fixture_response("stock_basic")
    connector = make_connector("stock_basic", _paged_transport("stock_basic", [same, same]),
                               page_size=2)
    run = begin_run(uow_factory, "stock_basic")
    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.PARTIAL_SUCCESS
    with uow_factory.transaction() as uow:
        detail = uow.ingest_runs.get(run["id"])
    assert "duplicate_page" in detail["error_detail"]["anomalies"]


def test_cancelled_run_makes_no_more_external_requests(uow_factory) -> None:
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        return fixture_response("stock_basic")

    connector = make_connector("stock_basic", transport)
    run = begin_run(uow_factory, "stock_basic")

    # 手动取消（RUNNING → CANCELLED 合法迁移）
    with uow_factory.transaction() as uow:
        uow.ingest_runs.transition(run["id"], IngestRunStatus.CANCELLED, finished_at=None)
    calls_before = calls["n"]

    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.CANCELLED
    assert calls["n"] == calls_before  # 取消后零外部请求


def test_concurrent_start_for_same_dataset_locks_or_reuses(uow_factory) -> None:
    # 第一个事务持锁未提交
    uow_a = uow_factory.open()
    start_run(
        uow_a,
        dataset_name="stock_basic",
        trace_id="trace-a",
        lease_owner="worker-a",
        lease_ttl_seconds=LEASE_TTL,
    )

    # 第二个并发触发：advisory lock 不可得 → 获得明确的锁失败/复用信号
    uow_b = uow_factory.open()
    with pytest.raises(ActiveRunExistsError):
        start_run(
            uow_b,
            dataset_name="stock_basic",
            trace_id="trace-b",
            lease_owner="worker-b",
            lease_ttl_seconds=LEASE_TTL,
        )
    uow_b.rollback()
    uow_b.close()

    uow_a.commit()
    uow_a.close()


def test_stale_running_recovered_by_lease_then_resumable(uow_factory) -> None:
    run = begin_run(uow_factory, "stock_basic")

    # 直接把租约置为过期（模拟崩溃后无人心跳）
    with uow_factory.transaction() as uow:
        uow._conn.execute(
            "UPDATE ops.ingest_run SET lease_expires_at = now() - interval '1 minute' "
            "WHERE id = %s",
            (run["id"],),
        )

    recovered = recover_stale_runs(uow_factory)
    assert any(item["run_id"] == run["id"] for item in recovered)
    with uow_factory.transaction() as uow:
        detail = uow.ingest_runs.get(run["id"])
    assert detail["status"] == "FAILED_RETRYABLE"
    assert detail["error_detail"]["recovered_by_lease"] is True

    # 断点续跑：重试运行显式关联父运行并继承游标
    retry = begin_run(uow_factory, "stock_basic", cursor=detail["cursor_state"],
                      parent=run["id"])
    with uow_factory.transaction() as uow:
        row = uow._conn.execute(
            "SELECT parent_run_id, status FROM ops.ingest_run WHERE id = %s", (retry["id"],)
        ).fetchone()
    assert str(row[0]) == str(run["id"])
    assert row[1] == "RUNNING"


def test_empty_result_is_success_and_not_negative_fact(uow_factory) -> None:
    empty = {"code": 0, "msg": "", "data": {"fields": ["ts_code", "name"], "items": []}}
    connector = make_connector("namechange", lambda req: empty)
    run = begin_run(uow_factory, "namechange")
    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.SUCCEEDED
    assert outcome.rows_received == 0
    with uow_factory.transaction() as uow:
        count = uow._conn.execute("SELECT count(*) FROM raw.source_record").fetchone()[0]
    assert count == 0  # 空结果不产生任何事实，更不是否定事实


def test_rejected_rows_make_run_partial_success(uow_factory) -> None:
    """自然键为空的行被拒绝：计数入 rows_rejected，运行只能 PARTIAL_SUCCESS。"""
    bad = {"code": 0, "msg": "",
           "data": {"fields": ["ts_code", "name", "start_date", "ann_date"],
                    "items": [[None, "改名记录", "20250101", "20250102"]]}}
    connector = make_connector("namechange", lambda req: bad)
    run = begin_run(uow_factory, "namechange")
    outcome = ingest_dataset(uow_factory, connector, run_id=run["id"],
                             lease_ttl_seconds=LEASE_TTL)
    assert outcome.status == IngestRunStatus.PARTIAL_SUCCESS  # 有拒绝行 → 不得 SUCCEEDED
    assert outcome.rows_rejected == 1
    assert outcome.rows_inserted == 0
