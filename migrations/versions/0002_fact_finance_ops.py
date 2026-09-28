"""fact / finance / ops tables, indexes and audit event

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29

- fact：文档、证据片段（pgvector 1024 维，HNSW 索引待 embedding 模型
  确认后另行迁移）、Claim、关联、Provenance、审核任务；
- finance：财务指标与观察（空值原样为 NULL，不写 0）；
- ops：图投影 Outbox（含原生状态机）与审计事件。

回滚策略：downgrade 按逆序删除本修订创建的表与索引；上一版本（0001）的
raw/master 数据不受影响。
"""

from __future__ import annotations

from alembic import op

revision = "0002_fact_finance_ops"
down_revision = "0001_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- fact：文档与证据 ----
    op.execute(
        """
        CREATE TABLE fact.document (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_type           VARCHAR(50) NOT NULL,
            source_system           VARCHAR(50) NOT NULL,
            external_id             VARCHAR(300),
            company_id              UUID REFERENCES master.company(id),
            title                   TEXT NOT NULL,
            published_at            TIMESTAMPTZ,
            source_url              TEXT,
            content_hash            CHAR(64) NOT NULL,
            local_object_key        TEXT,
            mime_type               VARCHAR(100),
            parse_status            document_parse_status NOT NULL DEFAULT 'PENDING',
            parser_version          VARCHAR(50),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_system, content_hash)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE fact.evidence_fragment (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id             UUID NOT NULL REFERENCES fact.document(id) ON DELETE CASCADE,
            page_number             INTEGER,
            section_title           TEXT,
            paragraph_index          INTEGER,
            char_start              INTEGER,
            char_end                INTEGER,
            quote_text              TEXT NOT NULL,
            normalized_text         TEXT,
            checksum                CHAR(64) NOT NULL,
            embedding               vector(1024),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, checksum),
            CHECK (page_number IS NULL OR page_number > 0),
            CHECK (char_end IS NULL OR char_start IS NULL OR char_end >= char_start)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_evidence_document_page "
        "ON fact.evidence_fragment (document_id, page_number)"
    )

    # ---- fact：Claim / 关联 / Provenance / 审核 ----
    op.execute(
        """
        CREATE TABLE fact.claim (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_entity_type     VARCHAR(50) NOT NULL,
            subject_entity_id       UUID NOT NULL,
            predicate_code          VARCHAR(100) NOT NULL,
            object_entity_type      VARCHAR(50),
            object_entity_id        UUID,
            object_value            JSONB,
            business_stage          VARCHAR(50),
            evidence_state          VARCHAR(50),
            claim_status            claim_status NOT NULL,
            valid_from              TIMESTAMPTZ,
            valid_to                TIMESTAMPTZ,
            temporal_precision      VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at           TIMESTAMPTZ,
            confidence              NUMERIC(5,4) NOT NULL,
            source_priority         INTEGER,
            extraction_method       VARCHAR(50) NOT NULL,
            ontology_version        VARCHAR(30) NOT NULL,
            mapping_version         VARCHAR(30),
            rule_version            VARCHAR(30),
            content_hash            CHAR(64) NOT NULL UNIQUE,
            reviewed_by             VARCHAR(200),
            reviewed_at             TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence >= 0 AND confidence <= 1),
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
            CHECK (superseded_at IS NULL OR superseded_at >= recorded_at),
            CHECK (
                object_entity_id IS NOT NULL
                OR object_value IS NOT NULL
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_claim_subject_predicate_status "
        "ON fact.claim (subject_entity_type, subject_entity_id, predicate_code, claim_status)"
    )
    op.execute(
        "CREATE INDEX idx_claim_temporal "
        "ON fact.claim (valid_from, valid_to, recorded_at, superseded_at)"
    )
    op.execute(
        """
        CREATE TABLE fact.claim_evidence (
            claim_id                UUID NOT NULL REFERENCES fact.claim(id) ON DELETE CASCADE,
            evidence_id             UUID NOT NULL REFERENCES fact.evidence_fragment(id),
            support_type            VARCHAR(30) NOT NULL,
            source_weight           NUMERIC(5,4),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (claim_id, evidence_id, support_type),
            CHECK (support_type IN ('SUPPORTS', 'CONTRADICTS', 'QUALIFIES', 'SUPERSEDES')),
            CHECK (source_weight IS NULL OR (source_weight >= 0 AND source_weight <= 1))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE fact.provenance_entry (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id               VARCHAR(300) NOT NULL,
            entity_type             VARCHAR(80) NOT NULL,
            activity_id             VARCHAR(300) NOT NULL,
            agent_id                VARCHAR(300),
            source_document_id      UUID REFERENCES fact.document(id),
            source_record_id        UUID REFERENCES raw.source_record(id),
            source_location         TEXT,
            source_quote            TEXT,
            parent_entity_id        VARCHAR(300),
            used_entities           JSONB NOT NULL DEFAULT '[]'::jsonb,
            confidence              NUMERIC(5,4),
            checksum                CHAR(64) NOT NULL,
            sequence_id             BIGSERIAL,
            previous_checksum       CHAR(64),
            metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_provenance_entity "
        "ON fact.provenance_entry (entity_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE fact.review_task (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_type               VARCHAR(50) NOT NULL,
            claim_id                UUID REFERENCES fact.claim(id),
            status                  review_task_status NOT NULL DEFAULT 'OPEN',
            priority                VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
            assigned_to             VARCHAR(200),
            reason_codes            JSONB NOT NULL DEFAULT '[]'::jsonb,
            decision                VARCHAR(30),
            decision_reason         TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at            TIMESTAMPTZ,
            CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL'))
        )
        """
    )

    # ---- finance：财务观察 ----
    op.execute(
        """
        CREATE TABLE finance.financial_metric (
            metric_code             VARCHAR(100) PRIMARY KEY,
            name                    VARCHAR(300) NOT NULL,
            statement_type          VARCHAR(50) NOT NULL,
            unit_type               VARCHAR(30) NOT NULL,
            default_aggregation     VARCHAR(50) NOT NULL,
            tushare_field           VARCHAR(100),
            description             TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.financial_observation (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id              UUID REFERENCES master.company(id),
            security_id             UUID REFERENCES master.security(id),
            metric_code             VARCHAR(100) NOT NULL REFERENCES finance.financial_metric(metric_code),
            period_start            DATE,
            period_end              DATE NOT NULL,
            report_type             VARCHAR(30) NOT NULL,
            value                   NUMERIC,
            currency                CHAR(3),
            announced_at            TIMESTAMPTZ,
            update_flag             VARCHAR(30),
            source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE NULLS NOT DISTINCT (
                security_id, metric_code, period_end, report_type, update_flag
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_financial_observation_query "
        "ON finance.financial_observation (metric_code, period_end, security_id)"
    )
    op.execute(
        """
        CREATE TABLE finance.business_segment_observation (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id              UUID NOT NULL REFERENCES master.company(id),
            security_id             UUID REFERENCES master.security(id),
            period_end              DATE NOT NULL,
            segment_type            VARCHAR(30) NOT NULL,
            raw_segment_name        TEXT NOT NULL,
            mapped_product_id       UUID REFERENCES master.product(id),
            revenue                 NUMERIC,
            cost                    NUMERIC,
            profit                  NUMERIC,
            currency                CHAR(3),
            source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
            mapping_status          VARCHAR(30) NOT NULL DEFAULT 'UNMAPPED',
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (segment_type IN ('PRODUCT', 'INDUSTRY', 'REGION', 'SALES_MODE')),
            CHECK (mapping_status IN ('UNMAPPED', 'CANDIDATE', 'ACCEPTED', 'REJECTED'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_business_segment_company_period "
        "ON finance.business_segment_observation (company_id, period_end DESC)"
    )

    # ---- ops：图投影 Outbox + 审计事件 ----
    op.execute(
        """
        CREATE TABLE ops.graph_outbox (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            aggregate_type          VARCHAR(50) NOT NULL,
            aggregate_id            UUID NOT NULL,
            event_type              VARCHAR(80) NOT NULL,
            payload                 JSONB NOT NULL,
            idempotency_key         VARCHAR(200) NOT NULL UNIQUE,
            status                  graph_outbox_status NOT NULL DEFAULT 'PENDING',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            available_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_at               TIMESTAMPTZ,
            locked_by               VARCHAR(200),
            processed_at            TIMESTAMPTZ,
            retry_count             INTEGER NOT NULL DEFAULT 0,
            last_error              TEXT,
            dead_lettered_at        TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_graph_outbox_pending "
        "ON ops.graph_outbox (available_at, created_at) "
        "WHERE processed_at IS NULL AND dead_lettered_at IS NULL"
    )
    op.execute(
        """
        CREATE TABLE ops.audit_event (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type             VARCHAR(80) NOT NULL,
            entity_id               UUID NOT NULL,
            event_type              VARCHAR(100) NOT NULL,
            payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,
            trace_id                VARCHAR(64),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_audit_event_entity "
        "ON ops.audit_event (entity_type, entity_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.audit_event")
    op.execute("DROP INDEX IF EXISTS ops.idx_graph_outbox_pending")
    op.execute("DROP TABLE IF EXISTS ops.graph_outbox")
    op.execute("DROP TABLE IF EXISTS finance.business_segment_observation")
    op.execute("DROP TABLE IF EXISTS finance.financial_observation")
    op.execute("DROP TABLE IF EXISTS finance.financial_metric")
    op.execute("DROP TABLE IF EXISTS fact.review_task")
    op.execute("DROP TABLE IF EXISTS fact.provenance_entry")
    op.execute("DROP TABLE IF EXISTS fact.claim_evidence")
    op.execute("DROP INDEX IF EXISTS fact.idx_claim_temporal")
    op.execute("DROP INDEX IF EXISTS fact.idx_claim_subject_predicate_status")
    op.execute("DROP TABLE IF EXISTS fact.claim")
    op.execute("DROP TABLE IF EXISTS fact.evidence_fragment")
    op.execute("DROP TABLE IF EXISTS fact.document")
