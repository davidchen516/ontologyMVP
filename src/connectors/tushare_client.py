"""通用 TuShare Client：Token 注入、超时、限流、指数退避+抖动、脱敏。

设计要点（issue #3）：
- 走 TuShare Pro HTTP JSON 接口（fields/items 原样保留），不使用会转 DataFrame
  的 SDK，保证 Raw 层"原样保存、不丢字段"；
- Transport 可替换：生产为 httpx，测试为 Fixture Transport（CI 不耗真实额度）；
- 错误分类（code/msg → 7 态能力枚举的瞬态/终态错误），瞬态才退避重试；
- Token、Authorization、敏感参数绝不进入日志与异常文本（sanitize 兜底）。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from src.connectors.ports import (
    NetworkError,
    PermissionDeniedError,
    RateLimitedError,
    TerminalConnectorError,
)
from src.core.config import Settings
from src.core.logging import sanitize_text

log = structlog.get_logger(__name__)

Transport = Callable[[dict[str, Any]], dict[str, Any]]

# TuShare 错误码分类（官方语义：负数为错误；-1x/-2x 为参数/积分/权限类）
NO_PERMISSION_CODES = {-2001, -2002, -2003}
RATE_LIMIT_HINTS = ("频率", "每分钟", "rate", "limit", "429")


class RateLimiter:
    """每 Connector 独立的固定间隔限速器（每分钟 N 个请求，间隔 60/N 秒）。"""

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic):
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self._per_minute = per_minute
        self._interval = 60.0 / per_minute
        self._clock = clock
        self._next_allowed = self._clock()

    def acquire(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
        self._next_allowed = max(self._next_allowed, self._clock()) + self._interval


def classify_tushare_response(raw: dict[str, Any]) -> tuple[str, str] | None:
    """把 TuShare 返回分类为 (error_code, sanitized_message)；None = 成功。

    只做分类，不做重试决策；调用方据此抛出对应异常。
    """
    code = raw.get("code")
    if code == 0:
        return None
    message = sanitize_text(str(raw.get("msg", "unknown tushare error")), limit=200)
    lower = message.lower()
    if any(hint in lower or hint in message for hint in RATE_LIMIT_HINTS) or code == 429:
        return "RATE_LIMITED", message
    if code in NO_PERMISSION_CODES:
        # 无法从负码区分普通权限与独立权限时，由 Probe 层结合 msg 细化
        return "NO_PERMISSION", message
    if code in (-1, -2):
        # 参数/内部错误：不可重试的数据集级失败
        return "INVALID_REQUEST", message
    if code == -4001:
        return "SCHEMA_CHANGED", message
    return "UNKNOWN", message


def http_transport(base_url: str, token: str, timeout_seconds: float) -> Transport:
    """生产 Transport：httpx POST JSON。Token 只存在于请求体，不出现在日志。"""
    import httpx

    def transport(request: dict[str, Any]) -> dict[str, Any]:
        body = {"api_name": request["api_name"], "token": token, "params": request["params"]}
        if "fields" in request:
            body["fields"] = request["fields"]
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(base_url, json=body)
        if response.status_code == 429:
            return {"code": 429, "msg": "HTTP 429 rate limited"}
        response.raise_for_status()
        return response.json()

    return transport


@dataclass
class TushareCallResult:
    raw: dict[str, Any]
    attempts: int
    latency_ms: int


class TushareClient:
    """每个数据集 Connector 一个实例 → 独立限流桶与退避状态。"""

    def __init__(
        self,
        *,
        api_name: str,
        settings: Settings,
        token: str | None,
        transport: Transport,
        rate_limiter: RateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int | None = None,
    ) -> None:
        self.api_name = api_name
        self._settings = settings
        self._token = token
        self._transport = transport
        self._limiter = rate_limiter or RateLimiter(settings.tushare_rate_per_minute)
        self._sleep = sleeper
        self._max_retries = max_retries if max_retries is not None else settings.tushare_max_retries

    def call(
        self, params: dict[str, Any], *, fields: list[str] | None = None
    ) -> TushareCallResult:
        """一次逻辑调用：限流 → 请求 → 分类；瞬态错误按指数退避+抖动重试。

        权限/参数错误立即抛 PermissionDeniedError（不无限重试）；
        网络异常包装为 NetworkError（保留脱敏后的原因）。
        """
        request: dict[str, Any] = {"api_name": self.api_name, "params": params}
        if fields:
            request["fields"] = ",".join(fields)

        attempts = 0
        last_transient: RateLimitedError | NetworkError | None = None
        while attempts <= self._max_retries:
            attempts += 1
            self._limiter.acquire()
            started = time.perf_counter()
            try:
                raw = self._transport(request)
            except Exception as exc:  # noqa: BLE001 - 网络层一切异常都是瞬态候选
                # 任意异常文本可能内嵌任何内容（请求体、Token、URL 凭据），
                # 因此绝不嵌入原始消息，只保留异常类名（ConnectError 等已足够可操作）
                last_transient = NetworkError(
                    f"{self.api_name} network error: {type(exc).__name__}"
                )
            else:
                latency_ms = int((time.perf_counter() - started) * 1000)
                classified = classify_tushare_response(raw)
                if classified is None:
                    return TushareCallResult(raw=raw, attempts=attempts, latency_ms=latency_ms)
                error_code, message = classified
                if error_code in ("RATE_LIMITED",):
                    last_transient = RateLimitedError(f"{self.api_name}: {message}")
                elif error_code == "NO_PERMISSION":
                    raise PermissionDeniedError(f"{self.api_name}: {message}")
                else:
                    # INVALID_REQUEST / SCHEMA_CHANGED / UNKNOWN：终态错误，交由上层熔断
                    raise TerminalConnectorError(
                        error_code, f"{self.api_name}: {message}"
                    ) from None
            # 瞬态退避：base * 2^(n-1) + 抖动（0~1s），封顶 cap；超阈值停止请求
            if attempts <= self._max_retries:
                backoff = min(
                    self._settings.tushare_backoff_base_seconds * (2 ** (attempts - 1)),
                    self._settings.tushare_backoff_cap_seconds,
                )
                backoff += random.uniform(0, 1.0)
                log.warning(
                    "tushare_transient_retry",
                    api_name=self.api_name,
                    attempt=attempts,
                    backoff_seconds=round(backoff, 2),
                    reason=type(last_transient).__name__,
                )
                self._sleep(backoff)

        assert last_transient is not None
        last_transient.attempts = attempts
        raise last_transient

    @property
    def token_missing(self) -> bool:
        return not self._token
