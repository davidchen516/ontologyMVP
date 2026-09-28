"""请求级结构化日志中间件：method/path/status/duration_ms，随 trace_id 输出。"""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status: int | None = None

        async def send_capture_status(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capture_status)
        finally:
            # uvicorn access log 已关闭（--no-access-log），本行是唯一请求日志
            log.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
