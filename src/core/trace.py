"""Trace ID：API 请求与 Worker 执行单元统一携带 trace_id，贯穿结构化日志。

实现为纯 ASGI 中间件（不使用 BaseHTTPMiddleware），保证 contextvar
在端点与请求日志中可见。
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Any

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

# 允许外部通过 X-Trace-Id 透传；仅接受安全字符集，防止日志注入
_TRACE_ID_PATTERN = re.compile(r"^[0-9A-Za-z._-]{1,64}$")

HEADER_NAME = "x-trace-id"


def new_trace_id() -> str:
    """生成新 trace_id。"""
    return uuid.uuid4().hex


def normalize_trace_id(value: str | None) -> str:
    """校验外部传入的 trace_id；非法时生成新值。"""
    candidate = (value or "").strip()
    if candidate and _TRACE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return new_trace_id()


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str) -> Any:
    """设置 trace_id，返回用于 reset 的 token。"""
    return _trace_id.set(trace_id)


def reset_trace_id(token: Any) -> None:
    _trace_id.reset(token)


class TraceIdMiddleware:
    """为每个 HTTP 请求设置 trace_id，并回写 X-Trace-Id 响应头。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming: str | None = None
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == HEADER_NAME:
                incoming = value.decode("latin-1")
                break
        trace_id = normalize_trace_id(incoming)
        token = set_trace_id(trace_id)

        async def send_with_trace_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER_NAME.encode("latin-1"), trace_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_header)
        finally:
            reset_trace_id(token)
