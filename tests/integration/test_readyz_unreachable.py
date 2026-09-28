"""真实探针 + 不可达端口的就绪集成测试（无需真实 PostgreSQL/Neo4j）。

连接 127.0.0.1:1 立即被拒绝，等价于“断开 PostgreSQL/Neo4j”的故障注入。
"""

from __future__ import annotations

from apps.api.app import create_app
from fastapi.testclient import TestClient
from src.core.health import probe_neo4j, probe_postgres

from tests.helpers import make_settings

DEAD_HOST = "127.0.0.1"
DEAD_PORT = 1
PASSWORD = "unit-test-password"


def test_readyz_fails_when_postgres_and_neo4j_unreachable():
    settings = make_settings(
        postgres_host=DEAD_HOST,
        postgres_port=DEAD_PORT,
        neo4j_uri=f"bolt://{DEAD_HOST}:{DEAD_PORT}",
        ready_connect_timeout_seconds=1,
    )
    client = TestClient(create_app(settings))

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "FAIL"
    assert body["components"]["postgres"]["status"] == "FAIL"
    assert body["components"]["neo4j"]["status"] == "FAIL"
    # 非敏感诊断：有错误类型与脱敏后的描述，但绝无密码/Token 原文
    for name in ("postgres", "neo4j"):
        component = body["components"][name]
        assert component["error_type"]
        assert component["detail"] is not None
    assert PASSWORD not in response.text
    assert "unit-test" not in response.text.replace("unit-test-password", "")
    # 可选能力仍需如实上报（缺失 → UNAVAILABLE），不伪装可用
    assert body["capabilities"]["tushare"]["status"] == "UNAVAILABLE"
    assert body["trace_id"]


def test_healthz_succeeds_while_dependencies_are_down():
    """依赖不可达时 healthz 必须仍然成功（存活 ≠ 就绪）。"""
    settings = make_settings(
        postgres_host=DEAD_HOST,
        postgres_port=DEAD_PORT,
        neo4j_uri=f"bolt://{DEAD_HOST}:{DEAD_PORT}",
    )
    client = TestClient(create_app(settings))
    assert client.get("/healthz").status_code == 200


def test_probe_diagnostics_never_contain_credentials():
    result = probe_postgres(
        f"postgresql://ontology:{PASSWORD}@{DEAD_HOST}:{DEAD_PORT}/ontology",
        timeout_seconds=1,
    )
    assert result.status == "FAIL"
    assert result.error_type
    assert PASSWORD not in (result.detail or "")


def test_probe_neo4j_unreachable_returns_fail_without_credentials():
    result = probe_neo4j(
        f"bolt://{DEAD_HOST}:{DEAD_PORT}", "neo4j", PASSWORD, timeout_seconds=1
    )
    assert result.status == "FAIL"
    assert result.error_type
    assert PASSWORD not in (result.detail or "")
