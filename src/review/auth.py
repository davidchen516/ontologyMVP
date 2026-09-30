"""审核认证/授权（ADR-0006）：静态 Reviewer API Key（SHA-256 哈希比对）。

- 服务端只存哈希（Settings.review_api_key_hashes，逗号分隔）；
- 常量时间比对（hmac.compare_digest）防时序侧信道；
- 明文密钥由 `X-Reviewer-Key` 请求头携带，不入 URL/日志/LocalStorage；
- 开关关闭或无有效密钥 → 403/503（事实库无副作用）。
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from src.core.config import Settings

HEADER_NAME = "x-reviewer-key"


def hash_key(plaintext: str) -> str:
    """密钥哈希工具（运维生成 .env 值用；服务端存储的就是这个值）。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _configured_hashes(settings: Settings) -> list[str]:
    raw = settings.review_api_key_hashes or ""
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def review_write_available(settings: Settings) -> bool:
    """审核写能力可用性：开关开 + 至少一个有效哈希。"""
    return settings.review_write_enabled and bool(_configured_hashes(settings))


def authenticate_reviewer(request: Request) -> str:
    """验证 Reviewer 密钥；有效返回 reviewer 标识（哈希前 8 位）。

    失败：403（有 key 但无效/无 key）或 503（服务端能力关闭）——
    两者都不产生任何事实库副作用。
    """
    settings: Settings = request.app.state.settings
    if not review_write_available(settings):
        raise HTTPException(
            status_code=503,
            detail="review write is disabled on this server",
        )
    supplied = request.headers.get(HEADER_NAME)
    if not supplied:
        raise HTTPException(status_code=401, detail="reviewer key required")
    supplied_hash = hash_key(supplied)
    for configured in _configured_hashes(settings):
        if hmac.compare_digest(supplied_hash, configured):
            # reviewer 标识 = 匹配哈希前 8 位（审计可追溯，不暴露明文）
            return f"reviewer:{configured[:8]}"
    raise HTTPException(status_code=403, detail="invalid reviewer key")
