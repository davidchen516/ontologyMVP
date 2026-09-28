"""healthz/readyz 语义测试（单元层，探针打桩）。"""

from __future__ import annotations

import structlog
from apps.api.app import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr
from src.core import health as health_module
from src.core.errors import AppError

from tests.helpers import make_settings

SECRET = "unit-test-password"


def build_client(**overrides: object) -> TestClient:
    return TestClient(create_app(make_settings(**overrides)))


def _patch_probes(monkeypatch, postgres, neo4j):
    monkeypatch.setattr(health_module, "probe_postgres", postgres)
    monkeypatch.setattr(health_module, "probe_neo4j", neo4j)


def test_healthz_only_means_process_alive_even_if_all_deps_fail(monkeypatch):
    """healthz 不探测依赖：核心依赖全部失败时仍返回 OK。"""
    _patch_probes(
        monkeypatch,
        lambda *a, **k: health_module.ComponentStatus(
            status="FAIL", error_type="OperationalError", detail="down"
        ),
        lambda *a, **k: health_module.ComponentStatus(
            status="FAIL", error_type="ServiceUnavailable", detail="down"
        ),
    )
    response = build_client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_readyz_ok_when_core_and_capabilities_available(monkeypatch):
    _patch_probes(
        monkeypatch,
        lambda *a, **k: health_module.ComponentStatus(status="OK"),
        lambda *a, **k: health_module.ComponentStatus(status="OK"),
    )
    client = build_client(tushare_token=SecretStr("t"), llm_api_key=SecretStr("k"))
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["components"]["postgres"]["status"] == "OK"
    assert body["components"]["neo4j"]["status"] == "OK"
    assert body["capabilities"] == {
        "tushare": {"status": "OK"},
        "llm": {"status": "OK"},
    }
    # 响应携带与响应头一致的 trace_id
    assert body["trace_id"] == response.headers["x-trace-id"]


def test_readyz_degraded_when_optional_capability_missing(monkeypatch):
    """可选能力缺失 → DEGRADED（200），能力标记 UNAVAILABLE，不伪装可用。"""
    _patch_probes(
        monkeypatch,
        lambda *a, **k: health_module.ComponentStatus(status="OK"),
        lambda *a, **k: health_module.ComponentStatus(status="OK"),
    )
    response = build_client().get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert body["capabilities"]["tushare"]["status"] == "UNAVAILABLE"
    assert body["capabilities"]["llm"]["status"] == "UNAVAILABLE"


def test_readyz_fails_503_with_non_sensitive_diagnostics_when_core_down(monkeypatch):
    _patch_probes(
        monkeypatch,
        lambda *a, **k: health_module.ComponentStatus(
            status="FAIL",
            error_type="OperationalError",
            detail=f"connection refused at postgres (password={SECRET})",
        ),
        lambda *a, **k: health_module.ComponentStatus(status="OK"),
    )
    response = build_client().get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "FAIL"
    assert body["components"]["postgres"]["status"] == "FAIL"
    assert body["components"]["postgres"]["error_type"] == "OperationalError"
    # 非敏感诊断：不得包含密码/Token 原文
    assert SECRET not in response.text
    assert "password=***" in body["components"]["postgres"]["detail"]


def test_app_error_returns_structured_error_with_trace_id():
    app = create_app(make_settings())

    @app.get("/_boom")
    def boom() -> None:
        raise AppError("boom for test")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "app_error"
    assert body["error"]["message"] == "boom for test"
    assert body["trace_id"] == response.headers["x-trace-id"]


def test_request_logs_carry_trace_id():
    """每个请求输出结构化 http_request 日志，且带 trace_id。"""
    import io
    import json

    from src.core.logging import configure_logging

    settings = make_settings()
    app = create_app(settings)  # 内部会做一次默认日志配置（stdout）
    buffer = io.StringIO()
    configure_logging(settings, output=buffer)  # 重定向到缓冲后再发请求

    client = TestClient(app)
    client.get("/healthz")

    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    request_logs = [line for line in lines if line.get("event") == "http_request"]
    assert request_logs, "应有 http_request 结构化日志"
    entry = request_logs[-1]
    assert entry["status"] == 200
    assert entry["method"] == "GET"
    assert entry["path"] == "/healthz"
    assert entry["trace_id"]
    assert structlog.is_configured()
