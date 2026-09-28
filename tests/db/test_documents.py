"""文档管道 DB 测试（issue #6 验收逐条 + 崩溃/对账不变量）。

Fixture 策略：PDF 由手工最小 PDF 字节在测试内生成（正常多页/扫描空页/
损坏字节/MIME 伪装），无二进制入库；下载传输用注入 fake（CI 零外呼）。
"""

from __future__ import annotations

import io
import uuid

import pytest
from src.documents.pipeline import (
    discover_document,
    download_version,
    parse_version,
    reconcile_storage,
)
from src.documents.storage import LocalFileStorage
from src.domain.enums import DownloadStatus

CNINFO_URL = "https://static.cninfo.com.cn/finalpage/2026-01-01/1.PDF"
EVIL_URL = "https://evil.example.com/x.pdf"


# ---- 测试 PDF 构造（手工最小多页 PDF，pypdf 可提取文本） ----


def _minimal_pdf(pages_text: list[str | None]) -> bytes:
    objects: list[bytes] = []
    page_ids: list[int] = []
    next_id = 4
    for text in pages_text:
        if text is None:
            stream = b""
        else:
            stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
        content_id = next_id
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
            + b"\nendstream"
        )
        page_id = next_id + 1
        objects.append(
            f"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        page_ids.append(page_id)
        next_id += 2

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    head = [
        b"<< /Type /Pages /Kids [" + kids.encode() + b"] /Count "
        + str(len(page_ids)).encode() + b" >>",
        b"<< /Type /Catalog /Pages 1 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    objects = head + objects

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 2 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF".encode()
    )
    return out.getvalue()


def make_normal_pdf() -> bytes:
    return _minimal_pdf([
        "Chapter 1 Operations Review. The company has achieved mass production of "
        "precision robot reducers with stable revenue growth this reporting period.",
        "Chapter 2 Risk Factors. Raw material price fluctuations may affect the "
        "gross margin and the company will keep investing in research.",
        "Chapter 3 Capacity Plan. The company plans to expand the precision "
        "machining line and expects a new capacity ramp next fiscal year.",
    ])


def make_scanned_pdf() -> bytes:
    """扫描/低文本 PDF：多页几乎无文本。"""
    return _minimal_pdf([None, None, None])


def make_fetch(data: bytes, *, content_type: str = "application/pdf", status: int = 200):
    def fetch(url: str):
        return status, {"content-type": content_type}, data
    return fetch


def version_repo(uow_factory):
    """跨事务仓库代理：每个方法调用独立事务（与生产 Worker 语义一致）。"""
    factory = uow_factory

    class _Proxy:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                with factory.transaction() as uow:
                    return getattr(uow.document_versions, name)(*args, **kwargs)
            return call

    return _Proxy()


def seed_document(uow_factory, *, content_hash: str, url: str = CNINFO_URL) -> dict:
    with uow_factory.transaction() as uow:
        return discover_document(
            uow.document_versions,
            source_system="CNINFO",
            external_id=f"ann-{content_hash[:8]}",
            title=f"公告 {content_hash[:8]}",
            content_hash=content_hash,
            source_url=url,
        )


# ---- 发现幂等与版本化 ----


def test_discover_twice_no_duplicate_document_or_version(uow_factory) -> None:
    content_hash = uuid.uuid4().hex
    first = seed_document(uow_factory, content_hash=content_hash)
    second = seed_document(uow_factory, content_hash=content_hash)
    assert first["document_id"] == second["document_id"]
    assert first["version"]["id"] == second["version"]["id"]
    with uow_factory.transaction() as uow:
        count = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM fact.document_version WHERE document_id = %s",
            (first["document_id"],),
        ).fetchone()[0]
    assert count == 1


