"""FastAPI 应用工厂（不自动加载配置；测试与入口共用）。

端点语义（issue #1 不变量）：
- /healthz：进程存活，永远不探测依赖；
- /readyz：核心依赖（PostgreSQL/Neo4j）+ 配置可用性；FAIL → 503，
  核心可用但可选能力缺失 → DEGRADED（200），不伪装可用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from src.core.config import Settings
from src.core.errors import AppError
from src.core.health import ReadinessReport, build_readiness
from src.core.logging import configure_logging
from src.core.trace import (
    TraceIdMiddleware,
    get_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

from apps.api.middleware import RequestLoggingMiddleware

log = structlog.get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """应用工厂：测试与生产共用同一构建路径。"""
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        token = set_trace_id(new_trace_id())
        try:
            log.info("api_startup", **settings.safe_summary())
        finally:
            reset_trace_id(token)
        yield
        log.info("api_shutdown")

    app = FastAPI(title="ontologyMVP API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    from src.query.api import router as query_router

    from apps.api.admin import build_admin_router

    app.include_router(build_admin_router(settings))
    app.include_router(query_router)

    # 后添加者在外层：TraceId 最外层，请求日志随 trace_id 输出
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(TraceIdMiddleware)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        # 只表示进程存活；不隐含任何依赖健康
        return {"status": "OK"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        report: ReadinessReport = await run_in_threadpool(build_readiness, settings)
        # FAIL → 503；OK/DEGRADED → 200（可选能力缺失不阻塞核心服务）
        status_code = 503 if report.status == "FAIL" else 200
        body = report.model_dump()
        body["trace_id"] = get_trace_id()
        return JSONResponse(status_code=status_code, content=body)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.to_public_dict(), "trace_id": get_trace_id()},
        )

    return app
