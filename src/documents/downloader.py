"""安全下载器（issue #6 负向矩阵的实现侧）。

- 域名白名单 + 仅 HTTPS；
- 重定向逐跳校验白名单（禁跨域重定向）、限制跳数；
- Content-Type + PDF 魔数校验（声称 PDF 但魔数不符 → 隔离）；
- 大小上限（防压缩炸弹的第一层：字节上限）；
- 超时；SHA-256 完整性；临时文件 + Hash 验证后原子落盘（由 storage 承担）；
- 带时效的签名 URL / 敏感参数不进入日志（本层对 URL 只记录白名单来源域与路径）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)

# 官方披露渠道白名单（可配置注入；默认巨潮/沪/深 + TuShare 静态资源域）
DEFAULT_ALLOWED_DOMAINS = frozenset(
    {
        "www.cninfo.com.cn",
        "static.cninfo.com.cn",
        "www.sse.com.cn",
        "static.sse.com.cn",
        "www.szse.cn",
        "disc.szse.cn",
        "static.szse.cn",
        "www.tushare.pro",
    }
)

PDF_MAGIC = b"%PDF-"
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB：超出直接 FAILED_FINAL
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_SECONDS = 30.0


class DownloadRejected(Exception):
    """下载被安全策略拒绝：不可重试（终态拒绝），调用方必须隔离。"""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class DownloadResult:
    data: bytes
    sha256: str
    content_type: str
    final_domain: str


def _assert_allowed(url: str, allowed_domains: frozenset[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise DownloadRejected(f"non-https scheme rejected: {parsed.scheme}")
    host = parsed.hostname or ""
    if host not in allowed_domains:
        raise DownloadRejected(f"domain not whitelisted: {host}")
    return host


def safe_download(
    url: str,
    *,
    fetch: Callable[[str], tuple[int, dict[str, str], bytes]],
    allowed_domains: frozenset[str] = DEFAULT_ALLOWED_DOMAINS,
    expected_mime: str = "application/pdf",
    max_bytes: int = MAX_FILE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> DownloadResult:
    """按安全策略下载。fetch 返回 (status, headers, body)，由注入传输执行
    （测试用 fake；生产用 httpx 且逐跳重定向回调本函数校验）。"""
    host = _assert_allowed(url, allowed_domains)

    current_url = url
    redirects = 0
    while True:
        status, headers, body = fetch(current_url)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("location", "")
            if not location:
                raise DownloadRejected("redirect without location", retryable=True)
            redirects += 1
            if redirects > max_redirects:
                # 重定向循环/超限是服务器侧持续状态：终态拒绝
                raise DownloadRejected(f"too many redirects (> {max_redirects})",
                                       retryable=False)
            # RFC 7231 允许相对 Location：先按 base URL 解析再校验白名单
            from urllib.parse import urljoin

            resolved = urljoin(current_url, location)
            _assert_allowed(resolved, allowed_domains)  # 跨域/非 HTTPS 重定向拒绝
            current_url = resolved
            continue
        if status != 200:
            # 404/410：URL 过期/资源移除 → 终态，调用方必须重解析目录元数据
            if status in (404, 410):
                raise DownloadRejected(f"url expired (http {status})", retryable=False)
            raise DownloadRejected(f"http status {status}", retryable=True)
        break

    content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type != expected_mime:
        raise DownloadRejected(f"unexpected content-type {content_type}")

    if len(body) > max_bytes:
        # 超大必须隔离（终态拒绝），不得无限重试
        raise DownloadRejected(f"file exceeds limit {max_bytes}", retryable=False)

    if expected_mime == "application/pdf":
        # PDF 规范允许头部前 ≤1024 字节杂讯：在窗口内寻找魔数
        if PDF_MAGIC not in body[:1024]:
            raise DownloadRejected("content is not a PDF (magic mismatch)")

    digest = hashlib.sha256(body).hexdigest()
    # 日志只含白名单域与大小，绝不含查询参数/签名 URL
    log.info(
        "document_downloaded", domain=host, path=urlparse(current_url).path,
        size_bytes=len(body), sha256_prefix=digest[:12],
    )
    return DownloadResult(data=body, sha256=digest, content_type=content_type or expected_mime,
                          final_domain=host)


def http_fetch_factory(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Callable:
    """生产传输：httpx GET（不跟随重定向——由 safe_download 逐跳校验）。"""
    import httpx

    def fetch(url: str) -> tuple[int, dict[str, str], bytes]:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.get(url)
        headers = {k.lower(): v for k, v in response.headers.items()}
        return response.status_code, headers, response.content

    return fetch
