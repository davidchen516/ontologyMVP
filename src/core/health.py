"""依赖健康探测与就绪报告。

语义（issue #1 不变量）：
- healthz=OK 只表示进程存活；
- readyz 中 PostgreSQL、Neo4j、应用配置全部可用才算核心可用；
- 任一核心依赖不可用 → FAIL（HTTP 503），返回非敏感诊断信息；
- 可选能力（TuShare/LLM）缺失 → DEGRADED，能力标记 UNAVAILABLE，不伪装可用。
"""

from __future__ import annotations

from typing import Literal

import psycopg
import structlog
from neo4j import GraphDatabase
from pydantic import BaseModel, field_validator

from src.core.config import Settings
from src.core.logging import sanitize_text

log = structlog.get_logger(__name__)


class ComponentStatus(BaseModel):
    status: Literal["OK", "FAIL"]
    error_type: str | None = None
    detail: str | None = None

    @field_validator("error_type", "detail", mode="before")
    @classmethod
    def _sanitize_diagnostics(cls, value: object) -> object:
        # 边界兜底：无论探针实现是否脱敏，诊断字段进入响应前强制脱敏
        return sanitize_text(str(value), limit=200) if isinstance(value, str) else value


class CapabilityStatus(BaseModel):
    status: Literal["OK", "UNAVAILABLE"]


class ReadinessReport(BaseModel):
    status: Literal["OK", "DEGRADED", "FAIL"]
    components: dict[str, ComponentStatus]
    capabilities: dict[str, CapabilityStatus]


def probe_postgres(dsn: str, *, timeout_seconds: float) -> ComponentStatus:
    try:
        with psycopg.connect(dsn, connect_timeout=int(max(1, timeout_seconds))) as conn:
            conn.execute("SELECT 1")
        return ComponentStatus(status="OK")
    except Exception as exc:  # noqa: BLE001 - 探测器必须吞掉一切连接失败
        return ComponentStatus(
            status="FAIL",
            error_type=type(exc).__name__,
            detail=sanitize_text(str(exc), limit=200),
        )


def probe_neo4j(
    uri: str, user: str, password: str, *, timeout_seconds: float
) -> ComponentStatus:
    driver = None
    try:
        driver = GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=max(1.0, timeout_seconds)
        )
        driver.verify_connectivity()
        return ComponentStatus(status="OK")
    except Exception as exc:  # noqa: BLE001
        return ComponentStatus(
            status="FAIL",
            error_type=type(exc).__name__,
            detail=sanitize_text(str(exc), limit=200),
        )
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响探测结果
                log.warning("neo4j_driver_close_failed")


def build_readiness(settings: Settings) -> ReadinessReport:
    """核心依赖探测 + 可选能力状态汇总。"""
    postgres = probe_postgres(
        settings.postgres_dsn, timeout_seconds=settings.ready_connect_timeout_seconds
    )
    neo4j = probe_neo4j(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
        timeout_seconds=settings.ready_connect_timeout_seconds,
    )
    capabilities = {
        name: CapabilityStatus(status="OK" if available else "UNAVAILABLE")
        for name, available in settings.capability_states().items()
    }

    core_ok = postgres.status == "OK" and neo4j.status == "OK"
    degraded = any(cap.status == "UNAVAILABLE" for cap in capabilities.values())

    if not core_ok:
        overall: Literal["OK", "DEGRADED", "FAIL"] = "FAIL"
    elif degraded:
        overall = "DEGRADED"
    else:
        overall = "OK"

    return ReadinessReport(
        status=overall,
        components={"postgres": postgres, "neo4j": neo4j},
        capabilities=capabilities,
    )
