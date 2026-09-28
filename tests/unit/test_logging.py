"""日志脱敏与 trace_id 注入测试：Token/密码不得被序列化。"""

from __future__ import annotations

import io
import json

import structlog
from src.core.logging import configure_logging, sanitize_text
from src.core.trace import new_trace_id, reset_trace_id, set_trace_id

from tests.helpers import make_settings


def read_events(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def configured_logger(buffer: io.StringIO):
    configure_logging(make_settings(log_level="DEBUG"), output=buffer)
    return structlog.get_logger("test")


def test_sensitive_keys_are_masked():
    buffer = io.StringIO()
    log = configured_logger(buffer)
    log.info(
        "evt",
        password="hunter2",
        postgres_password="hunter2",
        tushare_token="tok-abc",
        authorization="Bearer abc",
        api_key="k-1",
        some_secret="s-1",
    )
    events = read_events(buffer)
    assert events, "应产生日志输出"
    assert "hunter2" not in buffer.getvalue()
    assert "tok-abc" not in buffer.getvalue()
    assert "Bearer abc" not in buffer.getvalue()
    assert all(value == "***" for key, value in events[0].items()
               if key.endswith(("password", "token", "authorization", "api_key", "secret")))


def test_credentials_inside_string_values_are_masked():
    buffer = io.StringIO()
    log = configured_logger(buffer)
    log.info(
        "evt",
        dsn="postgresql://ontology:hunter2@db:5432/ontology",
        note="auth failed password=hunter2 for user=ontology",
        safe="postgres://ontology:***@db",
    )
    content = buffer.getvalue()
    assert "hunter2" not in content
    events = read_events(buffer)
    assert events[0]["dsn"] == "***"  # dsn 键名直接脱敏
    assert "password=***" in events[0]["note"]
    assert events[0]["safe"] == "postgres://ontology:***@db"


def test_exception_text_with_credentials_is_masked():
    buffer = io.StringIO()
    log = configured_logger(buffer)
    try:
        raise RuntimeError("connect failed password=hunter2 at postgres://u:pw@h/db")
    except RuntimeError:
        log.exception("boom", token="tok-abc")
    content = buffer.getvalue()
    assert "hunter2" not in content
    assert "tok-abc" not in content
    events = read_events(buffer)
    assert events[0]["token"] == "***"
    assert "u:***@h/db" in events[0]["exception"]


def test_trace_id_injected_from_contextvar():
    buffer = io.StringIO()
    log = configured_logger(buffer)
    token = set_trace_id("trace-abc-123")
    try:
        log.info("evt")
    finally:
        reset_trace_id(token)
    events = read_events(buffer)
    assert events[0]["trace_id"] == "trace-abc-123"


def test_events_without_trace_id_have_no_empty_field():
    buffer = io.StringIO()
    log = configured_logger(buffer)
    log.info("evt")
    assert "trace_id" not in read_events(buffer)[0]


def test_sanitize_text_url_and_kv_patterns():
    assert sanitize_text("http://u:pw@h/x") == "http://u:***@h/x"
    assert "pw" not in sanitize_text("x://u:pw@h").split("@")[0]
    assert "token=***" in sanitize_text("failed token=abc")
    long_text = sanitize_text("a" * 600, limit=500)
    assert len(long_text) == 500


def test_new_trace_id_unique_and_hex32():
    assert len(set(new_trace_id() for _ in range(100))) == 100
    assert all(len(t) == 32 and all(c in "0123456789abcdef" for c in t)
               for t in (new_trace_id() for _ in range(10)))
