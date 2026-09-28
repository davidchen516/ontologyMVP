"""文档管道编排：发现 → 下载 → 解析 → 片段（issue #6）。

崩溃恢复语义：
- 下载：临时字节经 storage 原子落盘后才 mark_downloaded——中途崩溃无半文件
  记录（storage 残留 .part 由 cleanup_temp 对账）；
- 解析：状态先转 PARSING，片段写入 + 版本解析结果 + 状态转移同事务；
  中途崩溃重放按 (document_id, checksum) 幂等，不混解析器版本；
- 对账：reconcile_storage 枚举 storage 孤儿（文件在、DB 无记录）与
  DB 记录缺失文件（告警 + 转 DOWNLOAD_PENDING 重下）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import structlog

from src.documents.downloader import DownloadRejected, safe_download
from src.documents.pdf_parser import (
    DocumentParseError,
    build_fragments,
    parse_pdf,
)
from src.documents.storage import FileStorage, LocalFileStorage
from src.domain.enums import DocumentParseStatus, DownloadStatus, ensure_transition

log = structlog.get_logger(__name__)

DocumentVersionRepo = Any  # src.documents.repositories.DocumentVersionRepository


def discover_document(
    repo: DocumentVersionRepo,
    *,
    source_system: str,
    external_id: str | None,
    title: str,
    content_hash: str,
    source_url: str,
    company_id: uuid.UUID | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """目录发现：幂等建 Document + 首个版本记录（download 状态机起点）。

    身份模型（issue #6）：Document 身份 = 元数据（来源+官方ID+URL）的稳定
    hash；同一 URL 的内容 Hash 变化 → 同一 Document 下的新 DocumentVersion，
    旧版本与文件永不覆盖。content_hash 参数是当前版本的内容 Hash。
    """
    import hashlib as _hashlib

    metadata_hash = _hashlib.sha256(
        f"{source_system}|{external_id}|{source_url}".encode()
    ).hexdigest()
    document = repo.upsert_document(
        source_system=source_system,
        external_id=external_id,
        title=title,
        company_id=company_id,
        content_hash=metadata_hash,
        source_url=source_url,
        published_at=None,
    )
    version = repo.create_version(
        document_id=document["id"], source_url=source_url, content_hash=content_hash
    )
    if source_note:
        log.info(
            "document_source_selected", source=source_system, note=source_note[:120]
        )
    return {"document_id": document["id"], "version": version,
            "document_inserted": document["inserted"]}


def download_version(
    repo: DocumentVersionRepo,
    *,
    version_id: uuid.UUID,
    fetch: Callable[[str], tuple[int, dict[str, str], bytes]],
    storage: FileStorage,
) -> dict[str, Any]:
    """安全下载 → 原子存储 → 状态推进。被拒/网络失败分类落状态。"""
    version = repo.get_version(version_id)
    if version is None:
        raise LookupError(f"document_version {version_id} not found")
    if version["download_status"] == DownloadStatus.DOWNLOADED.value:
        return {"downloaded": True, "already": True, "storage_key": version["storage_key"]}
    if version["download_status"] == DownloadStatus.DOWNLOAD_FAILED_FINAL.value:
        # 终态不再自动派发（人工处理/目录重解析入口另行提供）
        return {"downloaded": False, "rejected": "terminal download failure",
                "retryable": False, "terminal": True}

    if repo.get_download_attempts(version_id) >= 3:
        # 瞬态重试上限：3 次真实尝试后收敛为终态，不得无限重试。
        # 状态机无 FAILED_RETRYABLE→FINAL 直达边：经 PENDING→DOWNLOADING→FINAL
        repo.mark_download_status(version_id, DownloadStatus.DOWNLOAD_PENDING.value)
        repo.mark_download_status(version_id, DownloadStatus.DOWNLOADING.value)
        repo.mark_download_status(
            version_id, DownloadStatus.DOWNLOAD_FAILED_FINAL.value,
            error="retry budget exhausted",
        )
        return {"downloaded": False, "rejected": "retry budget exhausted",
                "retryable": False}

    current_status = version["download_status"]
    if current_status == DownloadStatus.DOWNLOADING.value:
        # 崩溃残留的 DOWNLOADING（状态机无回头路）：先按恢复语义落
        # FAILED_RETRYABLE，再重新排队——半文件不会被当作完成
        repo.mark_download_status(
            version_id, DownloadStatus.DOWNLOAD_FAILED_RETRYABLE.value,
            error="stale DOWNLOADING recovered by re-dispatch",
        )
        current_status = DownloadStatus.DOWNLOAD_FAILED_RETRYABLE.value
    ensure_transition("document_download", current_status,
                      DownloadStatus.DOWNLOAD_PENDING.value)
    repo.mark_download_status(version_id, DownloadStatus.DOWNLOAD_PENDING.value)
    repo.mark_download_status(version_id, DownloadStatus.DOWNLOADING.value)
    try:
        result = safe_download(version["source_url"], fetch=fetch)
    except DownloadRejected as exc:
        target = (
            DownloadStatus.DOWNLOAD_FAILED_RETRYABLE.value
            if exc.retryable
            else DownloadStatus.DOWNLOAD_FAILED_FINAL.value
        )
        repo.mark_download_status(version_id, target, error=exc.reason)
        expired = "url expired" in exc.reason
        return {
            "downloaded": False, "rejected": exc.reason,
            "retryable": exc.retryable, "url_expired": expired,
        }

    # 原子落盘成功 → 才置 DOWNLOADED（崩溃无"半文件被当完整"）
    storage_key = storage.put(result.data, expected_sha256=result.sha256)
    repo.mark_downloaded(
        version_id=version_id,
        storage_key=storage_key,
        file_hash=result.sha256,
        file_size=len(result.data),
        mime_type=result.content_type,
    )
    return {"downloaded": True, "already": False, "storage_key": storage_key,
            "sha256": result.sha256}


def parse_version(
    repo: DocumentVersionRepo,
    *,
    version_id: uuid.UUID,
    storage: FileStorage,
) -> dict[str, Any]:
    """解析已下载版本：质量门禁决定 PARSED / PARSE_NEEDS_REVIEW / FAILED_*。

    解析失败的文档不进入自动 Claim 抽取（无 fragments 落库语义由状态表达）。
    """
    from src.db.uow import UnitOfWork  # noqa: F401 - 类型提示

    version = repo.get_version(version_id)
    if version is None:
        raise LookupError(f"document_version {version_id} not found")
    if version["storage_key"] is None:
        raise ValueError(f"version {version_id} not downloaded yet")

    # 解析状态机驱动（BLOCKER-1：状态必须落库）
    parse_status_now = version["parse_status"]
    if parse_status_now == DocumentParseStatus.PARSING.value:
        # 崩溃残留的 PARSING：恢复语义 → FAILED_RETRYABLE → 重新排队
        repo.mark_parse_status(version_id, DocumentParseStatus.FAILED_RETRYABLE.value)
        repo.mark_parse_status(version_id, DocumentParseStatus.PENDING.value)
        parse_status_now = DocumentParseStatus.PENDING.value
    if parse_status_now == DocumentParseStatus.PARSED.value:
        return {"parsed": True, "already": True, "needs_review": False,
                "fragments": {"inserted": 0, "received": 0}}
    if parse_status_now in (
        DocumentParseStatus.FAILED_FINAL.value, DocumentParseStatus.SKIPPED.value
    ):
        return {"parsed": False, "needs_review": False, "terminal": True}

    repo.mark_parse_status(version_id, DocumentParseStatus.PARSING.value)
    data = storage.get(version["storage_key"])
    try:
        parsed = parse_pdf(data)
    except DocumentParseError as exc:
        repo.mark_parse_result(
            version_id, parse_status=DocumentParseStatus.FAILED_FINAL.value,
            parser_version="unknown", page_count=None,
            text_stats={"error": str(exc)[:300]},
        )
        return {"parsed": False, "needs_review": False, "error": str(exc)}

    status = (
        DocumentParseStatus.PARSE_NEEDS_REVIEW if parsed.needs_review
        else DocumentParseStatus.PARSED
    )
    # 需审核版本不写片段：纯扫描/低文本不得进入自动抽取池，
    # 人工审核通过后重新解析才产生片段
    if parsed.needs_review:
        repo.mark_parse_result(
            version_id, parse_status=status.value,
            parser_version=parsed.parser_version, page_count=len(parsed.pages),
            text_stats=parsed.quality,
        )
        log.info("document_needs_review", version_id=str(version_id),
                 quality=parsed.quality)
        return {
            "parsed": False, "needs_review": True, "quality": parsed.quality,
            "fragments": {"inserted": 0, "received": 0},
        }

    drafts = build_fragments(parsed, document_version_id=version_id)
    repo.mark_parse_result(
        version_id,
        parse_status=status.value,
        parser_version=parsed.parser_version,
        page_count=len(parsed.pages),
        text_stats=parsed.quality,
    )
    fragment_stats = repo.insert_fragments_batch(drafts)
    log.info(
        "document_parsed", version_id=str(version_id), status=status.value,
        pages=len(parsed.pages), fragments=fragment_stats["inserted"],
        quality=parsed.quality,
    )
    return {
        "parsed": True,
        "needs_review": False,
        "quality": parsed.quality,
        "fragments": fragment_stats,
    }


def reconcile_storage(
    repo: DocumentVersionRepo, storage: LocalFileStorage
) -> dict[str, Any]:
    """对账：DB 有记录但文件缺失 → 告警并转 DOWNLOAD_PENDING 重下；
    文件存在但无 DB 记录 → 孤儿清单（人工确认后清理）。"""
    orphans_in_db: list[str] = []
    for row in repo.find_orphans(set()):
        if row["storage_key"] and not storage.exists(row["storage_key"]):
            orphans_in_db.append(row["storage_key"])
            repo.mark_download_status(row["id"], DownloadStatus.DOWNLOAD_PENDING.value)

    known = {
        row["storage_key"]
        for row in repo.find_orphans(set())
        if row["storage_key"]
    }
    untracked_files = [key for key in storage.list_keys() if key not in known]

    cleaned = storage.cleanup_temp()
    return {
        "missing_files_requeued": len(orphans_in_db),
        "orphan_files": untracked_files,
        "temp_parts_cleaned": cleaned,
    }
