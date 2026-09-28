"""Connector 单元测试：错误分类、限流、退避、脱敏、注册表与 Fixture 契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.connectors.datasets import (
    load_datasets,
    payload_hash_for_row,
    schema_signature_for_fields,
)
from src.connectors.ports import (
    CapabilityStatus,
    NetworkError,
    PermissionDeniedError,
    RateLimitedError,
)
from src.connectors.testing import FixtureTransport, RecordingTransport
from src.connectors.tushare_client import (
    RateLimiter,
    TushareClient,
    classify_tushare_response,
)
from src.connectors.tushare_connectors import TushareConnector

from tests.helpers import make_settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tushare"


def make_client(
    transport, *, token="unit-test-token", settings=None, max_retries=2, sleeper=None
):
    settings = settings or make_settings()
    return TushareClient(
        api_name="stock_basic",
        settings=settings,
        token=token,
        transport=transport,
        max_retries=max_retries,
        sleeper=sleeper if sleeper is not None else (lambda seconds: None),
    )


def fixture_response(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---- 响应分类 ----


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("error_no_permission", ("NO_PERMISSION", "访问权限")),
        ("error_rate_limited", ("RATE_LIMITED", "每分钟")),
        ("error_http_429", ("RATE_LIMITED", "rate")),
        ("error_invalid_request", ("INVALID_REQUEST", "参数错误")),
        ("error_unknown", ("UNKNOWN", "token=***")),
    ],
)
def test_classify_tushare_response(fixture, expected):
    code, message = classify_tushare_response(fixture_response(fixture))
    assert code == expected[0]
    assert expected[1] in message


def test_classify_success_returns_none():
    assert classify_tushare_response(fixture_response("stock_basic")) is None


# ---- 分类后行为：瞬态重试 / 终态立即失败 ----


def test_rate_limited_retries_with_backoff_then_raises():
    sleeps: list[float] = []
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        return fixture_response("error_rate_limited")

    client = make_client(transport, max_retries=3)
    with pytest.raises(RateLimitedError):
        client.call({"list_status": "L"})
    assert calls["n"] == 4  # 1 + 3 次重试，不超过阈值（无请求风暴）
    assert len(sleeps) == 0 or all(s >= 0 for s in sleeps)


def test_backoff_sequence_exponential_with_jitter():
    recorded: list[float] = []

    client = make_client(lambda request: fixture_response("error_http_429"), max_retries=3)
    client._sleep = recorded.append
    with pytest.raises(RateLimitedError):
        client.call({})
    assert len(recorded) == 3
    settings = make_settings()
    base = settings.tushare_backoff_base_seconds
    for i, value in enumerate(recorded):
        expected = min(base * (2**i), settings.tushare_backoff_cap_seconds)
        assert expected <= value <= expected + 1.0  # base*2^i + 抖动(0~1)


def test_permission_error_is_final_no_retry():
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        return fixture_response("error_no_permission")

    client = make_client(transport, max_retries=3)
    with pytest.raises(PermissionDeniedError):
        client.call({})
    assert calls["n"] == 1  # 权限错误不重试


def test_network_exception_wrapped_and_retried():
    calls = {"n": 0}

    def transport(request):
        calls["n"] += 1
        raise ConnectionError(f"connect refused token={ {'x': 'SHOULDNOTAPPEAR'} }")

    client = make_client(transport, max_retries=2)
    with pytest.raises(NetworkError) as excinfo:
        client.call({})
    assert "SHOULDNOTAPPEAR" not in str(excinfo.value)  # 脱敏
    assert calls["n"] == 3


def test_token_never_in_exception_or_log_capture():
    def transport(request):
        # Token 只存在于 HTTP 层闭包，不进入请求字典（更小的泄露面）
        assert "token" not in request
        return {"code": -2002, "msg": "权限不足 token=unit-test-token"}

    import io

    from src.core.logging import configure_logging

    buffer = io.StringIO()
    settings = make_settings()
    configure_logging(settings, output=buffer)
    client = make_client(transport, max_retries=0)
    with pytest.raises(PermissionDeniedError) as excinfo:
        client.call({})
    assert "unit-test-token" not in str(excinfo.value)
    assert "unit-test-token" not in buffer.getvalue()


# ---- 限流器 ----


def test_rate_limiter_spaces_requests_per_minute():
    clock = {"now": 0.0}

    class Sleeper:
        def __init__(self) -> None:
            self.total = 0.0

        def sleep(self, seconds: float) -> None:
            self.total += seconds
            clock["now"] += seconds

    sleeper = Sleeper()
    limiter = RateLimiter(120, clock=lambda: clock["now"])
    import src.connectors.tushare_client as client_mod

    original_sleep = client_mod.time.sleep
    client_mod.time.sleep = sleeper.sleep
    try:
        for _ in range(5):
            limiter.acquire()
    finally:
        client_mod.time.sleep = original_sleep
    # 120/min → 每 0.5s 一个请求；第 5 次应等待约 2s
    assert sleeper.total >= 1.5


# ---- 数据集注册表与 Fixture 契约 ----


def test_registry_loads_from_yaml_with_probe_params():
    datasets = load_datasets()
    assert "stock_basic" in datasets
    assert datasets["stock_basic"].required_fields
    assert datasets["anns_d"].probe_only is True  # 只做权限探测
    assert datasets["stock_basic"].probe_only is False
    assert datasets["income_vip"].paginated is True


@pytest.mark.parametrize("api_name", sorted(load_datasets()))
def test_fixture_contract_every_dataset(api_name):
    """每个注册数据集都有成功 Fixture，且字段覆盖注册表 required_fields。"""
    payload = fixture_response(api_name)
    assert payload["code"] == 0
    fields = payload["data"]["fields"]
    datasets = load_datasets()
    required = datasets[api_name].required_fields
    missing = [f for f in required if f not in fields]
    assert not missing, f"{api_name} fixture 缺少必需字段 {missing}"
    assert len(payload["data"]["items"]) >= 2


def test_schema_signature_and_payload_hash_stable():
    assert schema_signature_for_fields(["a", "b"]) == schema_signature_for_fields(["a", "b"])
    # 字段重复剔除
    assert schema_signature_for_fields(["a", "a", "b"]) == schema_signature_for_fields(["a", "b"])
    row = {"ts_code": "000001.SZ", "name": "平安银行", "value": None}
    assert payload_hash_for_row(row) == payload_hash_for_row(dict(reversed(list(row.items()))))


# ---- 探针分类 ----


def make_connector(transport, *, api_name="stock_basic", token="unit-test-token", config=None):
    settings = make_settings()
    config = config or load_datasets()[api_name]
    return TushareConnector(config, settings=settings, token=token, transport=transport)


def test_probe_token_missing_returns_clear_status():
    connector = make_connector(FixtureTransport(FIXTURE_DIR), token=None)
    result = connector.probe()
    assert result.status == CapabilityStatus.NO_PERMISSION
    assert result.error_code == "TOKEN_MISSING"
    assert result.metadata == {"token_missing": True}


def test_probe_available_records_signature():
    connector = make_connector(FixtureTransport(FIXTURE_DIR))
    result = connector.probe()
    assert result.status == CapabilityStatus.AVAILABLE
    assert result.schema_signature
    assert result.response_latency_ms is not None


def test_probe_separate_permission_for_probe_only_apis():
    def transport_by_api(req):
        return fixture_response("error_no_permission")

    connector = make_connector(transport_by_api, api_name="anns_d")
    result = connector.probe()
    assert result.status == CapabilityStatus.SEPARATE_PERMISSION_REQUIRED


def test_probe_rate_limited_and_network_statuses():
    connector = make_connector(lambda req: fixture_response("error_rate_limited"))
    assert connector.probe().status == CapabilityStatus.RATE_LIMITED
    connector = make_connector(lambda req: (_ for _ in ()).throw(ConnectionError("x")))
    assert connector.probe().status == CapabilityStatus.NETWORK_ERROR


def test_probe_schema_changed_when_required_fields_missing():
    """成功码但字段缺失：SCHEMA_CHANGED，不伪装可用。"""
    raw = {"code": 0, "msg": "", "data": {"fields": ["unrelated_field"], "items": [["x"]]}}
    connector = make_connector(lambda req: raw, api_name="stock_company")
    result = connector.probe()
    assert result.status == CapabilityStatus.SCHEMA_CHANGED
    assert "ts_code" in result.error_message


# ---- Fixture 录制/重放 ----


def test_recording_and_replay_round_trip(tmp_path):
    def inner(request):
        return fixture_response("stock_basic")

    recording = RecordingTransport(inner, tmp_path)
    response = recording({"api_name": "stock_basic", "params": {}})
    assert response["code"] == 0
    recorded_file = tmp_path / "stock_basic.json"
    assert recorded_file.exists()
    replayed = FixtureTransport(tmp_path)({"api_name": "stock_basic", "params": {}})
    assert replayed == fixture_response("stock_basic")
