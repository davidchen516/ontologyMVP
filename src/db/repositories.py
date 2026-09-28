"""基础 Repository：psycopg 直连 SQL，全部在调用方事务内执行。

幂等策略（issue #2 负向场景）：
- SourceRecord：UNIQUE(source_system, api_name, payload_hash) + ON CONFLICT 跳过；
- Claim：UNIQUE(content_hash) + ON CONFLICT 返回既有行；
- GraphOutbox：UNIQUE(idempotency_key) + ON CONFLICT 跳过。
财务空值原样保存为 NULL，不写 0。
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Json

from src.domain.enums import (
    ClaimStatus,
    DocumentParseStatus,
    GraphOutboxStatus,
    IngestRunStatus,
    ReviewTaskStatus,
)


class Repository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> psycopg.Cursor:
        return self._conn.execute(query, params)

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = self._conn.execute(query, params)
        columns = [desc.name for desc in cur.description]
        row = cur.fetchone()
        return dict(zip(columns, row, strict=True)) if row else None

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = self._conn.execute(query, params)
        columns = [desc.name for desc in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


class IngestRunRepository(Repository):
    def create(
        self,
        *,
        dataset_name: str,
        source_system: str,
        trace_id: str,
        status: IngestRunStatus = IngestRunStatus.CREATED,
        cursor_state: dict[str, Any] | None = None,
        lease_owner: str | None = None,
        lease_expires_at: Any = None,
        parent_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO ops.ingest_run
                (dataset_name, source_system, trace_id, status, cursor_state,
                 lease_owner, lease_expires_at, parent_run_id)
            VALUES (%s, %s, %s, %s::ingest_run_status, %s, %s, %s, %s)
            RETURNING id, status, started_at
            """,
            (
                dataset_name,
                source_system,
                trace_id,
                status.value,
                Json(cursor_state or {}),
                lease_owner,
                lease_expires_at,
                parent_run_id,
            ),
        )
        assert row is not None
        return row

    def get_active_run(self, dataset_name: str, *, now: Any) -> dict[str, Any] | None:
        """同一数据集的活跃运行（RUNNING 且租约未过期）。"""
        return self._fetchone(
            "SELECT id, status, lease_owner, lease_expires_at, cursor_state "
            "FROM ops.ingest_run "
            "WHERE dataset_name = %s AND status = %s AND lease_expires_at > %s "
            "ORDER BY started_at DESC LIMIT 1",
            (dataset_name, IngestRunStatus.RUNNING.value, now),
        )

    def find_stale_running(self, *, now: Any) -> list[dict[str, Any]]:
        """租约过期仍 RUNNING 的任务：崩溃后由恢复器判定为可恢复。"""
        return self._fetchall(
            "SELECT id, dataset_name, cursor_state, lease_expires_at "
            "FROM ops.ingest_run "
            "WHERE status = %s AND lease_expires_at IS NOT NULL AND lease_expires_at < %s "
            "ORDER BY started_at",
            (IngestRunStatus.RUNNING.value, now),
        )

    def update_counts(
        self,
        run_id: uuid.UUID,
        *,
        request_count: int = 0,
        rows_received: int = 0,
        rows_inserted: int = 0,
        rows_rejected: int = 0,
        cursor_state: dict[str, Any] | None = None,
        lease_expires_at: Any = None,
    ) -> dict[str, Any]:
        """批次级原子更新：计数 + 游标 + 租约心跳同事务推进。"""
        sets = [
            "request_count = request_count + %s",
            "rows_received = rows_received + %s",
            "rows_inserted = rows_inserted + %s",
            "rows_rejected = rows_rejected + %s",
        ]
        values: list[Any] = [request_count, rows_received, rows_inserted, rows_rejected]
        if cursor_state is not None:
            sets.append("cursor_state = %s")
            values.append(Json(cursor_state))
        if lease_expires_at is not None:
            sets.append("lease_expires_at = %s")
            values.append(lease_expires_at)
        values.append(run_id)
        row = self._fetchone(
            f"UPDATE ops.ingest_run SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, request_count, rows_received, rows_inserted, rows_rejected",
            tuple(values),
        )
        assert row is not None
        return row

    def get(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, dataset_name, source_system, status, started_at, finished_at, "
            "request_count, rows_received, rows_inserted, rows_rejected, "
            "cursor_state, error_detail, lease_owner, lease_expires_at, parent_run_id, "
            "trace_id "
            "FROM ops.ingest_run WHERE id = %s",
            (run_id,),
        )

    def transition(
        self, run_id: uuid.UUID, target: IngestRunStatus, **fields: Any
    ) -> dict[str, Any]:
        """带状态机校验的迁移：非法迁移不产生任何写入。"""
        current = self.get(run_id)
        if current is None:
            raise LookupError(f"ingest_run {run_id} not found")
        from src.domain.enums import ensure_transition

        ensure_transition("ingest_run", current["status"], target.value)

        sets = ["status = %s::ingest_run_status"]
        values: list[Any] = [target.value]
        if "finished_at" in fields:
            sets.append("finished_at = %s")
            values.append(fields["finished_at"])
        if "cursor_state" in fields:
            sets.append("cursor_state = %s")
            values.append(Json(fields["cursor_state"]))
        if "error_detail" in fields:
            sets.append("error_detail = %s")
            values.append(Json(fields["error_detail"]))
        values.append(run_id)
        row = self._fetchone(
            f"UPDATE ops.ingest_run SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, status",
            tuple(values),
        )
        assert row is not None
        return row


