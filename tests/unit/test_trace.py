"""TraceIdMiddleware 行为：透传、生成、注入 contextvar、回写响应头。"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from src.core.trace import TraceIdMiddleware, get_trace_id
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def make_echo_app() -> Starlette:
    async def echo(request):
        return JSONResponse({"trace_id": get_trace_id()})

    app = Starlette(routes=[Route("/echo", echo)])
    app.add_middleware(TraceIdMiddleware)
    return app


def test_incoming_trace_id_is_propagated_and_echoed():
    client = TestClient(make_echo_app())
    response = client.get("/echo", headers={"X-Trace-Id": "abc-123"})
    assert response.json()["trace_id"] == "abc-123"
    assert response.headers["x-trace-id"] == "abc-123"


def test_missing_trace_id_is_generated():
    client = TestClient(make_echo_app())
    response = client.get("/echo")
    trace_id = response.json()["trace_id"]
    assert HEX32.fullmatch(trace_id)
    assert response.headers["x-trace-id"] == trace_id


def test_unsafe_trace_id_is_replaced_not_trusted():
    client = TestClient(make_echo_app())
    response = client.get("/echo", headers={"X-Trace-Id": "bad id\nINJECT"})
    trace_id = response.json()["trace_id"]
    assert HEX32.fullmatch(trace_id)  # 非法输入 → 生成新值，不接受注入
    assert response.headers["x-trace-id"] == trace_id


def test_contextvar_is_reset_after_request():
    client = TestClient(make_echo_app())
    client.get("/echo")
    assert get_trace_id() is None  # 请求结束后上下文复位，不跨请求泄漏
