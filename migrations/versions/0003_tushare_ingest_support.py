"""tushare ingestion support columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

- raw.source_record.schema_signature：数据集字段布局的签名（CHAR(64)），
  Schema 变化熔断与版本化回滚的依据（issue #3）；
- ops.ingest_run.lease_owner / lease_expires_at：调度窗口租约与心跳，
  崩溃后 RUNNING 任务的恢复判定（issue #3 并发/崩溃不变量）；
- ops.ingest_run.parent_run_id：断点续跑/重试与父运行的显式关联。

回滚策略：本修订仅新增可空列，downgrade 直接 DROP COLUMN，无数据破坏。
"""

from __future__ import annotations

from alembic import op

revision = "0003_tushare_ingest_support"
down_revision = "0002_fact_finance_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE raw.source_record ADD COLUMN schema_signature CHAR(64)")
    op.execute(
        "ALTER TABLE ops.ingest_run ADD COLUMN lease_owner VARCHAR(200)"
    )
    op.execute("ALTER TABLE ops.ingest_run ADD COLUMN lease_expires_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE ops.ingest_run ADD COLUMN parent_run_id UUID "
        "REFERENCES ops.ingest_run(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ops.ingest_run DROP COLUMN IF EXISTS parent_run_id")
    op.execute("ALTER TABLE ops.ingest_run DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE ops.ingest_run DROP COLUMN IF EXISTS lease_owner")
    op.execute("ALTER TABLE raw.source_record DROP COLUMN IF EXISTS schema_signature")
