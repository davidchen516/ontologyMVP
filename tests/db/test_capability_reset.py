"""issue #44 熔断解除+分类修正测试：状态机/审计/并发/权限/回滚。

GWT 覆盖（issue #44 补充节）：
- H1 正常解除：SCHEMA_CHANGED → reset → 探针 → AVAILABLE + 2 条审计
- E1 缺 reason/confirm → 422 + 0 条状态审计
- E2 不存在的 api_name → 404
- E3 能力非 SCHEMA_CHANGED → 幂等 no-op + 1 条 no-op 审计
- P1 无 key/错 key/开关关 → 401/403/503 + 零状态变化
- Cn1 并发解除 → 均完成、终态一致、无部分状态
- Cr1 探针崩溃 → 停留 reset_pending 不伪装
- Cr2 探针 SCHEMA_CHANGED → 回滚熔断 + 审计记解除验证失败
- B1/B2 端点生产接线回归（审查整改）：真实 transport 注入点可回滚、
  注册表外 api_name 404 先于状态变更、畸形请求体 422
"""

from __future__ import annotations

import hashlib

from apps.api.app import create_app
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from src.connectors.ports import CapabilityStatus, ProbeResult

from tests.helpers import make_settings

OPERATOR_KEY = "test-operator-key-42"
KEY_HASH = hashlib.sha256(OPERATOR_KEY.encode()).hexdigest()
AUTH = {"X-Operator-Key": OPERATOR_KEY}


def admin_client(main_dsn: str, *, enabled: bool = True,
                 token: str | None = None) -> TestClient:
    params = conninfo_to_dict(main_dsn)
    settings = make_settings(
        postgres_host=params["host"], postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"], postgres_user=params["user"],
        postgres_password=params["password"],
        operator_write_enabled=enabled,
        operator_api_key_hashes=KEY_HASH,
        tushare_token=token,
    )
    app = create_app(settings)
    executor = getattr(app.state, "query_graph_executor", None)
    if executor is not None:
        executor.close()
        app.state.query_graph_executor = None
    return TestClient(app)


def seed_capability(uow_factory, api_name="stock_basic",
                    status="SCHEMA_CHANGED"):
    with uow_factory.transaction() as uow:
        uow.source_capabilities.upsert(
            source_system="TUSHARE", api_name=api_name,
            status=status, detail={"error_code": "REQUIRED_FIELDS_MISSING"},
        )


def get_capability(uow_factory, api_name="stock_basic"):
    with uow_factory.transaction() as uow:
        return uow.source_capabilities.get_status("TUSHARE", api_name)


def count_reset_audits(uow_factory, api_name="stock_basic"):
    with uow_factory.transaction() as uow:
        return uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM ops.audit_event "
            "WHERE event_type = 'CAPABILITY_RESET' "
            "AND payload->>'api_name' = %s",
            (api_name,),
        ).fetchone()[0]


# ---- H1：正常解除 ----


def test_reset_succeeds_with_probe_and_audit(uow_factory, main_dsn) -> None:
    """H1：SCHEMA_CHANGED → reset → 探针 AVAILABLE → AVAILABLE + 2 条审计。"""
    seed_capability(uow_factory)

    def probe_fn():
        return ProbeResult(
            dataset_name="stock_basic", status=__import__(
                "src.connectors.ports", fromlist=["CapabilityStatus"]
            ).CapabilityStatus.AVAILABLE,
            metadata={"rows": 2},
        )

    # 直接调 service 层（API 层生产接线回归见下方 B1 测试）
    from src.connectors.capability_reset import reset_capability

    with uow_factory.transaction() as uow:
        result = reset_capability(
            uow, source_system="TUSHARE", api_name="stock_basic",
            actor="operator:test", reason="E2E 解除验证",
            probe_fn=probe_fn, trace_id="t-reset-1",
        )
    assert result["status"] == "AVAILABLE"
    assert result["reset_applied"] is True
    cap = get_capability(uow_factory)
    assert cap["status"] == "AVAILABLE"
    assert cap["detail"].get("reset_verified") is True
    audits = count_reset_audits(uow_factory)
    assert audits == 2  # reset + 探针结果


# ---- E1：缺 reason / confirm ----


def test_reset_missing_reason_or_confirm_422(uow_factory, main_dsn) -> None:
    """E1：缺 reason 或 confirm ≠ true → 422 + 0 条状态审计。"""
    seed_capability(uow_factory)
    client = admin_client(main_dsn)

    r1 = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"confirm": True}, headers=AUTH,
    )
    assert r1.status_code == 422
    r2 = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "x"}, headers=AUTH,
    )
    assert r2.status_code == 422
    r3 = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "x", "confirm": "yes"}, headers=AUTH,
    )
    assert r3.status_code == 422

    cap = get_capability(uow_factory)
    assert cap["status"] == "SCHEMA_CHANGED"  # 不变
    assert count_reset_audits(uow_factory) == 0


# ---- E2：不存在的 api_name ----


def test_reset_nonexistent_404(uow_factory, main_dsn) -> None:
    client = admin_client(main_dsn)
    r = client.post(
        "/admin/capabilities/BOGUS_API/reset",
        json={"reason": "x", "confirm": True}, headers=AUTH,
    )
    assert r.status_code == 404


