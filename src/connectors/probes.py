"""能力探针持久化与批量探测。"""

from __future__ import annotations

import structlog

from src.connectors.ports import ProbeResult, SourceConnector
from src.connectors.tushare_connectors import SOURCE_SYSTEM
from src.db.uow import UnitOfWork, UnitOfWorkFactory

log = structlog.get_logger(__name__)


def persist_probe(uow: UnitOfWork, result: ProbeResult) -> dict:
    """探针结果落库（UPSERT ops.source_capability）。"""
    detail = {
        "error_code": result.error_code,
        "error_message": result.error_message,
        "schema_signature": result.schema_signature,
        **result.metadata,
    }
    row = uow.source_capabilities.upsert(
        source_system=SOURCE_SYSTEM,
        api_name=result.dataset_name,
        status=result.status.value,
        response_latency_ms=result.response_latency_ms,
        detail=detail,
    )
    return row


def probe_connector(uow_factory: UnitOfWorkFactory, connector: SourceConnector) -> ProbeResult:
    """单接口探测并持久化；探针失败不抛异常，全部落为明确状态。"""
    result = connector.probe()
    with uow_factory.transaction() as uow:
        persist_probe(uow, result)
    log.info(
        "capability_probed",
        dataset=result.dataset_name,
        status=result.status.value,
        latency_ms=result.response_latency_ms,
    )
    return result


def probe_all(
    uow_factory: UnitOfWorkFactory, connectors: list[SourceConnector]
) -> list[ProbeResult]:
    return [probe_connector(uow_factory, connector) for connector in connectors]
