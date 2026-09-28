"""standardization support tables and financial current-value view

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29

- ops.normalization_run：标准化运行（复用 #2 的 ingest_run_status 状态机），
  含映射版本、水位、租约与父运行关联（issue #4 可版本化/可重放/可审计）；
- ops.normalization_event：REJECTED / VERSION_APPLIED / CORRECTION 审计事件流；
- master.security_name_history：namechange 历史别名（ts_code 有效期内唯一名称史）；
- master.company_profile：stock_company 映射出的画像字段（yaml target 无处安放
  的 chairman_name 等），随来源可追溯；
- fact.holding_observation：股东持有标准化观察（HOLDS 候选/事实）。
  本 PR 不产生 CONTROLS；Claim 升格属 #7；
- finance.v_financial_observation_current：确定性"当前有效财务值"视图，
  口径与版本偏好按 ontology/mappings/tushare.yaml financial_version_selection：
  scope CONSOLIDATED > PARENT > UNKNOWN；版本取最新公告日期，再取最新 update_flag；
  保留全部历史（retain_all_versions），视图只做选择不删除。

回滚策略：downgrade 删除视图与新增表，标准层可由 Raw + 指定映射版本重建。
"""

from __future__ import annotations

from alembic import op

revision = "0004_standardization_layer"
down_revision = "0003_tushare_ingest_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.normalization_run (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dataset_name            VARCHAR(100) NOT NULL,
            source_system           VARCHAR(50) NOT NULL,
            mapping_version         VARCHAR(50) NOT NULL,
            status                  ingest_run_status NOT NULL DEFAULT 'CREATED',
            started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at             TIMESTAMPTZ,
            rows_read               INTEGER NOT NULL DEFAULT 0,
            rows_written            INTEGER NOT NULL DEFAULT 0,
            rows_rejected           INTEGER NOT NULL DEFAULT 0,
            error_detail            JSONB,
            watermark               JSONB NOT NULL DEFAULT '{}'::jsonb,
            trace_id                VARCHAR(64) NOT NULL,
            lease_owner             VARCHAR(200),
            lease_expires_at        TIMESTAMPTZ,
            parent_run_id           UUID REFERENCES ops.normalization_run(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_normalization_run_dataset_started "
        "ON ops.normalization_run (dataset_name, started_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE ops.normalization_event (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id                  UUID NOT NULL REFERENCES ops.normalization_run(id),
            event_type              VARCHAR(30) NOT NULL,
            entity_type             VARCHAR(80),
            entity_ref              VARCHAR(300),
            detail                  JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_record_id        UUID,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (event_type IN ('VERSION_APPLIED', 'REJECTED', 'MAPPED', 'CORRECTION'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_normalization_event_run "
        "ON ops.normalization_event (run_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE master.security_name_history (
            security_id             UUID NOT NULL REFERENCES master.security(id),
            name                    VARCHAR(300) NOT NULL,
            start_date              DATE,
            end_date                DATE,
            ann_date                DATE,
            source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (security_id, name, start_date)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE master.company_profile (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id              UUID NOT NULL UNIQUE REFERENCES master.company(id),
            chairman_name           VARCHAR(200),
            general_manager_name    VARCHAR(200),
            board_secretary_name    VARCHAR(200),
            registered_capital       NUMERIC,
            founded_date            DATE,
            province                VARCHAR(100),
            city                    VARCHAR(100),
            employees               NUMERIC,
            main_part_business      TEXT,
            source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE fact.holding_observation (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            security_id             UUID NOT NULL REFERENCES master.security(id),
            holder_name             VARCHAR(300) NOT NULL,
            holder_type             VARCHAR(50),
            hold_amount             NUMERIC,
            hold_ratio              NUMERIC(10,6),
            end_date                DATE NOT NULL,
            source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE NULLS NOT DISTINCT (security_id, holder_name, end_date, hold_ratio)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_holding_observation_security "
        "ON fact.holding_observation (security_id, end_date DESC)"
    )

    # 标准层幂等所需唯一键：同一公司/报告期/分部类型/原始披露名的重放收敛为一条
    op.execute(
        "CREATE UNIQUE INDEX idx_business_segment_identity "
        "ON finance.business_segment_observation "
        "(company_id, period_end, segment_type, raw_segment_name)"
    )

    # 确定性"当前有效财务值"视图（选择规则来自 tushare.yaml financial_version_selection）
    op.execute(
        """
        CREATE VIEW finance.v_financial_observation_current AS
        SELECT o.*
        FROM (
            SELECT fo.*,
                   row_number() OVER (
                       PARTITION BY fo.security_id, fo.metric_code, fo.period_end
                       ORDER BY
                           CASE fo.report_type
                               WHEN '1' THEN 1 WHEN '4' THEN 1
                               WHEN '2' THEN 2 WHEN '5' THEN 2
                               ELSE 3
                           END ASC,
                           fo.announced_at DESC NULLS LAST,
                           fo.update_flag DESC NULLS LAST,
                           fo.recorded_at DESC
                   ) AS version_rank
            FROM finance.financial_observation fo
        ) o
        WHERE o.version_rank = 1
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS finance.v_financial_observation_current")
    op.execute("DROP INDEX IF EXISTS finance.idx_business_segment_identity")
    op.execute("DROP INDEX IF EXISTS fact.idx_holding_observation_security")
    op.execute("DROP TABLE IF EXISTS fact.holding_observation")
    op.execute("DROP TABLE IF EXISTS master.company_profile")
    op.execute("DROP TABLE IF EXISTS master.security_name_history")
    op.execute("DROP INDEX IF EXISTS ops.idx_normalization_event_run")
    op.execute("DROP TABLE IF EXISTS ops.normalization_event")
    op.execute("DROP INDEX IF EXISTS ops.idx_normalization_run_dataset_started")
    op.execute("DROP TABLE IF EXISTS ops.normalization_run")