# ---- E3：幂等 no-op ----


def test_reset_available_is_noop(uow_factory) -> None:
    """E3：能力=AVAILABLE → reset → 幂等 no-op + 1 条审计 + 不触发探针。"""
    seed_capability(uow_factory, status="AVAILABLE")
    from src.connectors.capability_reset import reset_capability

    probe_called = []
    with uow_factory.transaction() as uow:
        result = reset_capability(
            uow, source_system="TUSHARE", api_name="stock_basic",
            actor="op", reason="r",
            probe_fn=lambda: probe_called.append(1) or None,
        )
    assert result["reset_applied"] is False
    assert probe_called == []  # 不触发探针
    assert count_reset_audits(uow_factory) == 1  # no-op 审计


# ---- P1：权限拒绝 ----


def test_reset_auth_failures(uow_factory, main_dsn) -> None:
    """P1：无 key→401 / 错 key→403 / 开关关→503；零状态审计。"""
    seed_capability(uow_factory)
    # 无 key
    c = admin_client(main_dsn)
    assert c.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "x", "confirm": True},
    ).status_code == 401
    # 错 key
    assert c.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "x", "confirm": True},
        headers={"X-Operator-Key": "wrong"},
    ).status_code == 403
    # 开关关
    c_off = admin_client(main_dsn, enabled=False)
    assert c_off.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "x", "confirm": True}, headers=AUTH,
    ).status_code == 503
    assert get_capability(uow_factory)["status"] == "SCHEMA_CHANGED"
    assert count_reset_audits(uow_factory) == 0


# ---- Cn1：并发 ----


def test_reset_concurrent_single_winner(uow_factory, main_dsn) -> None:
    """Cn1：双线程屏障同步并发 reset → 均完成、终态一致、无部分状态。

    两线程都从 SCHEMA_CHANGED 出发（读到对方未提交的事务前状态）；
    无论交错如何，最终都是一致的 AVAILABLE，且每次迁移都有审计。
    """
    seed_capability(uow_factory)
    import threading

    from src.connectors.capability_reset import reset_capability
    from src.db.uow import UnitOfWorkFactory

    def probe_fn():
        return ProbeResult(
            dataset_name="stock_basic", status=CapabilityStatus.AVAILABLE,
        )

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=10)
            factory = UnitOfWorkFactory(main_dsn)
            with factory.transaction() as uow:
                reset_capability(
                    uow, source_system="TUSHARE", api_name="stock_basic",
                    actor="op", reason="并发测试", probe_fn=probe_fn,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert errors == []  # 均完成、无异常
    # 终态一致：双方探针都成功 → AVAILABLE（无部分状态）
    assert get_capability(uow_factory)["status"] == "AVAILABLE"
    # 每次迁移有审计：双解除=4 条，或一先提交另一转 no-op=3 条
    assert count_reset_audits(uow_factory) in (3, 4)


# ---- Cr1/Cr2：探针崩溃 / 探针回滚 ----


def test_reset_probe_crash_stays_pending(uow_factory) -> None:
    """Cr1：探针崩溃 → 停 AVAILABLE+reset_pending 不伪装。"""
    seed_capability(uow_factory)
    from src.connectors.capability_reset import reset_capability

    def probe_fn():
        raise RuntimeError("probe crashed")

    with uow_factory.transaction() as uow:
        result = reset_capability(
            uow, source_system="TUSHARE", api_name="stock_basic",
            actor="op", reason="r", probe_fn=probe_fn,
        )
    assert result["reset_applied"] is True
    cap = get_capability(uow_factory)
    assert cap["status"] == "AVAILABLE"
    assert cap["detail"].get("reset_pending") is True


def test_reset_probe_schema_changed_rolls_back(uow_factory) -> None:
    """Cr2：探针返回 SCHEMA_CHANGED → 回滚熔断 + 审计记解除验证失败。"""
    seed_capability(uow_factory)
    from src.connectors.capability_reset import reset_capability

    def probe_fn():
        return ProbeResult(
            dataset_name="stock_basic",
            status=CapabilityStatus.SCHEMA_CHANGED,
        )

    with uow_factory.transaction() as uow:
        result = reset_capability(
            uow, source_system="TUSHARE", api_name="stock_basic",
            actor="op", reason="r", probe_fn=probe_fn,
        )
    assert result["status"] == "SCHEMA_CHANGED"
    cap = get_capability(uow_factory)
    assert cap["status"] == "SCHEMA_CHANGED"
    # 审查 N3：回滚不抹掉原熔断证据（error_code 来自 seed detail）
    assert cap["detail"].get("error_code") == "REQUIRED_FIELDS_MISSING"
    with uow_factory.transaction() as uow:
        events = uow._conn.execute(  # noqa: SLF001
            "SELECT payload->>'probe_outcome' FROM ops.audit_event "
            "WHERE event_type = 'CAPABILITY_RESET' "
            "AND payload->>'probe_outcome' = 'reset_verification_failed'",
        ).fetchall()
    assert len(events) == 1  # I2


# ---- B1/B2 端点生产接线回归（审查整改） ----


def _stub_transport(raw: dict):
    def transport(request: dict) -> dict:
        return raw

    return transport


def test_reset_endpoint_production_probe_wiring(monkeypatch, uow_factory,
                                                 main_dsn) -> None:
    """B1 回归：端点探针走 http_transport 注入点——可用与回滚路径均可达。

    修复前生产探针为 FixtureTransport 回放（部署镜像无 fixtures →
    永远"崩溃"→ 盲目解除 200）。修复后探针构造真实 transport，测试
    在 transport 边界打桩：SCHEMA_CHANGED 响应必须回滚（I2），成功
    响应才 AVAILABLE。
    """
    from src.connectors import tushare_client

    seed_capability(uow_factory)
    client = admin_client(main_dsn, token="probe-token")

    # 回滚路径：真实 TuShare 返回 -4001 → 回滚 SCHEMA_CHANGED
    monkeypatch.setattr(
        tushare_client, "http_transport",
        lambda base_url, token, timeout: _stub_transport(
            {"code": -4001, "msg": "字段错误: 不支持的参数"}),
    )
    r = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "解除验证", "confirm": True}, headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "SCHEMA_CHANGED"
    assert r.json()["probe_result"]["status"] == "SCHEMA_CHANGED"
    assert get_capability(uow_factory)["status"] == "SCHEMA_CHANGED"

    # 成功路径：布局含全部身份字段 → AVAILABLE + reset_verified
    ok_raw = {"code": 0, "msg": "",
              "data": {"fields": ["ts_code", "symbol", "name", "exchange",
                                   "list_status"],
                       "items": [["000001.SZ", "000001", "平安银行",
                                  "SZ", "L"]]}}
    monkeypatch.setattr(
        tushare_client, "http_transport",
        lambda base_url, token, timeout: _stub_transport(ok_raw),
    )
    r2 = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "解除验证", "confirm": True}, headers=AUTH,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "AVAILABLE"
    cap = get_capability(uow_factory)
    assert cap["status"] == "AVAILABLE"
    assert cap["detail"].get("reset_verified") is True


