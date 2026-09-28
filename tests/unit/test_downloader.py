"""下载安全矩阵单元测试（无 DB——纯函数层，本地/CI 无 DSN 也必跑）。"""

from __future__ import annotations

import pytest
from src.documents.downloader import (
    MAX_FILE_BYTES,
    DownloadRejected,
    safe_download,
)

OK_URL = "https://static.cninfo.com.cn/finalpage/2026/1.PDF"


def fetch_ok(url):
    return 200, {"content-type": "application/pdf"}, b"%PDF-1.4 normal"


def test_https_whitelist_rejects_http():
    with pytest.raises(DownloadRejected, match="non-https"):
        safe_download("http://static.cninfo.com.cn/x.pdf", fetch=fetch_ok)


def test_whitelist_rejects_unknown_domain():
    with pytest.raises(DownloadRejected, match="not whitelisted"):
        safe_download("https://evil.example.com/x.pdf", fetch=fetch_ok)


def test_whitelist_rejects_subdomain():
    with pytest.raises(DownloadRejected, match="not whitelisted"):
        safe_download("https://evil.static.cninfo.com.cn.attacker.io/x.pdf",
                      fetch=fetch_ok)


def test_whitelist_rejects_userinfo_trick():
    with pytest.raises(DownloadRejected):
        safe_download("https://evil.com#@static.cninfo.com.cn/x.pdf", fetch=fetch_ok)


def test_uppercase_domain_allowed():
    result = safe_download("https://STATIC.CNINFO.COM.CN/x.pdf", fetch=fetch_ok)
    assert result.sha256


def test_redirect_to_evil_domain_rejected():
    def fetch(url):
        if "cninfo" in url:
            return 302, {"location": "https://evil.example.com/x.pdf"}, b""
        return 200, {}, b"%PDF-"

    with pytest.raises(DownloadRejected, match="not whitelisted"):
        safe_download(OK_URL, fetch=fetch)


def test_redirect_to_http_rejected():
    def fetch(url):
        return 302, {"location": "http://static.cninfo.com.cn/x.pdf"}, b""

    with pytest.raises(DownloadRejected, match="non-https"):
        safe_download(OK_URL, fetch=fetch)


def test_relative_redirect_resolved_against_whitelist():
    def fetch(url):
        if url == OK_URL:
            return 302, {"location": "/finalpage/2026/2.PDF"}, b""
        return 200, {"content-type": "application/pdf"}, b"%PDF-1.4 ok"

    result = safe_download(OK_URL, fetch=fetch)
    assert result.data == b"%PDF-1.4 ok"


def test_redirect_limit_is_final():
    def fetch(url):
        return 302, {"location": "https://static.cninfo.com.cn/loop"}, b""

    with pytest.raises(DownloadRejected, match="too many redirects") as excinfo:
        safe_download(OK_URL, fetch=fetch)
    assert excinfo.value.retryable is False


def test_magic_mismatch_rejected():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    with pytest.raises(DownloadRejected, match="magic mismatch"):
        safe_download(OK_URL, fetch=lambda url: (200, {"content-type": "application/pdf"}, png))


def test_magic_within_1024_byte_window_accepted():
    # PDF 规范允许头部前杂讯：魔数在 1024 窗口内应通过
    padded = b"junk" * 100 + b"%PDF-1.4 body"
    def fetch_window(url):
        return 200, {"content-type": "application/pdf"}, padded

    result = safe_download(OK_URL, fetch=fetch_window)
    assert result.data == padded


def test_oversize_is_terminal_isolation():
    huge = b"%PDF-" + b"0" * (MAX_FILE_BYTES + 1)
    with pytest.raises(DownloadRejected, match="exceeds limit") as excinfo:
        safe_download(OK_URL, fetch=lambda url: (200, {"content-type": "application/pdf"}, huge))
    assert excinfo.value.retryable is False  # 隔离，不重试


def test_404_is_expired_not_retryable():
    with pytest.raises(DownloadRejected, match="url expired") as excinfo:
        safe_download(OK_URL, fetch=lambda url: (404, {}, b""))
    assert excinfo.value.retryable is False


def test_503_is_retryable():
    with pytest.raises(DownloadRejected) as excinfo:
        safe_download(OK_URL, fetch=lambda url: (503, {}, b""))
    assert excinfo.value.retryable is True


def test_content_type_mismatch_rejected():
    def fetch(url):
        return 200, {"content-type": "text/html"}, b"%PDF-1.4 html page"

    with pytest.raises(DownloadRejected, match="unexpected content-type"):
        safe_download(OK_URL, fetch=fetch)
