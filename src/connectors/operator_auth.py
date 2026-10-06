"""Operator 认证（issue #44 D-1）：复用 ADR-0006 API-Key 机制。

与 Reviewer 认证（src/review/auth.py）同构——X-Operator-Key →
SHA-256 哈希 + 常量时间比对；服务端只存哈希。
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from src.core.config import Settings

HEADER_NAME = "x-operator-key"


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _configured_hashes(settings: Settings) -> list[str]:
    raw = settings.operator_api_key_hashes or ""
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def operator_write_available(settings: Settings) -> bool:
    """运维写能力可用性：开关开 + 至少一个有效哈希。"""
    return (
        settings.operator_write_enabled
        and bool(_configured_hashes(settings))
    )


def authenticate_operator(request: Request) -> str:
    """验证 Operator 密钥；有效返回 operator 标识（哈希前 8 位）。

    失败：403（无效 key）/ 401（无 key）/ 503（能力关闭）——零副作用。
    """
    settings: Settings = request.app.state.settings
    if not operator_write_available(settings):
        raise HTTPException(
            status_code=503,
            detail="operator write is disabled on this server",
        )
    supplied = request.headers.get(HEADER_NAME)
    if not supplied:
        raise HTTPException(status_code=401, detail="operator key required")
    supplied_hash = hash_key(supplied)
    for configured in _configured_hashes(settings):
        if hmac.compare_digest(supplied_hash, configured):
            return f"operator:{configured[:8]}"
    raise HTTPException(status_code=403, detail="invalid operator key")
