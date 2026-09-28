"""TuShare Connector：probe/fetch/next_cursor（每数据集一个实例）。

错误到能力状态的映射（issue #3 统一枚举）：
- Token 缺失 → NO_PERMISSION（明确状态，绝不伪装可用）
- 权限错误：常规接口 → NO_PERMISSION；probe-only/提示独立权限 → SEPARATE_PERMISSION_REQUIRED
- 限流耗尽 → RATE_LIMITED；网络耗尽 → NETWORK_ERROR
- 字段布局熔断 → SCHEMA_CHANGED
- 其余 → UNKNOWN
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from src.connectors.datasets import (
    DatasetConfig,
    payload_hash_for_row,
    schema_signature_for_fields,
)
from src.connectors.ports import (
    CapabilityStatus,
    ConnectorError,
    NetworkError,
    PermissionDeniedError,
    ProbeResult,
    RateLimitedError,
    SchemaChangedError,
    SourceBatch,
    TerminalConnectorError,
)
from src.connectors.tushare_client import RateLimiter, Transport, TushareClient
from src.core.config import Settings

SOURCE_SYSTEM = "TUSHARE"

SEPARATE_HINTS = ("独立", "单独", "separate", "单独申请")


class TushareConnector:
    def __init__(
        self,
        config: DatasetConfig,
        *,
        settings: Settings,
        token: str | None,
        transport: Transport,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.config = config
        self.dataset_name = config.api_name
        self.settings = settings
        self._client = TushareClient(
            api_name=config.api_name,
            settings=settings,
            token=token,
            transport=transport,
            rate_limiter=rate_limiter,
        )

    # ---- probe ----

    def probe(self) -> ProbeResult:
        if self._client.token_missing:
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=CapabilityStatus.NO_PERMISSION,
                error_code="TOKEN_MISSING",
                error_message="tushare token not configured",
                metadata={"token_missing": True},
            )
        try:
            result = self._client.call(self._probe_params())
        except PermissionDeniedError as exc:
            return self._permission_probe_result(exc)
        except TerminalConnectorError as exc:
            # 非权限类终态错误：按分类码映射，不伪装成权限问题
            status = (
                CapabilityStatus.SCHEMA_CHANGED
                if exc.code == "SCHEMA_CHANGED"
                else CapabilityStatus.UNKNOWN
            )
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=status,
                error_code=exc.code,
                error_message=str(exc),
            )
        except RateLimitedError as exc:
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=CapabilityStatus.RATE_LIMITED,
                error_code="RATE_LIMITED",
                error_message=str(exc),
                metadata={"attempts": self._client._max_retries + 1},
            )
        except NetworkError as exc:
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=CapabilityStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                error_message=str(exc),
            )
        except ConnectorError as exc:
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=CapabilityStatus.UNKNOWN,
                error_code="CONNECTOR_ERROR",
                error_message=str(exc),
            )

        data = result.raw.get("data") or {}
        fields = list(data.get("fields") or [])
        items = data.get("items") or []
        missing = [f for f in self.config.required_fields if f not in fields]
        if missing:
            # 成功码但字段缺失/布局变化：记录签名并按 SCHEMA_CHANGED 上报，不伪装可用
            return ProbeResult(
                dataset_name=self.dataset_name,
                status=CapabilityStatus.SCHEMA_CHANGED,
                error_code="REQUIRED_FIELDS_MISSING",
                error_message=f"required fields missing: {missing}",
                schema_signature=schema_signature_for_fields(fields),
                metadata={"rows": len(items), "attempts": result.attempts},
            )
        return ProbeResult(
            dataset_name=self.dataset_name,
            status=CapabilityStatus.AVAILABLE,
            response_latency_ms=result.latency_ms,
            schema_signature=schema_signature_for_fields(fields),
            metadata={"rows": len(items), "attempts": result.attempts},
        )

    def _probe_params(self) -> dict[str, Any]:
        # probe-only 接口也用最小参数探测权限（不假设可用）
        return dict(self.config.probe_params or {})

    def _permission_probe_result(self, exc: PermissionDeniedError) -> ProbeResult:
        message = str(exc)
        separate = self.config.probe_only or any(h in message for h in SEPARATE_HINTS)
        if "SCHEMA_CHANGED" in message:
            status = CapabilityStatus.SCHEMA_CHANGED
            error_code = "SCHEMA_CHANGED"
        elif separate:
            status = CapabilityStatus.SEPARATE_PERMISSION_REQUIRED
            error_code = "NO_PERMISSION"
        else:
            status = CapabilityStatus.NO_PERMISSION
            error_code = "NO_PERMISSION"
        return ProbeResult(
            dataset_name=self.dataset_name,
            status=status,
            error_code=error_code,
            error_message=message,
        )

    # ---- fetch / cursor ----

    def fetch(self, cursor: dict[str, Any] | None = None) -> SourceBatch:
        cursor = cursor or {}
        params: dict[str, Any] = dict(self.config.probe_params or {})
        if self.config.paginated:
            params["offset"] = cursor.get("offset", 0)
            params["limit"] = self.config.page_size
        result = self._client.call(params)
        data = result.raw.get("data") or {}
        fields = list(data.get("fields") or [])
        items = data.get("items") or []
        rows = [dict(zip(fields, item, strict=False)) for item in items]

        signature = schema_signature_for_fields(fields)
        missing = [f for f in self.config.required_fields if f not in fields]
        if missing:
            raise SchemaChangedError(
                f"{self.dataset_name}: required fields missing {missing} "
                f"(signature={signature[:12]}…)"
            )
        # 同批内字段签名与上批不一致 → 数据集字段布局漂移
        previous_signature = cursor.get("schema_signature")
        if previous_signature and previous_signature != signature:
            raise SchemaChangedError(
                f"{self.dataset_name}: schema signature changed "
                f"{previous_signature[:12]}… -> {signature[:12]}…"
            )

        return SourceBatch(
            source_system=SOURCE_SYSTEM,
            dataset_name=self.dataset_name,
            request_params=params,
            retrieved_at=dt.datetime.now(tz=dt.UTC),
            rows=rows,
            schema_signature=signature,
            is_empty=len(rows) == 0,
        )

    def next_cursor(self, batch: SourceBatch) -> dict[str, Any] | None:
        if not self.config.paginated:
            return None
        if len(batch.rows) < self.config.page_size:
            return None  # 末页
        next_offset = (batch.request_params.get("offset") or 0) + len(batch.rows)
        return {
            "offset": next_offset,
            "schema_signature": batch.schema_signature,
            "last_page_hash": self.page_hash(batch),
        }

    @staticmethod
    def page_hash(batch: SourceBatch) -> str:
        joined = [payload_hash_for_row(row) for row in batch.rows]
        return schema_signature_for_fields(joined) if joined else uuid.uuid4().hex