def test_reset_endpoint_no_token_rolls_back(uow_factory, main_dsn) -> None:
    """B1 语义：无 Token 时探针返回 NO_PERMISSION → 按 I2 回滚，不伪装。"""
    seed_capability(uow_factory)
    client = admin_client(main_dsn, token=None)  # 无 token → 探针 NO_PERMISSION

    r = client.post(
        "/admin/capabilities/stock_basic/reset",
        json={"reason": "解除验证", "confirm": True}, headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["probe_result"]["status"] == "NO_PERMISSION"
    # 未配置 Token 的部署不能解除熔断（回滚而非盲目放行）
    assert get_capability(uow_factory)["status"] == "NO_PERMISSION"


def test_reset_unregistered_dataset_404_before_mutation(uow_factory,
                                                         main_dsn) -> None:
    """B1(e) 回归：注册表外 api_name → 404 先于任何状态变更。

    修复前该请求 200 + 熔断被盲目释放（LookupError 被探针崩溃兜底吞掉）。
    """
    seed_capability(uow_factory, api_name="not_a_real_dataset")
    client = admin_client(main_dsn, token="probe-token")

    r = client.post(
        "/admin/capabilities/not_a_real_dataset/reset",
        json={"reason": "x", "confirm": True}, headers=AUTH,
    )
    assert r.status_code == 404
    cap = get_capability(uow_factory, api_name="not_a_real_dataset")
    assert cap["status"] == "SCHEMA_CHANGED"  # 未被释放
    assert count_reset_audits(uow_factory, api_name="not_a_real_dataset") == 0


def test_reset_malformed_body_422(uow_factory, main_dsn) -> None:
    """B2 回归：畸形/空请求体 → 422（而非 500），零状态变更零审计。"""
    seed_capability(uow_factory)
    client = admin_client(main_dsn)
    url = "/admin/capabilities/stock_basic/reset"

    # 空体 / 非法 JSON / 非对象体 / null / confirm 隐式真值 / confirm=false
    responses = [
        client.post(url, headers=AUTH),  # 无 body
        client.post(url, content=b"{invalid", headers={**AUTH,
                                                       "content-type": "application/json"}),
        client.post(url, json=[1, 2], headers=AUTH),
        client.post(url, json="x", headers=AUTH),
        client.post(url, json=None, headers=AUTH),
        client.post(url, json={"reason": "x", "confirm": "yes"}, headers=AUTH),
        client.post(url, json={"reason": "x", "confirm": False}, headers=AUTH),
    ]
    assert [r.status_code for r in responses] == [422] * 7

    assert get_capability(uow_factory)["status"] == "SCHEMA_CHANGED"
    assert count_reset_audits(uow_factory) == 0


# ---- 配置守护 ----


def test_operator_write_capability_reflects_settings() -> None:
    from src.connectors.operator_auth import operator_write_available

    assert operator_write_available(make_settings()) is False
    assert operator_write_available(make_settings(
        operator_write_enabled=True, operator_api_key_hashes="0" * 64,
    )) is True
