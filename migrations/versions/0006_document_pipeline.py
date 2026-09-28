"""document versioning, download status machine and evidence versioning

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29

- download_status 原生枚举：DISCOVERED/DOWNLOAD_PENDING/DOWNLOADING/
  DOWNLOADED/DOWNLOAD_FAILED_RETRYABLE/DOWNLOAD_FAILED_FINAL（issue #6 状态机）；
- document_parse_status 追加 PARSE_NEEDS_REVIEW（纯扫描/低文本文档进人工审核，
  不进入自动 Claim 抽取）；
- fact.document_version：同 URL 内容 Hash 变化 → 新版本，旧版本与文件不覆盖；
- fact.evidence_fragment 增加 document_version_id：片段必须引用具体解析版本。

回滚策略：downgrade 删列/表/枚举值（DROP VALUE 需重建类型，采用
重建-重插法）；扩展仅新增，不破坏既有数据。
"""

from __future__ import annotations

from alembic import op

revision = "0006_document_pipeline"
down_revision = "0005_financial_announced_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE download_status AS ENUM ("
        "'DISCOVERED', 'DOWNLOAD_PENDING', 'DOWNLOADING', 'DOWNLOADED', "
        "'DOWNLOAD_FAILED_RETRYABLE', 'DOWNLOAD_FAILED_FINAL')"
    )
    op.execute("ALTER TABLE fact.document ADD COLUMN download_status download_status"
               " NOT NULL DEFAULT 'DISCOVERED'")
    # PARSE_NEEDS_REVIEW：纯扫描文档显式进入人工审核（不静默、不进抽取队列）
    op.execute("ALTER TYPE document_parse_status ADD VALUE 'PARSE_NEEDS_REVIEW'")

    op.execute(
        """
        CREATE TABLE fact.document_version (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id             UUID NOT NULL REFERENCES fact.document(id),
            version                 INTEGER NOT NULL,
            source_url              TEXT NOT NULL,
            content_hash            CHAR(64) NOT NULL,
            file_hash               CHAR(64),
            storage_key             TEXT,
            file_size               BIGINT,
            mime_type               VARCHAR(100),
            download_status         download_status NOT NULL DEFAULT 'DISCOVERED',
            parse_status            document_parse_status NOT NULL DEFAULT 'PENDING',
            downloaded_at           TIMESTAMPTZ,
            parser_version          VARCHAR(50),
            page_count              INTEGER,
            text_stats              JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, version),
            UNIQUE (document_id, content_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_document_version_document "
        "ON fact.document_version (document_id, version DESC)"
    )
    op.execute("ALTER TABLE fact.evidence_fragment ADD COLUMN document_version_id UUID")
    op.execute(
        "ALTER TABLE fact.evidence_fragment ADD CONSTRAINT "
        "evidence_fragment_version_fkey "
        "FOREIGN KEY (document_version_id) REFERENCES fact.document_version(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fact.evidence_fragment DROP CONSTRAINT IF EXISTS evidence_fragment_version_fkey")
    op.execute("ALTER TABLE fact.evidence_fragment DROP COLUMN IF EXISTS document_version_id")
    op.execute("ALTER TABLE fact.document_version DROP COLUMN IF EXISTS parse_status")
    op.execute("DROP INDEX IF EXISTS fact.idx_document_version_document")
    op.execute("DROP TABLE IF EXISTS fact.document_version")
    # 重建 parse 枚举去掉 PARSE_NEEDS_REVIEW（列未存该值时才可回退）
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM fact.document WHERE parse_status = 'PARSE_NEEDS_REVIEW'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: PARSE_NEEDS_REVIEW rows exist';
            END IF;
        END $$;
        """
    )
    # PG 无 DROP VALUE：重建枚举类型（不含 PARSE_NEEDS_REVIEW）并重转换列；
    # 若仍存有该值行，类型转换会响亮失败（防降级丢数据）
    op.execute("CREATE TYPE document_parse_status_v1 AS ENUM ("
               "'PENDING', 'PARSING', 'PARSED', "
               "'FAILED_RETRYABLE', 'FAILED_FINAL', 'SKIPPED')")
    # 列 DEFAULT 会阻挡类型转换：先移除；类型重建完成后再恢复 DEFAULT
    op.execute("ALTER TABLE fact.document ALTER COLUMN parse_status DROP DEFAULT")
    op.execute(
        "ALTER TABLE fact.document ALTER COLUMN parse_status "
        "TYPE document_parse_status_v1 USING parse_status::text::document_parse_status_v1"
    )
    op.execute("DROP TYPE document_parse_status")
    op.execute("ALTER TYPE document_parse_status_v1 RENAME TO document_parse_status")
    op.execute(
        "ALTER TABLE fact.document ALTER COLUMN parse_status "
        "SET DEFAULT 'PENDING'::document_parse_status"
    )
    op.execute("ALTER TABLE fact.document DROP COLUMN IF EXISTS download_status")
    op.execute("DROP TYPE IF EXISTS download_status")
