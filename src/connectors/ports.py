"""Connector 端口层：数据源无关的采集契约（docs/data-sources-and-ingestion.md §4）。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class CapabilityStatus(StrEnum):
    """数据源能力状态（与 ops.source_capability 的 CHECK 一致，7 态）。"""

    AVAILABLE = "AVAILABLE"
    NO_PERMISSION = "NO_PERMISSION"
    SEPARATE_PERMISSION_REQUIRED = "SEPARATE_PERMISSION_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceBatch:
    source_system: str
    dataset_name: str
    request_params: dict[str, Any]
    retrieved_at: dt.datetime
    rows: list[dict[str, Any]]
    schema_signature: str
    # 空 DataFrame / 无数据：rows 为空但结果合法；绝不能解释为"没有数据"的否定事实
    is_empty: bool = False


@dataclass(frozen=True)
class ProbeResult:
    dataset_name: str
    status: CapabilityStatus
    checked_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(tz=dt.UTC))
    response_latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    schema_signature: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    """每个数据集一个独立 Connector；probe 可用性，fetch 按 cursor 拉批次。"""

    dataset_name: str

    def probe(self) -> ProbeResult: ...

    def fetch(self, cursor: dict[str, Any] | None = None) -> SourceBatch: ...

    def next_cursor(self, batch: SourceBatch) -> dict[str, Any] | None: ...


class ConnectorError(Exception):
    """采集过程中的可分类错误；message 必须已脱敏（无 Token/敏感参数）。"""


class PermissionDeniedError(ConnectorError):
    """无权限/独立权限：不可重试，不得进入 FAILED_RETRYABLE。"""


class RateLimitedError(ConnectorError):
    """限流/429：瞬态，按退避策略重试。"""


class NetworkError(ConnectorError):
    """网络/超时：瞬态，按退避策略重试。"""


class SchemaChangedError(ConnectorError):
    """字段布局变化：熔断数据集，停止后续标准化。"""


class TerminalConnectorError(ConnectorError):
    """不可重试且非权限类的终态错误（INVALID_REQUEST / UNKNOWN / SCHEMA_CHANGED）。

    code 保留分类码，供能力探针映射到统一枚举；message 已脱敏。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