def test_content_change_creates_new_version_old_accessible(uow_factory) -> None:
    v1, v2 = uuid.uuid4().hex * 2, uuid.uuid4().hex * 2  # CHAR(64) 定长
    external_id = "ann-stable-id"  # 官方 ID 不随内容变化 → 同一 Document
    with uow_factory.transaction() as uow:
        first = discover_document(
            uow.document_versions, source_system="CNINFO", external_id=external_id,
            title="同 URL 公告", content_hash=v1, source_url=CNINFO_URL,
        )
    with uow_factory.transaction() as uow:
        second = discover_document(
            uow.document_versions, source_system="CNINFO", external_id=external_id,
            title="同 URL 公告（新内容）", content_hash=v2, source_url=CNINFO_URL,
        )
    assert second["document_id"] == first["document_id"]
    with uow_factory.transaction() as uow:
        versions = uow.document_versions.list_versions(first["document_id"])
    assert len(versions) == 2
    assert {v["version"] for v in versions} == {1, 2}
    assert {v["content_hash"] for v in versions} == {v1, v2}  # 旧 Hash 仍可访问


# ---- 下载安全矩阵 ----


def test_download_rejects_non_whitelisted_domain(uow_factory, tmp_path) -> None:
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex, url=EVIL_URL)
    result = download_version(
        version_repo(uow_factory), version_id=seeded["version"]["id"],
        fetch=make_fetch(b"%PDF- whatever"), storage=LocalFileStorage(tmp_path),
    )
    assert result["downloaded"] is False
    assert "not whitelisted" in result["rejected"]
    assert result["retryable"] is False  # 域名拒绝不可重试（终态隔离）


def test_download_rejects_mime_disguise(uow_factory, tmp_path) -> None:
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    result = download_version(
        version_repo(uow_factory), version_id=seeded["version"]["id"],
        fetch=make_fetch(png_bytes), storage=LocalFileStorage(tmp_path),
    )
    assert result["downloaded"] is False
    assert "magic mismatch" in result["rejected"]
    with uow_factory.transaction() as uow:
        version = uow.document_versions.get_version(seeded["version"]["id"])
    assert version["download_status"] == DownloadStatus.DOWNLOAD_FAILED_FINAL.value


def test_download_rejects_oversize(uow_factory, tmp_path) -> None:
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    from src.documents.downloader import MAX_FILE_BYTES

    huge = b"%PDF-" + b"0" * (MAX_FILE_BYTES + 1)
    result = download_version(
        version_repo(uow_factory), version_id=seeded["version"]["id"],
        fetch=make_fetch(huge), storage=LocalFileStorage(tmp_path),
    )
    assert result["downloaded"] is False
    assert "exceeds limit" in result["rejected"]


def test_download_http_error_is_retryable(uow_factory, tmp_path) -> None:
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    result = download_version(
        version_repo(uow_factory), version_id=seeded["version"]["id"],
        fetch=make_fetch(b"", status=503), storage=LocalFileStorage(tmp_path),
    )
    assert result["downloaded"] is False
    assert result["retryable"] is True
    with uow_factory.transaction() as uow:
        version = uow.document_versions.get_version(seeded["version"]["id"])
    assert version["download_status"] == DownloadStatus.DOWNLOAD_FAILED_RETRYABLE.value


def test_download_and_parse_happy_path_fragments_locatable(uow_factory, tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    pdf = make_normal_pdf()
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)

    download_result = download_version(
        repo, version_id=seeded["version"]["id"], fetch=make_fetch(pdf), storage=storage
    )
    assert download_result["downloaded"] is True

    parse_result = parse_version(repo, version_id=seeded["version"]["id"], storage=storage)
    assert parse_result["parsed"] is True
    assert parse_result["needs_review"] is False

    with uow_factory.transaction() as uow:
        rows = uow._conn.execute(  # noqa: SLF001
            "SELECT page_number, section_title, char_start, char_end, quote_text, "
            "normalized_text, checksum FROM fact.evidence_fragment ORDER BY page_number"
        ).fetchall()
    assert len(rows) >= 2
    for row in rows:
        page, section, start, end, quote, normalized, checksum = row
        assert page >= 1
        assert section is not None  # 章节被识别
        assert 0 <= start < end
        assert quote and normalized
        assert len(checksum) == 64
    assert any("mass production" in row[4] for row in rows)


