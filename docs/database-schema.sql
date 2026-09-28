-- ontologyMVP PostgreSQL schema baseline
-- Target: PostgreSQL 15+ with pgcrypto and pgvector
-- This file is a design baseline and must stay consistent with:
--   - migrations/versions/0001_base_schemas_raw_master.py
--   - migrations/versions/0002_fact_finance_ops.py
--   - src/domain/enums.py (key status machines)
-- Production applies it through Alembic migrations, never by hand.
-- Key status columns use PostgreSQL native enum types (created in migration 0001):
--   ingest_run_status / claim_status / review_task_status /
--   graph_outbox_status / document_parse_status

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS master;
CREATE SCHEMA IF NOT EXISTS fact;
CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS ops;

-- ---------------------------------------------------------------------------
-- Operations and ingestion
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.source_capability (
    source_system           VARCHAR(50) NOT NULL,
    api_name                VARCHAR(100) NOT NULL,
    status                  VARCHAR(40) NOT NULL,
    checked_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    response_latency_ms     INTEGER,
    detail                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_system, api_name),
    CHECK (status IN (
        'AVAILABLE',
        'NO_PERMISSION',
        'SEPARATE_PERMISSION_REQUIRED',
        'RATE_LIMITED',
        'SCHEMA_CHANGED',
        'NETWORK_ERROR',
        'UNKNOWN'
    ))
);

