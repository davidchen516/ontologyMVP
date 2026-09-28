"""统一错误模型。

约定：AppError 的 message 不得包含 Secret、密码、Token 或完整 DSN；
面向外部的错误体只包含 code/message/trace_id。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """应用错误基类，携带稳定错误码与建议 HTTP 状态码。"""

    code = "app_error"
    http_status = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.context = context or {}

    def to_public_dict(self) -> dict[str, Any]:
        """可安全返回给调用方的错误体（无敏感信息）。"""
        return {"code": self.code, "message": self.message}


class ConfigurationError(AppError):
    code = "configuration_error"
    http_status = 503
