"""outbox lease column for worker crash recovery

Revision ID: 0007_outbox_lease
Revises: 0006_document_pipeline
Create Date: 2026-09-29

issue #8：Worker 崩溃后租约过期恢复——需要 lease_expires_at 列
标记租约到期时间，与 ingest_run 的租约机制对称。

回滚策略：downgrade 删除新增列，无数据破坏。
"""

from __future__ import annotations

from alembic import op

revision = "0007_outbox_lease"
down_revision = "0006_document_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ops.graph_outbox ADD COLUMN lease_expires_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ops.graph_outbox DROP COLUMN IF EXISTS lease_expires_at")
