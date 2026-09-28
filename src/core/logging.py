"""结构化 JSON 日志：所有事件带 trace_id，敏感字段与凭据强制脱敏。

脱敏保证（有测试约束）：
- 键名命中敏感模式（password/token/secret 等）的值替换为 ***；
- 字符串值中的 URL 凭据（scheme://user:pass@）与 key=value/键:值 形式的
  密码/Token 一律掩码，异常堆栈文本同样经过该处理器。
"""

from __future__ import annotations

import re
import sys
from typing import Any, TextIO

import structlog

from src.core.config import Settings
from src.core.trace import get_trace_id

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_SENSITIVE_KEY_EXACT = {
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "dsn",
}
_SENSITIVE_KEY_SUFFIX = ("password", "passwd", "secret", "token", "api_key", "key")

# postgres://user:password@host → postgres://user:***@host
_URL_CREDENTIALS = re.compile(r"://([^:/@\s]+):([^@/\s]+)@")
# password=hunter2 / password: hunter2 / token=abc（含日志常用的引号包裹形式）
_KEY_VALUE_CREDENTIALS = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*['\"]?([^\s'\",;&]+)"
)


def sanitize_text(value: str, *, limit: int = 500) -> str:
    """掩码字符串中的凭据；截断超长文本（诊断信息只保留非敏感前缀）。"""
    sanitized = _URL_CREDENTIALS.sub(r"://\1:***@", value)
    sanitized = _KEY_VALUE_CREDENTIALS.sub(r"\1=***", sanitized)
    return sanitized[:limit]


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: _redact_key(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item) for item in value]
    return value


def _redact_key(key: Any, value: Any) -> Any:
    name = str(key).lower().replace("-", "_")
    if (
        name in _SENSITIVE_KEY_EXACT
        or name.startswith(_SENSITIVE_KEY_SUFFIX)
        or name.endswith(_SENSITIVE_KEY_SUFFIX)
    ):
        return "***"
    return _sanitize_value(value)


def redact_secrets(logger: Any, method: str, event: dict[str, Any]) -> dict[str, Any]:
    """structlog 处理器：递归脱敏事件。"""
    return {key: _redact_key(key, value) for key, value in event.items()}


def add_trace_id(logger: Any, method: str, event: dict[str, Any]) -> dict[str, Any]:
    """structlog 处理器：注入当前 contextvar 中的 trace_id。"""
    trace_id = get_trace_id()
    if trace_id:
        event["trace_id"] = trace_id
    return event


def configure_logging(settings: Settings, *, output: TextIO | None = None) -> None:
    """进程级日志配置；幂等，测试与多次启动安全。

    output 允许测试捕获渲染后的 JSON 行，与生产处理器链完全一致。
    """
    level = settings.log_level.upper()
    if level not in _LEVELS:  # pragma: no cover - pydantic 已保证，双保险
        level = "INFO"
    structlog.configure(
        processors=[
            redact_secrets,
            add_trace_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.set_exc_info,
            structlog.processors.format_exc_info,
            redact_secrets,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, f"_{level}", 20)
        ),
        logger_factory=structlog.PrintLoggerFactory(
            file=output if output is not None else sys.stdout
        ),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
