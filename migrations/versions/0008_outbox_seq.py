"""outbox monotonic sequence for reliable replay ordering

Revision ID: 0008_outbox_seq
Revises: 0007_outbox_lease
Create Date: 2026-09-29

issue #8：水位回放需要单调序号——UUIDv4 字典序无时间含义。
bigserial seq 列保证全表严格递增，重建回放按 seq > watermark 可靠命中。
"""

from __future__ import annotations

from alembic import op

revision = "0008_outbox_seq"
down_revision = "0007_outbox_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ops.graph_outbox ADD COLUMN seq BIGSERIAL"
    )
    op.execute(
        "CREATE INDEX idx_graph_outbox_seq ON ops.graph_outbox (seq)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ops.idx_graph_outbox_seq")
    op.execute("ALTER TABLE ops.graph_outbox DROP COLUMN IF EXISTS seq")
