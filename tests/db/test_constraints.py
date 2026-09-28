"""数据库约束测试：CHECK/UNIQUE/枚举/向量维度/财务空值语义。"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from src.domain.enums import ClaimStatus, IngestRunStatus


def test_claim_requires_object_entity_or_value(uow_factory) -> None:
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="约束测试公司")
        with pytest.raises(CheckViolation):
            uow.claims.insert(
                subject_entity_type="Company",
                subject_entity_id=company["id"],
                predicate_code="PRODUCES",
                content_hash=uuid.uuid4().hex,
                extraction_method="RULE",
                ontology_version="v1",
                confidence=0.5,
            )


def test_claim_confidence_bounds_enforced(uow_factory) -> None:
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="置信度公司")
        with pytest.raises(CheckViolation):
            uow.claims.insert(
                subject_entity_type="Company",
                subject_entity_id=company["id"],
                predicate_code="PRODUCES",
                content_hash=uuid.uuid4().hex,
                extraction_method="RULE",
                ontology_version="v1",
                confidence=1.5,
                object_value={"x": 1},
            )


def test_claim_temporal_ordering_enforced(uow_factory) -> None:
    with uow_factory.transaction() as uow:
        with pytest.raises(CheckViolation):
            uow._conn.execute(
                """
                INSERT INTO fact.claim (subject_entity_type, subject_entity_id,
                    predicate_code, claim_status, valid_from, valid_to,
                    confidence, extraction_method, ontology_version, content_hash)
                VALUES ('Company', gen_random_uuid(), 'X', 'EXTRACTED',
                        now(), now() - interval '1 day', 0.5, 'RULE', 'v1', %s)
                """,
                (uuid.uuid4().hex,),
            )


def test_claim_content_hash_unique(uow_factory) -> None:
    content_hash = uuid.uuid4().hex
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="唯一哈希公司")
        first = uow.claims.insert(
            subject_entity_type="Company",
            subject_entity_id=company["id"],
            predicate_code="PRODUCES",
            content_hash=content_hash,
            extraction_method="RULE",
            ontology_version="v1",
            confidence=0.5,
            object_value={"x": 1},
        )
        assert first["inserted"] is True
        with pytest.raises(UniqueViolation):
            uow._conn.execute(
                """
                INSERT INTO fact.claim (subject_entity_type, subject_entity_id,
                    predicate_code, claim_status, confidence, extraction_method,
                    ontology_version, content_hash, object_value)
                VALUES ('Company', %s, 'PRODUCES', 'EXTRACTED', 0.5, 'RULE', 'v1', %s, '{"y":2}')
                """,
                (company["id"], content_hash),
            )


def test_financial_null_value_stays_null_not_zero(uow_factory) -> None:
    """财务空值不得自动写成 0（负向验收）。"""
    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="tushare:cashflow", source_system="TUSHARE", trace_id="t"
        )
        record = uow.source_records.insert_idempotent(
            source_system="TUSHARE",
            api_name="cashflow",
            payload_hash=uuid.uuid4().hex,
            raw_payload={"value": None},
            ingest_run_id=run["id"],
        )
        uow._conn.execute(
            "INSERT INTO finance.financial_metric "
            "(metric_code, name, statement_type, unit_type, default_aggregation) "
            "VALUES ('NET_CF', '经营现金流', 'CASHFLOW', 'CNY', 'SUM')"
        )
        uow._conn.execute(
            """
            INSERT INTO finance.financial_observation
                (security_id, metric_code, period_end, report_type, value, source_record_id)
            VALUES (NULL, 'NET_CF', '2025-12-31', 'ANNUAL', NULL, %s)
            """,
            (record["id"],),
        )
        value = uow._conn.execute(
            "SELECT value FROM finance.financial_observation WHERE metric_code = 'NET_CF'"
        ).fetchone()[0]
        assert value is None, "空值必须保持 NULL，不得伪装为 0"


def test_evidence_embedding_dimension_enforced(uow_factory) -> None:
    """pgvector 1024 维约束：错误维度必须失败。"""
    with uow_factory.transaction() as uow:
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="CNINFO",
            title="年报", content_hash=uuid.uuid4().hex,
        )
        # 正确维度（1024）通过
        uow._conn.execute(
            """
            INSERT INTO fact.evidence_fragment
                (document_id, quote_text, checksum, embedding)
            VALUES (%s, 'q', %s, (%s))
            """,
            (
                document["id"],
                uuid.uuid4().hex,
                "[" + ",".join(["0.01"] * 1024) + "]",
            ),
        )
        # 错误维度（1023）失败
        with pytest.raises(psycopg.errors.DataError):
            uow._conn.execute(
                """
                INSERT INTO fact.evidence_fragment
                    (document_id, quote_text, checksum, embedding)
                VALUES (%s, 'q2', %s, (%s))
                """,
                (
                    document["id"],
                    uuid.uuid4().hex,
                    "[" + ",".join(["0.01"] * 1023) + "]",
                ),
            )


def test_evidence_page_number_positive(uow_factory) -> None:
    with uow_factory.transaction() as uow:
        document = uow.documents.insert(
            document_type="REPORT", source_system="CNINFO",
            title="文档", content_hash=uuid.uuid4().hex,
        )
        with pytest.raises(CheckViolation):
            uow.claim_evidence.insert_evidence_fragment(
                document_id=document["id"],
                quote_text="x",
                checksum=uuid.uuid4().hex,
                page_number=0,
            )


def test_invalid_enum_value_rejected_by_native_type(uow_factory) -> None:
    """原生枚举拒绝非法状态值（DDL 层防御）。"""
    with uow_factory.transaction() as uow:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            uow._conn.execute(
                """
                INSERT INTO ops.ingest_run (dataset_name, source_system, trace_id, status)
                VALUES ('x', 'TUSHARE', 't', 'PARTIAL')
                """
            )


def test_ingest_run_retry_preserves_cursor_and_error(uow_factory) -> None:
    """重试必须保留前次错误与游标（状态机不变量）。"""
    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="tushare:income", source_system="TUSHARE", trace_id="t"
        )
        uow.ingest_runs.transition(run["id"], IngestRunStatus.RUNNING)
        uow.ingest_runs.transition(
            run["id"],
            IngestRunStatus.FAILED_RETRYABLE,
            cursor_state={"page": 3},
            error_detail={"code": "RATE_LIMITED"},
        )
        uow.ingest_runs.transition(run["id"], IngestRunStatus.RUNNING)

        current = uow.ingest_runs.get(run["id"])
        assert current["status"] == "RUNNING"
        assert current["cursor_state"]["page"] == 3
        assert current["error_detail"]["code"] == "RATE_LIMITED"


def test_superseded_claim_records_timestamp_and_survives(uow_factory) -> None:
    """SUPERSEDED 必须记录替代时间；历史 Claim 不物理删除。"""
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="被替代公司")
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="CNINFO",
            title="年报", content_hash=uuid.uuid4().hex,
        )
        evidence = uow.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], quote_text="原文", checksum=uuid.uuid4().hex
        )
        claim = uow.claims.insert(
            subject_entity_type="Company",
            subject_entity_id=company["id"],
            predicate_code="PRODUCES",
            content_hash=uuid.uuid4().hex,
            extraction_method="RULE",
            ontology_version="v1",
            confidence=0.5,
            object_value={"x": 1},
        )
        uow.claims.update_status(claim["id"], ClaimStatus.VALIDATED)
        from src.domain.claim_service import accept_claim

        accept_claim(
            uow,
            claim_id=claim["id"],
            reviewed_by="reviewer-a",
            evidence_ids=[evidence["id"]],
            provenance={"activity_id": "act-1", "checksum": uuid.uuid4().hex},
        )
        import datetime as dt

        # CHECK: superseded_at >= recorded_at；替代时刻晚于记录时刻
        uow.claims.update_status(
            claim["id"],
            ClaimStatus.SUPERSEDED,
            superseded_at=dt.datetime.now(tz=dt.UTC),
        )

        row = uow._conn.execute(
            "SELECT claim_status, superseded_at FROM fact.claim WHERE id = %s",
            (claim["id"],),
        ).fetchone()
        assert row[0] == "SUPERSEDED"
        assert row[1] is not None, "必须能指向替代原因/时间"
