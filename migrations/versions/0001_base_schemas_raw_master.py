"""base schemas, native status enums, raw + master layer

Revision ID: 0001
Revises:
Create Date: 2026-09-29

五个关键状态机使用 PostgreSQL 原生枚举（ingest_run_status / claim_status /
review_task_status / graph_outbox_status / document_parse_status），值与
src/domain/enums.py 及 docs/database-schema.sql 逐值一致，由
tests/db/test_state_machines.py 守护。

回滚策略：downgrade 按逆序删除本修订创建的表/枚举/Schema。扩展
（pgcrypto/vector）为共享对象，downgrade 刻意保留（不可逆项说明：
若确需移除，按"扩展-迁移-收缩"人工执行 DROP EXTENSION，并先确认
无其他数据库对象依赖）。
"""

from __future__ import annotations

from alembic import op

revision = "0001_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 扩展（PostgreSQL 15+ / pgvector） ----
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- Schema 拓扑 ----
    for schema in ("raw", "master", "fact", "finance", "ops"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # ---- 关键状态机原生枚举（与 src/domain/enums.py 同步） ----
    op.execute(
        "CREATE TYPE ingest_run_status AS ENUM ("
        "'CREATED', 'RUNNING', 'PARTIAL_SUCCESS', 'SUCCEEDED', "
        "'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCELLED')"
    )
    op.execute(
        "CREATE TYPE claim_status AS ENUM ("
        "'EXTRACTED', 'VALIDATED', 'NEEDS_REVIEW', 'ACCEPTED', "
        "'CONTRADICTED', 'SUPERSEDED', 'REJECTED')"
    )
    op.execute(
        "CREATE TYPE review_task_status AS ENUM "
        "('OPEN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')"
    )
    op.execute(
        "CREATE TYPE graph_outbox_status AS ENUM "
        "('PENDING', 'PROCESSING', 'PROCESSED', 'DEAD_LETTERED')"
    )
    op.execute(
        "CREATE TYPE document_parse_status AS ENUM ("
        "'PENDING', 'PARSING', 'PARSED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'SKIPPED')"
    )

    # ---- ops：采集能力与任务 ----
    op.execute(
        """
        CREATE TABLE ops.source_capability (
            source_system           VARCHAR(50) NOT NULL,
            api_name                VARCHAR(100) NOT NULL,
            status                  VARCHAR(40) NOT NULL,
            checked_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            response_latency_ms     INTEGER,
            detail                  JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (source_system, api_name),
            CHECK (status IN (
                'AVAILABLE', 'NO_PERMISSION', 'SEPARATE_PERMISSION_REQUIRED',
                'RATE_LIMITED', 'SCHEMA_CHANGED', 'NETWORK_ERROR', 'UNKNOWN'
            ))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops.ingest_run (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dataset_name            VARCHAR(100) NOT NULL,
            source_system           VARCHAR(50) NOT NULL,
            started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at             TIMESTAMPTZ,
            status                  ingest_run_status NOT NULL DEFAULT 'CREATED',
            cursor_state            JSONB NOT NULL DEFAULT '{}'::jsonb,
            request_count           INTEGER NOT NULL DEFAULT 0,
            rows_received           INTEGER NOT NULL DEFAULT 0,
            rows_inserted           INTEGER NOT NULL DEFAULT 0,
            rows_updated            INTEGER NOT NULL DEFAULT 0,
            rows_rejected           INTEGER NOT NULL DEFAULT 0,
            error_detail            JSONB,
            trace_id                VARCHAR(64) NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_ingest_run_dataset_started "
        "ON ops.ingest_run (dataset_name, started_at DESC)"
    )

    # ---- raw：原始层（幂等键：source_system+api_name+payload_hash） ----
    op.execute(
        """
        CREATE TABLE raw.source_record (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_system           VARCHAR(50) NOT NULL,
            api_name                VARCHAR(100) NOT NULL,
            source_key              VARCHAR(500),
            request_params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            retrieved_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_effective_at     TIMESTAMPTZ,
            payload_hash            CHAR(64) NOT NULL,
            raw_payload             JSONB NOT NULL,
            ingest_run_id           UUID NOT NULL REFERENCES ops.ingest_run(id),
            schema_version          VARCHAR(50),
            is_current              BOOLEAN NOT NULL DEFAULT TRUE,
            UNIQUE (source_system, api_name, payload_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_source_record_lookup "
        "ON raw.source_record (source_system, api_name, source_key, retrieved_at DESC)"
    )

    # ---- master：主数据 ----
    op.execute(
        """
        CREATE TABLE master.company (
            id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name                  VARCHAR(500) NOT NULL,
            legal_name                      VARCHAR(500),
            unified_social_credit_code       VARCHAR(50),
            company_type                    VARCHAR(40) NOT NULL DEFAULT 'LISTED_COMPANY',
            country_code                    CHAR(2) NOT NULL DEFAULT 'CN',
            status                          VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            first_source_record_id          UUID REFERENCES raw.source_record(id),
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE NULLS NOT DISTINCT (unified_social_credit_code)
        )
        """
    )
    op.execute("CREATE INDEX idx_company_canonical_name ON master.company (canonical_name)")

    op.execute(
        """
        CREATE TABLE master.exchange (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code                    VARCHAR(20) NOT NULL UNIQUE,
            name                    VARCHAR(200) NOT NULL,
            country_code            CHAR(2) NOT NULL DEFAULT 'CN'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.security (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ts_code                 VARCHAR(20) NOT NULL UNIQUE,
            symbol                  VARCHAR(20) NOT NULL,
            name                    VARCHAR(100) NOT NULL,
            security_type           VARCHAR(30) NOT NULL DEFAULT 'STOCK',
            market                  VARCHAR(50),
            exchange_id             UUID NOT NULL REFERENCES master.exchange(id),
            list_date               DATE,
            delist_date             DATE,
            status                  VARCHAR(30) NOT NULL,
            source_record_id        UUID REFERENCES raw.source_record(id),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (exchange_id, symbol)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.company_security (
            company_id              UUID NOT NULL REFERENCES master.company(id),
            security_id             UUID NOT NULL REFERENCES master.security(id),
            relation_type           VARCHAR(30) NOT NULL DEFAULT 'ISSUES',
            valid_from              TIMESTAMPTZ,
            valid_to                TIMESTAMPTZ,
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at           TIMESTAMPTZ,
            source_record_id        UUID REFERENCES raw.source_record(id),
            PRIMARY KEY (company_id, security_id, recorded_at),
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.industry (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            taxonomy                VARCHAR(30) NOT NULL,
            taxonomy_version        VARCHAR(50) NOT NULL,
            level_code              VARCHAR(10) NOT NULL,
            external_code           VARCHAR(50) NOT NULL,
            name                    VARCHAR(300) NOT NULL,
            parent_id               UUID REFERENCES master.industry(id),
            UNIQUE (taxonomy, taxonomy_version, external_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.company_industry_membership (
            company_id              UUID NOT NULL REFERENCES master.company(id),
            industry_id             UUID NOT NULL REFERENCES master.industry(id),
            valid_from              TIMESTAMPTZ,
            valid_to                TIMESTAMPTZ,
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at           TIMESTAMPTZ,
            source_record_id        UUID REFERENCES raw.source_record(id),
            PRIMARY KEY (company_id, industry_id, recorded_at),
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.concept (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_platform         VARCHAR(30) NOT NULL,
            external_code           VARCHAR(100) NOT NULL,
            name                    VARCHAR(300) NOT NULL,
            description             TEXT,
            UNIQUE (source_platform, external_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.company_concept_membership (
            company_id              UUID NOT NULL REFERENCES master.company(id),
            concept_id              UUID NOT NULL REFERENCES master.concept(id),
            snapshot_date           DATE NOT NULL,
            is_member               BOOLEAN NOT NULL DEFAULT TRUE,
            source_record_id        UUID REFERENCES raw.source_record(id),
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (company_id, concept_id, snapshot_date)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.theme (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            iri                     VARCHAR(500) NOT NULL UNIQUE,
            canonical_name          VARCHAR(300) NOT NULL,
            description             TEXT,
            ontology_version        VARCHAR(30) NOT NULL,
            active                  BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.product (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            iri                     VARCHAR(500) NOT NULL UNIQUE,
            canonical_name          VARCHAR(300) NOT NULL,
            product_type            VARCHAR(50) NOT NULL DEFAULT 'PRODUCT',
            parent_product_id       UUID REFERENCES master.product(id),
            ontology_version        VARCHAR(30) NOT NULL,
            active                  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master.product_term_mapping (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            raw_term                VARCHAR(1000) NOT NULL,
            normalized_term         VARCHAR(1000) NOT NULL,
            product_id              UUID NOT NULL REFERENCES master.product(id),
            source_system           VARCHAR(50),
            mapping_method          VARCHAR(30) NOT NULL,
            confidence              NUMERIC(5,4) NOT NULL,
            ontology_version        VARCHAR(30) NOT NULL,
            review_status           VARCHAR(30) NOT NULL,
            reviewed_by             VARCHAR(200),
            reviewed_at             TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (confidence >= 0 AND confidence <= 1),
            CHECK (mapping_method IN ('MANUAL', 'RULE', 'LLM_ASSISTED')),
            CHECK (review_status IN ('PENDING', 'ACCEPTED', 'REJECTED'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_product_term_normalized ON master.product_term_mapping (normalized_term)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS master.product_term_mapping")
    op.execute("DROP TABLE IF EXISTS master.product")
    op.execute("DROP TABLE IF EXISTS master.theme")
    op.execute("DROP TABLE IF EXISTS master.company_concept_membership")
    op.execute("DROP TABLE IF EXISTS master.concept")
    op.execute("DROP TABLE IF EXISTS master.company_industry_membership")
    op.execute("DROP TABLE IF EXISTS master.industry")
    op.execute("DROP TABLE IF EXISTS master.company_security")
    op.execute("DROP TABLE IF EXISTS master.security")
    op.execute("DROP TABLE IF EXISTS master.exchange")
    op.execute("DROP TABLE IF EXISTS master.company")
    op.execute("DROP TABLE IF EXISTS raw.source_record")
    op.execute("DROP INDEX IF EXISTS ops.idx_ingest_run_dataset_started")
    op.execute("DROP INDEX IF EXISTS raw.idx_source_record_lookup")
    op.execute("DROP INDEX IF EXISTS master.idx_product_term_normalized")
    op.execute("DROP INDEX IF EXISTS master.idx_company_canonical_name")
    op.execute("DROP TABLE IF EXISTS ops.ingest_run")
    op.execute("DROP TABLE IF EXISTS ops.source_capability")
    # 扩展保留：共享对象，移除需人工按"扩展-迁移-收缩"确认后执行
    for type_name in (
        "document_parse_status",
        "graph_outbox_status",
        "review_task_status",
        "claim_status",
        "ingest_run_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
    for schema in ("fact", "finance", "ops", "master", "raw"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} RESTRICT")