def test_scanned_pdf_goes_to_review_not_parsed(uow_factory, tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)
    download_version(repo, version_id=seeded["version"]["id"],
                     fetch=make_fetch(make_scanned_pdf()), storage=storage)
    result = parse_version(repo, version_id=seeded["version"]["id"], storage=storage)
    assert result["needs_review"] is True  # 纯扫描 → 人工审核，不进自动抽取
    with uow_factory.transaction() as uow:
        version = uow.document_versions.get_version(seeded["version"]["id"])
    assert version["text_stats"]["page_count"] == 3


def test_corrupted_pdf_is_failed_final(uow_factory, tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    corrupted = b"%PDF-1.4\nthis is not really a pdf object stream\n%%EOF" + b"\x00\x01" * 50
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)
    download_version(repo, version_id=seeded["version"]["id"],
                     fetch=make_fetch(corrupted), storage=storage)
    result = parse_version(repo, version_id=seeded["version"]["id"], storage=storage)
    assert result["parsed"] is False and result["needs_review"] is False
    with uow_factory.transaction() as uow:
        fragments = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM fact.evidence_fragment"
        ).fetchone()[0]
    assert fragments == 0  # 解析失败不进入抽取队列语义


def test_replay_parse_twice_no_duplicate_fragments(uow_factory, tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)
    download_version(repo, version_id=seeded["version"]["id"],
                     fetch=make_fetch(make_normal_pdf()), storage=storage)
    first = parse_version(repo, version_id=seeded["version"]["id"], storage=storage)
    second = parse_version(repo, version_id=seeded["version"]["id"], storage=storage)
    assert second["fragments"]["inserted"] == 0  # 幂等重放
    assert first["fragments"]["inserted"] == second["fragments"]["received"]
    with uow_factory.transaction() as uow:
        count = uow._conn.execute(  # noqa: SLF001
            "SELECT count(*) FROM fact.evidence_fragment"
        ).fetchone()[0]
    assert count == first["fragments"]["inserted"]


def test_crash_between_storage_and_status_leaves_no_false_complete(
    uow_factory, tmp_path, monkeypatch
) -> None:
    """storage.put 后、mark_downloaded 前崩溃 → 无 DOWNLOADED 假完成。
    实际顺序：mark 在 put 之后——注入 put 抛出，验证状态留在 DOWNLOADING，
    重放后正确完成且无重复文件。"""
    storage = LocalFileStorage(tmp_path / "store")
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)

    original_put = LocalFileStorage.put

    def crashing_put(self, data, **kwargs):
        raise RuntimeError("simulated crash during storage write")

    monkeypatch.setattr(LocalFileStorage, "put", crashing_put)
    with pytest.raises(RuntimeError):
        download_version(repo, version_id=seeded["version"]["id"],
                         fetch=make_fetch(make_normal_pdf()), storage=storage)
    monkeypatch.setattr(LocalFileStorage, "put", original_put)

    with uow_factory.transaction() as uow:
        version = uow.document_versions.get_version(seeded["version"]["id"])
    assert version["download_status"] == DownloadStatus.DOWNLOADING.value  # 未假完成

    replay = download_version(repo, version_id=seeded["version"]["id"],
                              fetch=make_fetch(make_normal_pdf()), storage=storage)
    assert replay["downloaded"] is True
    assert storage.exists(replay["storage_key"])


def test_reconcile_missing_file_requeues_and_reports_orphans(uow_factory, tmp_path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    seeded = seed_document(uow_factory, content_hash=uuid.uuid4().hex)
    repo = version_repo(uow_factory)
    download_version(repo, version_id=seeded["version"]["id"],
                     fetch=make_fetch(make_normal_pdf()), storage=storage)

    # 文件丢失（模拟外部删除）
    version = repo.get_version(seeded["version"]["id"])
    storage.delete(version["storage_key"])

    report = reconcile_storage(repo, storage)
    assert report["missing_files_requeued"] == 1
    after = repo.get_version(seeded["version"]["id"])
    assert after["download_status"] == DownloadStatus.DOWNLOAD_PENDING.value

    # 孤儿文件：storage 有、DB 无对应 DOWNLOADED 记录
    storage.put(b"orphan-bytes")
    report2 = reconcile_storage(repo, storage)
    assert len(report2["orphan_files"]) == 1
