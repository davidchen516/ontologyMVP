"""query audit records for the controlled query pipeline

Revision ID: 0009_query_audit
Revises: 0008_outbox_seq
Create Date: 2026-09-29

issue #9：查询链路必须可审计——保存原问题、规范化 QueryPlan、执行版本、
口径（PeriodRule）、trace_id、状态与错误类别。
query_id 唯一约束保证同一计划重试不产生多条矛盾审计记录（安全重执行策略）。
"""

from __future__ import annotations

from alembic import op

revision = "0009_query_audit"
down_revision = "0008_outbox_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA query")
    op.execute(
        """
        CREATE TABLE query.query_audit (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            query_id                UUID NOT NULL UNIQUE,
            natural_question        TEXT,
            plan                    JSONB NOT NULL,
            intent                  VARCHAR(30) NOT NULL,
            status                  VARCHAR(20) NOT NULL,
            error_category          VARCHAR(60),
            trace_id                VARCHAR(64),
            execution_version       VARCHAR(20) NOT NULL,
            period_rule             VARCHAR(50),
            duration_ms             INTEGER,
            response                JSONB,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_query_audit_created ON query.query_audit (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS query.query_audit")
    op.execute("DROP SCHEMA IF EXISTS query")