class SourceRecordRepository(Repository):
    def insert_idempotent(
        self,
        *,
        source_system: str,
        api_name: str,
        payload_hash: str,
        raw_payload: dict[str, Any],
        ingest_run_id: uuid.UUID,
        source_key: str | None = None,
        request_params: dict[str, Any] | None = None,
        schema_signature: str | None = None,
    ) -> dict[str, Any]:
        """重复 payload_hash 的重放不产生重复事实；返回 inserted 标志。"""
        row = self._fetchone(
            """
            INSERT INTO raw.source_record
                (source_system, api_name, source_key, request_params, payload_hash,
                 raw_payload, ingest_run_id, schema_signature)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system, api_name, payload_hash) DO NOTHING
            RETURNING id
            """,
            (
                source_system,
                api_name,
                source_key,
                Json(request_params or {}),
                payload_hash,
                Json(raw_payload),
                ingest_run_id,
                schema_signature,
            ),
        )
        if row is None:
            existing = self._fetchone(
                "SELECT id FROM raw.source_record "
                "WHERE source_system = %s AND api_name = %s AND payload_hash = %s",
                (source_system, api_name, payload_hash),
            )
            assert existing is not None
            return {"id": existing["id"], "inserted": False}
        return {"id": row["id"], "inserted": True}

    def count_by_run(self, ingest_run_id: uuid.UUID) -> int:
        row = self._fetchone(
            "SELECT count(*) AS n FROM raw.source_record WHERE ingest_run_id = %s",
            (ingest_run_id,),
        )
        assert row is not None
        return row["n"]


class CompanyRepository(Repository):
    def insert(self, *, canonical_name: str, **fields: Any) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.company (canonical_name, legal_name, unified_social_credit_code)
            VALUES (%s, %s, %s)
            RETURNING id, canonical_name
            """,
            (
                canonical_name,
                fields.get("legal_name"),
                fields.get("unified_social_credit_code"),
            ),
        )
        assert row is not None
        return row

    def get(self, company_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, canonical_name FROM master.company WHERE id = %s", (company_id,)
        )


class SecurityRepository(Repository):
    def insert(
        self, *, ts_code: str, symbol: str, name: str, exchange_id: uuid.UUID
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.security (ts_code, symbol, name, exchange_id, status)
            VALUES (%s, %s, %s, %s, 'ACTIVE')
            RETURNING id, ts_code
            """,
            (ts_code, symbol, name, exchange_id),
        )
        assert row is not None
        return row