CREATE TABLE IF NOT EXISTS ops.ingest_run (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_name            VARCHAR(100) NOT NULL,
    source_system           VARCHAR(50) NOT NULL,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ,
    -- 采集任务状态机（与 src/domain/enums.py IngestRunStatus 一致）：
    -- CREATED -> RUNNING -> {PARTIAL_SUCCESS | SUCCEEDED | FAILED_RETRYABLE
    --                         | FAILED_FINAL | CANCELLED}
    -- FAILED_RETRYABLE -> RUNNING | FAILED_FINAL | CANCELLED
    -- PARTIAL_SUCCESS -> RUNNING | SUCCEEDED | FAILED_FINAL
    -- 重试保留前次错误与游标；终态无审计不回 RUNNING
    status                  ingest_run_status NOT NULL DEFAULT 'CREATED',
    cursor_state            JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_count           INTEGER NOT NULL DEFAULT 0,
    rows_received           INTEGER NOT NULL DEFAULT 0,
    rows_inserted           INTEGER NOT NULL DEFAULT 0,
    rows_updated            INTEGER NOT NULL DEFAULT 0,
    rows_rejected           INTEGER NOT NULL DEFAULT 0,
    error_detail            JSONB,
    trace_id                VARCHAR(64) NOT NULL,
    -- 调度租约与心跳（migration 0003）：崩溃后 RUNNING 任务的恢复判定依据
    lease_owner             VARCHAR(200),
    lease_expires_at        TIMESTAMPTZ,
    -- 断点续跑/重试与父运行的显式关联
    parent_run_id           UUID REFERENCES ops.ingest_run(id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_run_dataset_started
ON ops.ingest_run (dataset_name, started_at DESC);

CREATE TABLE IF NOT EXISTS raw.source_record (
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
    -- 数据集字段布局签名（migration 0003）：Schema 变化熔断与版本化回滚依据
    schema_signature        CHAR(64),
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (source_system, api_name, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_source_record_lookup
ON raw.source_record (source_system, api_name, source_key, retrieved_at DESC);

-- ---------------------------------------------------------------------------
-- Master data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS master.company (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name                  VARCHAR(500) NOT NULL,
    legal_name                      VARCHAR(500),
    unified_social_credit_code      VARCHAR(50),
    company_type                    VARCHAR(40) NOT NULL DEFAULT 'LISTED_COMPANY',
    country_code                    CHAR(2) NOT NULL DEFAULT 'CN',
    status                          VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
    first_source_record_id          UUID REFERENCES raw.source_record(id),
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 注意：NULLS NOT DISTINCT 意味着全库最多一家"无统一社会信用代码"的公司；
    -- 尚未取得信用代码的主体需由标准化层（#4）先分配占位值再落库
    UNIQUE NULLS NOT DISTINCT (unified_social_credit_code)
);

CREATE INDEX IF NOT EXISTS idx_company_canonical_name
ON master.company (canonical_name);

CREATE TABLE IF NOT EXISTS master.exchange (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                    VARCHAR(20) NOT NULL UNIQUE,
    name                    VARCHAR(200) NOT NULL,
    country_code            CHAR(2) NOT NULL DEFAULT 'CN'
);

CREATE TABLE IF NOT EXISTS master.security (
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
);

CREATE TABLE IF NOT EXISTS master.company_security (
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
);

CREATE TABLE IF NOT EXISTS master.industry (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxonomy                VARCHAR(30) NOT NULL,
    taxonomy_version        VARCHAR(50) NOT NULL,
    level_code              VARCHAR(10) NOT NULL,
    external_code           VARCHAR(50) NOT NULL,
    name                    VARCHAR(300) NOT NULL,
    parent_id               UUID REFERENCES master.industry(id),
    UNIQUE (taxonomy, taxonomy_version, external_code)
);

CREATE TABLE IF NOT EXISTS master.company_industry_membership (
    company_id              UUID NOT NULL REFERENCES master.company(id),
    industry_id             UUID NOT NULL REFERENCES master.industry(id),
    valid_from              TIMESTAMPTZ,
    valid_to                TIMESTAMPTZ,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at           TIMESTAMPTZ,
    source_record_id        UUID REFERENCES raw.source_record(id),
    PRIMARY KEY (company_id, industry_id, recorded_at),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS master.concept (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_platform         VARCHAR(30) NOT NULL,
    external_code           VARCHAR(100) NOT NULL,
    name                    VARCHAR(300) NOT NULL,
    description             TEXT,
    UNIQUE (source_platform, external_code)
);

CREATE TABLE IF NOT EXISTS master.company_concept_membership (
    company_id              UUID NOT NULL REFERENCES master.company(id),
    concept_id              UUID NOT NULL REFERENCES master.concept(id),
    snapshot_date           DATE NOT NULL,
    is_member               BOOLEAN NOT NULL DEFAULT TRUE,
    source_record_id        UUID REFERENCES raw.source_record(id),
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, concept_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS master.theme (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    iri                     VARCHAR(500) NOT NULL UNIQUE,
    canonical_name          VARCHAR(300) NOT NULL,
    description             TEXT,
    ontology_version        VARCHAR(30) NOT NULL,
    active                  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS master.product (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    iri                     VARCHAR(500) NOT NULL UNIQUE,
    canonical_name          VARCHAR(300) NOT NULL,
    product_type            VARCHAR(50) NOT NULL DEFAULT 'PRODUCT',
    parent_product_id       UUID REFERENCES master.product(id),
    ontology_version        VARCHAR(30) NOT NULL,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS master.product_term_mapping (
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
);

CREATE INDEX IF NOT EXISTS idx_product_term_normalized
ON master.product_term_mapping (normalized_term);

-- ---------------------------------------------------------------------------
-- Documents, evidence, claims and provenance
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact.document (
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
    -- 下载状态机（migration 0006）
    download_status         download_status NOT NULL DEFAULT 'DISCOVERED',
    -- 解析状态机 document_parse_status：PENDING/PARSING/PARSED/
    -- FAILED_RETRYABLE/FAILED_FINAL/SKIPPED/PARSE_NEEDS_REVIEW(0006 追加)
    parse_status            document_parse_status NOT NULL DEFAULT 'PENDING',
    parser_version          VARCHAR(50),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, content_hash)
);

CREATE TABLE IF NOT EXISTS fact.evidence_fragment (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             UUID NOT NULL REFERENCES fact.document(id) ON DELETE CASCADE,
    page_number             INTEGER,
    section_title           TEXT,
    paragraph_index         INTEGER,
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
);

CREATE INDEX IF NOT EXISTS idx_evidence_document_page
ON fact.evidence_fragment (document_id, page_number);

-- Create the vector index only after enough rows exist and after confirming
-- the selected embedding model uses 1024 dimensions.
-- CREATE INDEX idx_evidence_embedding_hnsw
-- ON fact.evidence_fragment USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS fact.claim (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_entity_type     VARCHAR(50) NOT NULL,
    subject_entity_id       UUID NOT NULL,
    predicate_code          VARCHAR(100) NOT NULL,
    object_entity_type      VARCHAR(50),
    object_entity_id        UUID,
    object_value            JSONB,
    business_stage          VARCHAR(50),
    evidence_state          VARCHAR(50),
    -- Claim 状态机（ADR-0002，与 claim_status 原生枚举一致）：
    -- EXTRACTED -> VALIDATED | NEEDS_REVIEW | REJECTED
    -- VALIDATED -> ACCEPTED | NEEDS_REVIEW
    -- NEEDS_REVIEW -> ACCEPTED | REJECTED
    -- ACCEPTED -> CONTRADICTED | SUPERSEDED
    -- CONTRADICTED -> ACCEPTED | SUPERSEDED
    -- 历史 Claim 不物理删除；ACCEPTED 与 Graph Outbox 同事务产生
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
);

CREATE INDEX IF NOT EXISTS idx_claim_subject_predicate_status
ON fact.claim (subject_entity_type, subject_entity_id, predicate_code, claim_status);

CREATE INDEX IF NOT EXISTS idx_claim_temporal
ON fact.claim (valid_from, valid_to, recorded_at, superseded_at);

CREATE TABLE IF NOT EXISTS fact.claim_evidence (
    claim_id                UUID NOT NULL REFERENCES fact.claim(id) ON DELETE CASCADE,
    evidence_id             UUID NOT NULL REFERENCES fact.evidence_fragment(id),
    support_type            VARCHAR(30) NOT NULL,
    source_weight           NUMERIC(5,4),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, evidence_id, support_type),
    CHECK (support_type IN ('SUPPORTS', 'CONTRADICTS', 'QUALIFIES', 'SUPERSEDES')),
    CHECK (source_weight IS NULL OR (source_weight >= 0 AND source_weight <= 1))
);

CREATE TABLE IF NOT EXISTS fact.provenance_entry (
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
);

CREATE INDEX IF NOT EXISTS idx_provenance_entity
ON fact.provenance_entry (entity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fact.review_task (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type               VARCHAR(50) NOT NULL,
    claim_id                UUID REFERENCES fact.claim(id),
    -- 审核任务状态机 review_task_status：OPEN/IN_PROGRESS/COMPLETED/CANCELLED
    status                  review_task_status NOT NULL DEFAULT 'OPEN',
    priority                VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    assigned_to             VARCHAR(200),
    reason_codes            JSONB NOT NULL DEFAULT '[]'::jsonb,
    decision                VARCHAR(30),
    decision_reason         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ,
    CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL'))
);

-- ---------------------------------------------------------------------------
-- Financial observations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finance.financial_metric (
    metric_code             VARCHAR(100) PRIMARY KEY,
    name                    VARCHAR(300) NOT NULL,
    statement_type          VARCHAR(50) NOT NULL,
    unit_type               VARCHAR(30) NOT NULL,
    default_aggregation     VARCHAR(50) NOT NULL,
    tushare_field           VARCHAR(100),
    description             TEXT
);

CREATE TABLE IF NOT EXISTS finance.financial_observation (
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
    -- 公告日期属于观察值身份（migration 0005）：同口径重述独立保留，
    -- 由当前值视图按最新公告日期选择，历史不覆盖
    UNIQUE NULLS NOT DISTINCT (
        security_id,
        metric_code,
        period_end,
        report_type,
        update_flag,
        announced_at
    )
);

CREATE INDEX IF NOT EXISTS idx_financial_observation_query
ON finance.financial_observation (metric_code, period_end, security_id);

CREATE TABLE IF NOT EXISTS finance.business_segment_observation (
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
);

CREATE INDEX IF NOT EXISTS idx_business_segment_company_period
ON finance.business_segment_observation (company_id, period_end DESC);

-- ---------------------------------------------------------------------------
-- Graph projection outbox
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.graph_outbox (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type          VARCHAR(50) NOT NULL,
    aggregate_id            UUID NOT NULL,
    event_type              VARCHAR(80) NOT NULL,
    payload                 JSONB NOT NULL,
    idempotency_key         VARCHAR(200) NOT NULL UNIQUE,
    -- Outbox 状态机（与 graph_outbox_status 原生枚举一致）：
    -- PENDING -> PROCESSING -> {PROCESSED | PENDING(重试) | DEAD_LETTERED}
    status                  graph_outbox_status NOT NULL DEFAULT 'PENDING',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at               TIMESTAMPTZ,
    locked_by               VARCHAR(200),
    processed_at            TIMESTAMPTZ,
    retry_count             INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    dead_lettered_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_graph_outbox_pending
ON ops.graph_outbox (available_at, created_at)
WHERE processed_at IS NULL AND dead_lettered_at IS NULL;

-- 审计事件：与 Claim 状态变更同事务写入（issue #2 事务边界）
CREATE TABLE IF NOT EXISTS ops.audit_event (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type             VARCHAR(80) NOT NULL,
    entity_id               UUID NOT NULL,
    event_type              VARCHAR(100) NOT NULL,
    payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id                VARCHAR(64),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_event_entity
ON ops.audit_event (entity_type, entity_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Document pipeline (migration 0006, issue #6)
-- ---------------------------------------------------------------------------

-- 下载状态机（原生枚举）
CREATE TYPE download_status AS ENUM (
    'DISCOVERED', 'DOWNLOAD_PENDING', 'DOWNLOADING', 'DOWNLOADED',
    'DOWNLOAD_FAILED_RETRYABLE', 'DOWNLOAD_FAILED_FINAL'
);
-- 解析状态机 document_parse_status 追加 PARSE_NEEDS_REVIEW（纯扫描→人工审核，
-- 不进自动抽取）；ALTER TYPE ADD VALUE 追加于末尾

-- 文档版本：同 URL 内容 Hash 变化 → 新版本，旧版本与文件不覆盖
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
    downloaded_at           TIMESTAMPTZ,
    parser_version          VARCHAR(50),
    page_count              INTEGER,
    text_stats              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version),
    UNIQUE (document_id, content_hash)
);
CREATE INDEX idx_document_version_document
ON fact.document_version (document_id, version DESC);

-- 片段引用具体解析版本（0006 增列）
-- fact.evidence_fragment.document_version_id UUID REFERENCES fact.document_version(id)

-- ---------------------------------------------------------------------------
-- Standardization layer (migration 0004, issue #4)
-- ---------------------------------------------------------------------------

-- 标准化运行：复用 ingest_run_status 状态机；水位/租约/父运行与映射版本
CREATE TABLE IF NOT EXISTS ops.normalization_run (
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
);

-- 审计事件流：VERSION_APPLIED / REJECTED / MAPPED / CORRECTION
CREATE TABLE IF NOT EXISTS ops.normalization_event (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID NOT NULL REFERENCES ops.normalization_run(id),
    event_type              VARCHAR(30) NOT NULL,
    entity_type             VARCHAR(80),
    entity_ref              VARCHAR(300),
    detail                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id        UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (event_type IN ('VERSION_APPLIED', 'REJECTED', 'MAPPED', 'CORRECTION'))
);

-- namechange → 历史别名
CREATE TABLE IF NOT EXISTS master.security_name_history (
    security_id             UUID NOT NULL REFERENCES master.security(id),
    name                    VARCHAR(300) NOT NULL,
    start_date              DATE,
    end_date                DATE,
    ann_date                DATE,
    source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (security_id, name, start_date)
);

-- stock_company 画像字段（chairman 等映射目标）
CREATE TABLE IF NOT EXISTS master.company_profile (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              UUID NOT NULL UNIQUE REFERENCES master.company(id),
    chairman_name           VARCHAR(200),
    general_manager_name    VARCHAR(200),
    board_secretary_name    VARCHAR(200),
    registered_capital      NUMERIC,
    founded_date            DATE,
    province                VARCHAR(100),
    city                    VARCHAR(100),
    employees               NUMERIC,
    main_part_business      TEXT,
    source_record_id        UUID NOT NULL REFERENCES raw.source_record(id),
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 股东持有观察：仅 HOLDS 候选/事实，绝不产生 CONTROLS
CREATE TABLE IF NOT EXISTS fact.holding_observation (
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
);

-- 标准化幂等键：同一公司/报告期/分部类型/原始披露名收敛为一条
CREATE UNIQUE INDEX IF NOT EXISTS idx_business_segment_identity
ON finance.business_segment_observation (company_id, period_end, segment_type, raw_segment_name);

-- 确定性"当前有效财务值"视图（选择规则 = tushare.yaml financial_version_selection：
-- 保留全部版本；口径 CONSOLIDATED > PARENT > UNKNOWN；最新公告日期优先，再最新 update_flag）
CREATE OR REPLACE VIEW finance.v_financial_observation_current AS
SELECT o.*
FROM (
    SELECT fo.*,
           row_number() OVER (
               PARTITION BY fo.security_id, fo.metric_code, fo.period_end
               ORDER BY
                   CASE fo.report_type
                       WHEN '1' THEN 1 WHEN '4' THEN 1
                       WHEN '2' THEN 2 WHEN '5' THEN 2 WHEN '6' THEN 2
                       ELSE 3
                   END ASC,
                   fo.announced_at DESC NULLS LAST,
                   fo.update_flag DESC NULLS LAST,
                   fo.recorded_at DESC
           ) AS version_rank
    FROM finance.financial_observation fo
) o
WHERE o.version_rank = 1;

-- ---------------------------------------------------------------------------
-- Required application-level invariants
-- ---------------------------------------------------------------------------
-- 1. ACCEPTED operating claims require at least one Evidence or SourceRecord.
-- 2. MASS_PRODUCTION cannot be supported only by concept membership.
-- 3. REVENUE_DISCLOSED requires a report period or business segment observation.
-- 4. An operating graph relationship must carry claim_id.
-- 5. UNKNOWN/EVIDENCE_INSUFFICIENT must not be converted to FALSE.
-- 6. Claim, provenance and graph_outbox are inserted in one transaction.
-- 7. Neo4j can be fully rebuilt from ACCEPTED claims and master data.
