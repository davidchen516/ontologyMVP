"""文档仓储：Document / DocumentVersion / EvidenceFragment 幂等操作（issue #6）。

不变量：
- 同 (document_id, content_hash) 只存一个版本——重复发现幂等返回既有版本；
- 同 URL 内容 Hash 变化 → 新版本（version = max+1），旧版本与文件永不覆盖；
- EvidenceFragment 引用 document_version_id，(document_id, checksum) 唯一去重；
- 解析失败的文档 parse_status 只能是 FAILED_* / PARSE_NEEDS_REVIEW，
  绝不带 fragments 进入抽取语义。
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from src.db.repositories import Repository


class DocumentVersionRepository(Repository):
    def upsert_document(
        self,
        *,
        source_system: str,
        external_id: str | None,
        title: str,
        company_id: uuid.UUID | None,
        content_hash: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """目录发现幂等：同 (source_system, content_hash) 只建一个 Document。"""
        row = self._fetchone(
            """
            INSERT INTO fact.document
                (document_type, source_system, external_id, company_id, title,
                 published_at, source_url, content_hash, mime_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system, content_hash) DO UPDATE
                SET title = EXCLUDED.title
            RETURNING id, content_hash, (xmax = 0) AS inserted
            """,
            (
                fields.get("document_type", "ANNOUNCEMENT"),
                source_system,
                external_id,
                company_id,
                title,
                fields.get("published_at"),
                fields.get("source_url"),
                content_hash,
                fields.get("mime_type", "application/pdf"),
            ),
        )
        assert row is not None
        return row

    def create_version(
        self,
        *,
        document_id: uuid.UUID,
        source_url: str,
        content_hash: str,
    ) -> dict[str, Any]:
        """同 (document, hash) 幂等；新内容 → 新版本号（原子 max+1）。"""
        existing = self._fetchone(
            "SELECT id, version FROM fact.document_version "
            "WHERE document_id = %s AND content_hash = %s",
            (document_id, content_hash),
        )
        if existing is not None:
            return {**existing, "inserted": False}
        row = self._fetchone(
            """
            INSERT INTO fact.document_version
                (document_id, version, source_url, content_hash)
            VALUES (%s,
                    COALESCE((SELECT max(version) FROM fact.document_version
                              WHERE document_id = %s), 0) + 1,
                    %s, %s)
            RETURNING id, version
            """,
            (document_id, document_id, source_url, content_hash),
        )
        assert row is not None
        return {**row, "inserted": True}

    def mark_downloaded(
        self,
        *,
        version_id: uuid.UUID,
        storage_key: str,
        file_hash: str,
        file_size: int,
        mime_type: str,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            UPDATE fact.document_version SET
                download_status = 'DOWNLOADED', storage_key = %s, file_hash = %s,
                file_size = %s, mime_type = %s, downloaded_at = now()
            WHERE id = %s
            RETURNING id, version, download_status, storage_key
            """,
            (storage_key, file_hash, file_size, mime_type, version_id),
        )
        assert row is not None
        return row

    def mark_download_status(
        self, version_id: uuid.UUID, target: str, *, error: str | None = None
    ) -> None:
        sets = "download_status = %s::download_status"
        params: list[Any] = [target]
        if error is not None:
            sets += ", text_stats = text_stats || %s::jsonb"
            params.append(Json({"download_error": error[:300]}))
        params.append(version_id)
        self._execute(
            f"UPDATE fact.document_version SET {sets} WHERE id = %s", tuple(params)
        )

    def get_version(self, version_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, document_id, version, source_url, content_hash, file_hash, "
            "storage_key, file_size, mime_type, download_status, downloaded_at, "
            "parser_version, page_count, text_stats, created_at "
            "FROM fact.document_version WHERE id = %s",
            (version_id,),
        )

    def list_versions(self, document_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, version, content_hash, file_hash, storage_key, "
            "download_status, parser_version, page_count, text_stats, created_at "
            "FROM fact.document_version WHERE document_id = %s ORDER BY version DESC",
            (document_id,),
        )

    def find_orphans(self, known_keys: set[str]) -> list[dict[str, Any]]:
        """数据库记录成功但文件缺失 → 告警重下清单。"""
        return self._fetchall(
            "SELECT id, document_id, version, storage_key, download_status "
            "FROM fact.document_version "
            "WHERE download_status = 'DOWNLOADED' AND storage_key IS NOT NULL"
        )

    def mark_parse_result(
        self,
        version_id: uuid.UUID,
        *,
        parse_status: str,
        parser_version: str,
        page_count: int | None,
        text_stats: dict[str, Any],
    ) -> None:
        self._execute(
            """
            UPDATE fact.document_version SET
                parser_version = %s, page_count = %s, text_stats = %s
            WHERE id = %s
            """,
            (parser_version, page_count, Json(text_stats), version_id),
        )

    def insert_fragments_batch(
        self, drafts: list[Any]
    ) -> dict[str, int]:
        """批量幂等写入片段：同 (document_id, checksum) 只保留一条；
        全批一个事务（调用方事务内执行）——重放不重复。"""
        inserted = 0
        for draft in drafts:
            row = self._fetchone(
                """
                SELECT ef.id FROM fact.evidence_fragment ef
                WHERE ef.document_id = (
                    SELECT document_id FROM fact.document_version WHERE id = %s
                ) AND ef.checksum = %s
                """,
                (draft.document_version_id, draft.checksum),
            )
            if row is not None:
                continue
            self._execute(
                """
                INSERT INTO fact.evidence_fragment
                    (document_id, document_version_id, page_number, section_title,
                     paragraph_index, char_start, char_end, quote_text,
                     normalized_text, checksum)
                SELECT dv.document_id, %s, %s, %s, %s, %s, %s, %s, %s, %s
                FROM fact.document_version dv WHERE dv.id = %s
                ON CONFLICT (document_id, checksum) DO NOTHING
                """,
                (
                    draft.document_version_id, draft.page_number, draft.section_title,
                    draft.paragraph_index, draft.char_start, draft.char_end,
                    draft.quote_text, draft.normalized_text, draft.checksum,
                    draft.document_version_id,
                ),
            )
            inserted += 1
        return {"inserted": inserted, "received": len(drafts)}