class ClaimRepository(Repository):
    def insert(
        self,
        *,
        subject_entity_type: str,
        subject_entity_id: uuid.UUID,
        predicate_code: str,
        content_hash: str,
        extraction_method: str,
        ontology_version: str,
        confidence: float,
        status: ClaimStatus = ClaimStatus.EXTRACTED,
        **fields: Any,
    ) -> dict[str, Any]:
        """content_hash 唯一：重放同一 Fixture 收敛为同一条 Claim。"""
        row = self._fetchone(
            """
            INSERT INTO fact.claim
                (subject_entity_type, subject_entity_id, predicate_code, claim_status,
                 object_entity_type, object_entity_id, object_value, business_stage,
                 evidence_state, confidence, extraction_method, ontology_version,
                 mapping_version, content_hash)
            VALUES (%s, %s, %s, %s::claim_status, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (content_hash) DO UPDATE SET content_hash = EXCLUDED.content_hash
            RETURNING id, claim_status, (xmax = 0) AS inserted
            """,
            (
                subject_entity_type,
                subject_entity_id,
                predicate_code,
                status.value,
                fields.get("object_entity_type"),
                fields.get("object_entity_id"),
                Json(fields["object_value"]) if fields.get("object_value") else None,
                fields.get("business_stage"),
                fields.get("evidence_state"),
                confidence,
                extraction_method,
                ontology_version,
                fields.get("mapping_version"),
                content_hash,
            ),
        )
        assert row is not None
        return row

    def get(self, claim_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, claim_status, subject_entity_type, subject_entity_id, "
            "predicate_code, content_hash, reviewed_by, reviewed_at "
            "FROM fact.claim WHERE id = %s",
            (claim_id,),
        )

    def get_for_update_nowait(self, claim_id: uuid.UUID) -> dict[str, Any] | None:
        """悲观锁 + NOWAIT：并发审核同一 Claim 时后到者立即得到冲突。"""
        try:
            return self._fetchone(
                "SELECT id, claim_status, subject_entity_type, subject_entity_id, "
                "predicate_code, content_hash, reviewed_by, reviewed_at "
                "FROM fact.claim WHERE id = %s FOR UPDATE NOWAIT",
                (claim_id,),
            )
        except psycopg.errors.LockNotAvailable:
            raise ConcurrentClaimUpdateError(claim_id) from None

    def update_status_guarded(
        self,
        claim_id: uuid.UUID,
        expected_status: ClaimStatus,
        target: ClaimStatus,
        *,
        reviewed_by: str | None = None,
        reviewed_at: Any = None,
        superseded_by_claim_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """乐观守卫：基于当前版本的成功；版本不符得到冲突，不覆盖他人决定。"""
        row = self._fetchone(
            """
            UPDATE fact.claim
            SET claim_status = %s::claim_status, reviewed_by = %s,
                reviewed_at = %s, updated_at = now()
            WHERE id = %s AND claim_status = %s::claim_status
            RETURNING id, claim_status
            """,
            (target.value, reviewed_by, reviewed_at, claim_id, expected_status.value),
        )
        if row is None:
            raise ConcurrentClaimUpdateError(claim_id)
        return row

    def update_status(
        self, claim_id: uuid.UUID, target: ClaimStatus, **fields: Any
    ) -> dict[str, Any]:
        sets = ["claim_status = %s::claim_status", "updated_at = now()"]
        values: list[Any] = [target.value]
        if "superseded_at" in fields:
            sets.append("superseded_at = %s")
            values.append(fields["superseded_at"])
        if "evidence_state" in fields:
            sets.append("evidence_state = %s")
            values.append(fields["evidence_state"])
        values.append(claim_id)
        row = self._fetchone(
            f"UPDATE fact.claim SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, claim_status",
            tuple(values),
        )
        assert row is not None
        return row


class ClaimEvidenceRepository(Repository):
    def link(
        self,
        *,
        claim_id: uuid.UUID,
        evidence_id: uuid.UUID,
        support_type: str = "SUPPORTS",
        source_weight: float | None = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO fact.claim_evidence (claim_id, evidence_id, support_type, source_weight)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (claim_id, evidence_id, support_type) DO NOTHING
            """,
            (claim_id, evidence_id, support_type, source_weight),
        )

    def count_for_claim(self, claim_id: uuid.UUID) -> int:
        row = self._fetchone(
            "SELECT count(*) AS n FROM fact.claim_evidence WHERE claim_id = %s", (claim_id,)
        )
        assert row is not None
        return row["n"]

    def exists(self, evidence_id: uuid.UUID) -> bool:
        row = self._fetchone(
            "SELECT 1 AS ok FROM fact.evidence_fragment WHERE id = %s", (evidence_id,)
        )
        return row is not None

    def insert_evidence_fragment(
        self,
        *,
        document_id: uuid.UUID,
        quote_text: str,
        checksum: str,
        **fields: Any,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO fact.evidence_fragment (document_id, page_number, quote_text, checksum)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (document_id, checksum) DO UPDATE SET checksum = EXCLUDED.checksum
            RETURNING id, (xmax = 0) AS inserted
            """,
            (document_id, fields.get("page_number"), quote_text, checksum),
        )
        assert row is not None
        return row


class ProvenanceRepository(Repository):
    # Provenance Hash 链统一 advisory lock 键（与 PostgresProvenanceStorage 共享）
    CHAIN_LOCK_KEY = 0x50524F56

    def insert(
        self,
        *,
        entity_id: str,
        entity_type: str,
        activity_id: str,
        checksum: str,
        **fields: Any,
    ) -> dict[str, Any]:
        # 统一链语义：未显式给出 previous_checksum 时咬合当前链头
        # （共享 advisory lock，与 PostgresProvenanceStorage 并发不分叉）
        if fields.get("previous_checksum") is None:
            self._conn.execute(
                "SELECT pg_advisory_xact_lock(%s)", (self.CHAIN_LOCK_KEY,)
            )
            head = self._fetchone(
                "SELECT checksum FROM fact.provenance_entry "
                "ORDER BY sequence_id DESC NULLS LAST LIMIT 1"
            )
            fields["previous_checksum"] = head["checksum"] if head else None
        row = self._fetchone(
            """
            INSERT INTO fact.provenance_entry
                (entity_id, entity_type, activity_id, agent_id, source_document_id,
                 source_record_id, source_location, source_quote, parent_entity_id,
                 used_entities, confidence, checksum, previous_checksum, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                entity_id,
                entity_type,
                activity_id,
                fields.get("agent_id"),
                fields.get("source_document_id"),
                fields.get("source_record_id"),
                fields.get("source_location"),
                fields.get("source_quote"),
                fields.get("parent_entity_id"),
                Json(fields.get("used_entities") or []),
                fields.get("confidence"),
                checksum,
                fields.get("previous_checksum"),
                Json(fields.get("metadata") or {}),
            ),
        )
        assert row is not None
        return row

    def count_for_entity(self, entity_id: str) -> int:
        row = self._fetchone(
            "SELECT count(*) AS n FROM fact.provenance_entry WHERE entity_id = %s",
            (entity_id,),
        )
        assert row is not None
        return row["n"]


class ReviewTaskRepository(Repository):
    def create(
        self,
        *,
        task_type: str,
        claim_id: uuid.UUID | None = None,
        priority: str = "NORMAL",
        **fields: Any,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO fact.review_task (task_type, claim_id, priority, reason_codes)
            VALUES (%s, %s, %s, %s)
            RETURNING id, status
            """,
            (task_type, claim_id, priority, Json(fields.get("reason_codes") or [])),
        )
        assert row is not None
        return row

    def get(self, task_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, status, decision, claim_id FROM fact.review_task WHERE id = %s",
            (task_id,),
        )

    def transition(
        self, task_id: uuid.UUID, target: ReviewTaskStatus, **fields: Any
    ) -> dict[str, Any]:
        current = self.get(task_id)
        if current is None:
            raise LookupError(f"review_task {task_id} not found")
        from src.domain.enums import ensure_transition

        ensure_transition("review_task", current["status"], target.value)
        sets = ["status = %s::review_task_status"]
        values: list[Any] = [target.value]
        if "decision" in fields:
            sets.append("decision = %s")
            values.append(fields["decision"])
        if "decision_reason" in fields:
            sets.append("decision_reason = %s")
            values.append(fields["decision_reason"])
        if "completed_at" in fields:
            sets.append("completed_at = %s")
            values.append(fields["completed_at"])
        values.append(task_id)
        row = self._fetchone(
            f"UPDATE fact.review_task SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, status",
            tuple(values),
        )
        assert row is not None
        return row


class GraphOutboxRepository(Repository):
    def insert_idempotent(
        self,
        *,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """idempotency_key 唯一：同键重放不产生重复事件。"""
        row = self._fetchone(
            """
            INSERT INTO ops.graph_outbox
                (aggregate_type, aggregate_id, event_type, payload, idempotency_key)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (aggregate_type, aggregate_id, event_type, Json(payload), idempotency_key),
        )
        if row is None:
            existing = self._fetchone(
                "SELECT id FROM ops.graph_outbox WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            assert existing is not None
            return {"id": existing["id"], "inserted": False}
        return {"id": row["id"], "inserted": True}

    def count_all(self) -> int:
        row = self._fetchone("SELECT count(*) AS n FROM ops.graph_outbox")
        assert row is not None
        return row["n"]

    def update_status(
        self, outbox_id: uuid.UUID, target: GraphOutboxStatus, **fields: Any
    ) -> dict[str, Any]:
        current = self._fetchone(
            "SELECT status FROM ops.graph_outbox WHERE id = %s", (outbox_id,)
        )
        if current is None:
            raise LookupError(f"graph_outbox {outbox_id} not found")
        from src.domain.enums import ensure_transition

        ensure_transition("graph_outbox", current["status"], target.value)
        row = self._fetchone(
            "UPDATE ops.graph_outbox SET status = %s::graph_outbox_status, "
            "retry_count = retry_count + 1 WHERE id = %s RETURNING id, status",
            (target.value, outbox_id),
        )
        assert row is not None
        return row


class DocumentRepository(Repository):
    def insert(
        self, *, document_type: str, source_system: str, title: str, content_hash: str
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO fact.document
                (document_type, source_system, title, content_hash, parse_status)
            VALUES (%s, %s, %s, %s, %s::document_parse_status)
            ON CONFLICT (source_system, content_hash) DO UPDATE
                SET content_hash = EXCLUDED.content_hash
            RETURNING id, parse_status, (xmax = 0) AS inserted
            """,
            (document_type, source_system, title, content_hash,
             DocumentParseStatus.PENDING.value),
        )
        assert row is not None
        return row


class AuditEventRepository(Repository):
    def insert(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO ops.audit_event (entity_type, entity_id, event_type, payload, trace_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (entity_type, entity_id, event_type, Json(payload or {}), trace_id),
        )
        assert row is not None
        return row

    def count_for_entity(self, entity_type: str, entity_id: uuid.UUID) -> int:
        row = self._fetchone(
            "SELECT count(*) AS n FROM ops.audit_event "
            "WHERE entity_type = %s AND entity_id = %s",
            (entity_type, entity_id),
        )
        assert row is not None
        return row["n"]


class SourceCapabilityRepository(Repository):
    def upsert(
        self,
        *,
        source_system: str,
        api_name: str,
        status: str,
        response_latency_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO ops.source_capability
                (source_system, api_name, status, checked_at, response_latency_ms, detail)
            VALUES (%s, %s, %s, now(), %s, %s)
            ON CONFLICT (source_system, api_name) DO UPDATE SET
                status = EXCLUDED.status,
                checked_at = EXCLUDED.checked_at,
                response_latency_ms = EXCLUDED.response_latency_ms,
                detail = EXCLUDED.detail
            RETURNING source_system, api_name, status, checked_at, response_latency_ms
            """,
            (source_system, api_name, status, response_latency_ms, Json(detail or {})),
        )
        assert row is not None
        return row

    def get_status(self, source_system: str, api_name: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT source_system, api_name, status, checked_at, response_latency_ms, detail "
            "FROM ops.source_capability WHERE source_system = %s AND api_name = %s",
            (source_system, api_name),
        )

    def list_all(self) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT source_system, api_name, status, checked_at, response_latency_ms, detail "
            "FROM ops.source_capability ORDER BY source_system, api_name"
        )


class ConcurrentClaimUpdateError(Exception):
    """并发处理同一 Claim：后到者基于过期版本，得到冲突而非覆盖。"""

    def __init__(self, claim_id: uuid.UUID) -> None:
        super().__init__(f"concurrent update on claim {claim_id}")
        self.claim_id = claim_id
