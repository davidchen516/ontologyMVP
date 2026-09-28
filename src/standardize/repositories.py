"""标准层仓储：幂等 upsert（重放同一 Raw 不产生重复标准结果）。

幂等性设计：
- 关系表 recorded_at 一律取 Raw 的 retrieved_at（确定性，非 now()）；
- 全部 upsert 使用 ON CONFLICT DO NOTHING / DO UPDATE，不物理删除。
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from src.db.repositories import Repository


class ExchangeRepository(Repository):
    def upsert(self, *, code: str, name: str, country_code: str = "CN") -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.exchange (code, name, country_code)
            VALUES (%s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, code
            """,
            (code, name, country_code),
        )
        assert row is not None
        return row


class StandardSecurityRepository(Repository):
    def upsert(
        self,
        *,
        ts_code: str,
        symbol: str,
        name: str,
        exchange_id: uuid.UUID,
        source_record_id: uuid.UUID,
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.security (ts_code, symbol, name, exchange_id, status,
                                         source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name, updated_at = now()
            RETURNING id, ts_code, (xmax = 0) AS inserted
            """,
            (ts_code, symbol, name, exchange_id, status, source_record_id),
        )
        assert row is not None
        return row

    def get_by_ts_code(self, ts_code: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, ts_code, name FROM master.security WHERE ts_code = %s", (ts_code,)
        )

    def add_name_history(
        self,
        *,
        security_id: uuid.UUID,
        name: str,
        start_date: Any,
        end_date: Any,
        ann_date: Any,
        source_record_id: uuid.UUID,
        recorded_at: Any,
    ) -> None:
        self._execute(
            """
            INSERT INTO master.security_name_history
                (security_id, name, start_date, end_date, ann_date, source_record_id,
                 recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_id, name, start_date) DO NOTHING
            """,
            (security_id, name, start_date, end_date, ann_date, source_record_id, recorded_at),
        )


class StandardCompanyRepository(Repository):
    def upsert_with_security_anchor(
        self,
        *,
        canonical_name: str,
        source_record_id: uuid.UUID,
        security_id: uuid.UUID | None = None,
        ts_code: str | None = None,
        unified_social_credit_code: str | None = None,
    ) -> dict[str, Any]:
        """以"证券→发行主体"的受控复合键定位公司；不按简称合并。

        信用代码缺失时使用受控占位（TUSHARE-UNVERIFIED-{ts_code}，唯一且显式
        标注不确定），记录在 normalization_event 中，绝不把两家同名公司并成一家。
        """
        if unified_social_credit_code is None and ts_code is None:
            raise ValueError("company anchor requires uscc or ts_code")

        # 复用：该证券已关联的公司
        if security_id is not None:
            existing = self._fetchone(
                """
                SELECT c.id, c.canonical_name, c.unified_social_credit_code
                FROM master.company c
                JOIN master.company_security cs ON cs.company_id = c.id
                WHERE cs.security_id = %s
                ORDER BY cs.recorded_at DESC
                LIMIT 1
                """,
                (security_id,),
            )
            if existing is not None:
                return {**existing, "inserted": False}

        uscc = unified_social_credit_code or f"TUSHARE-UNVERIFIED-{ts_code}"
        row = self._fetchone(
            """
            INSERT INTO master.company (canonical_name, unified_social_credit_code)
            VALUES (%s, %s)
            ON CONFLICT (unified_social_credit_code) DO UPDATE SET
                canonical_name = EXCLUDED.canonical_name
            RETURNING id, canonical_name, unified_social_credit_code, (xmax = 0) AS inserted
            """,
            (canonical_name, uscc),
        )
        assert row is not None
        if row["inserted"] and unified_social_credit_code is None:
            row["uscc_placeholder"] = True
        return row

    def link_security(
        self,
        *,
        company_id: uuid.UUID,
        security_id: uuid.UUID,
        recorded_at: Any,
        source_record_id: uuid.UUID,
    ) -> None:
        self._execute(
            """
            INSERT INTO master.company_security
                (company_id, security_id, recorded_at, source_record_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (company_id, security_id, recorded_at) DO NOTHING
            """,
            (company_id, security_id, recorded_at, source_record_id),
        )

    def upsert_profile(
        self,
        *,
        company_id: uuid.UUID,
        source_record_id: uuid.UUID,
        **fields: Any,
    ) -> None:
        self._execute(
            """
            INSERT INTO master.company_profile
                (company_id, chairman_name, general_manager_name, board_secretary_name,
                 registered_capital, founded_date, province, city, employees,
                 main_part_business, source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id) DO UPDATE SET
                chairman_name = EXCLUDED.chairman_name,
                general_manager_name = EXCLUDED.general_manager_name,
                board_secretary_name = EXCLUDED.board_secretary_name,
                registered_capital = EXCLUDED.registered_capital,
                founded_date = EXCLUDED.founded_date,
                province = EXCLUDED.province,
                city = EXCLUDED.city,
                employees = EXCLUDED.employees,
                main_part_business = EXCLUDED.main_part_business,
                source_record_id = EXCLUDED.source_record_id,
                updated_at = now()
            """,
            (
                company_id,
                fields.get("chairman_name"),
                fields.get("general_manager_name"),
                fields.get("board_secretary_name"),
                fields.get("registered_capital"),
                fields.get("founded_date"),
                fields.get("province"),
                fields.get("city"),
                fields.get("employees"),
                fields.get("main_part_business"),
                source_record_id,
            ),
        )


class IndustryRepository(Repository):
    def upsert(
        self,
        *,
        taxonomy: str,
        taxonomy_version: str,
        external_code: str,
        level_code: str,
        name: str,
        parent_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.industry
                (taxonomy, taxonomy_version, level_code, external_code, name, parent_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (taxonomy, taxonomy_version, external_code) DO UPDATE SET
                name = EXCLUDED.name
            RETURNING id, (xmax = 0) AS inserted
            """,
            (taxonomy, taxonomy_version, level_code, external_code, name, parent_id),
        )
        assert row is not None
        return row

    def upsert_company_membership(
        self,
        *,
        company_id: uuid.UUID,
        industry_id: uuid.UUID,
        valid_from: Any,
        valid_to: Any,
        recorded_at: Any,
        source_record_id: uuid.UUID,
    ) -> None:
        self._execute(
            """
            INSERT INTO master.company_industry_membership
                (company_id, industry_id, valid_from, valid_to, recorded_at, source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, industry_id, recorded_at) DO NOTHING
            """,
            (company_id, industry_id, valid_from, valid_to, recorded_at, source_record_id),
        )


class ConceptRepository(Repository):
    def upsert(
        self, *, source_platform: str, external_code: str, name: str
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO master.concept (source_platform, external_code, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_platform, external_code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, (xmax = 0) AS inserted
            """,
            (source_platform, external_code, name),
        )
        assert row is not None
        return row

    def upsert_company_membership(
        self,
        *,
        company_id: uuid.UUID,
        concept_id: uuid.UUID,
        snapshot_date: Any,
        is_member: bool,
        source_record_id: uuid.UUID,
        recorded_at: Any,
    ) -> None:
        self._execute(
            """
            INSERT INTO master.company_concept_membership
                (company_id, concept_id, snapshot_date, is_member, source_record_id,
                 recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, concept_id, snapshot_date) DO NOTHING
            """,
            (company_id, concept_id, snapshot_date, is_member, source_record_id, recorded_at),
        )


class FinancialRepository(Repository):
    def upsert_metric(
        self,
        *,
        metric_code: str,
        name: str,
        statement_type: str,
        unit_type: str,
        default_aggregation: str,
        tushare_field: str | None = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO finance.financial_metric
                (metric_code, name, statement_type, unit_type, default_aggregation,
                 tushare_field)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (metric_code) DO UPDATE SET name = EXCLUDED.name
            """,
            (metric_code, name, statement_type, unit_type, default_aggregation, tushare_field),
        )

    def upsert_observation(
        self,
        *,
        security_id: uuid.UUID,
        metric_code: str,
        period_end: Any,
        report_type: str,
        value: Any,
        currency: str | None,
        announced_at: Any,
        update_flag: str | None,
        source_record_id: uuid.UUID,
    ) -> dict[str, Any]:
        """缺失值保持 NULL（不转 0）；币种未知保持 NULL（不默认 CNY）。
        同键（含口径/更新标志）重放不重复；全部历史版本保留。"""
        row = self._fetchone(
            """
            INSERT INTO finance.financial_observation
                (security_id, metric_code, period_end, report_type, value, currency,
                 announced_at, update_flag, source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_id, metric_code, period_end, report_type, update_flag,
                         announced_at)
                DO NOTHING
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                security_id,
                metric_code,
                period_end,
                report_type,
                value,
                currency,
                announced_at,
                update_flag,
                source_record_id,
            ),
        )
        if row is None:
            return {"id": None, "inserted": False}
        return row

    def upsert_business_segment(
        self,
        *,
        company_id: uuid.UUID,
        period_end: Any,
        segment_type: str,
        raw_segment_name: str,
        revenue: Any,
        cost: Any,
        profit: Any,
        currency: str | None,
        source_record_id: uuid.UUID,
        recorded_at: Any,
    ) -> dict[str, Any]:
        """bz_item 原样保留为 raw_segment_name；mapped_product 保持 NULL
        （产品语义映射属后续语义层）。最新快照 DO UPDATE，审计走事件流。"""
        row = self._fetchone(
            """
            INSERT INTO finance.business_segment_observation
                (company_id, period_end, segment_type, raw_segment_name, revenue, cost,
                 profit, currency, source_record_id, mapping_status, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'UNMAPPED', %s)
            ON CONFLICT (company_id, period_end, segment_type, raw_segment_name)
                DO UPDATE SET
                    revenue = EXCLUDED.revenue,
                    cost = EXCLUDED.cost,
                    profit = EXCLUDED.profit,
                    currency = EXCLUDED.currency,
                    source_record_id = EXCLUDED.source_record_id,
                    recorded_at = EXCLUDED.recorded_at
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                company_id,
                period_end,
                segment_type,
                raw_segment_name,
                revenue,
                cost,
                profit,
                currency,
                source_record_id,
                recorded_at,
            ),
        )
        assert row is not None
        return row


class HoldingRepository(Repository):
    """股东持有观察：只产生 HOLDS 候选，绝不产生 CONTROLS。"""

    def upsert_observation(
        self,
        *,
        security_id: uuid.UUID,
        holder_name: str,
        holder_type: str | None,
        hold_amount: Any,
        hold_ratio: Any,
        end_date: Any,
        source_record_id: uuid.UUID,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO fact.holding_observation
                (security_id, holder_name, holder_type, hold_amount, hold_ratio,
                 end_date, source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_id, holder_name, end_date, hold_ratio) DO NOTHING
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                security_id,
                holder_name,
                holder_type,
                hold_amount,
                hold_ratio,
                end_date,
                source_record_id,
            ),
        )
        if row is None:
            return {"id": None, "inserted": False}
        return row


class NormalizationRunRepository(Repository):
    def create(
        self,
        *,
        dataset_name: str,
        source_system: str,
        mapping_version: str,
        trace_id: str,
        watermark: dict[str, Any] | None = None,
        lease_owner: str | None = None,
        lease_expires_at: Any = None,
        parent_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = self._fetchone(
            """
            INSERT INTO ops.normalization_run
                (dataset_name, source_system, mapping_version, trace_id, watermark,
                 lease_owner, lease_expires_at, parent_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, status
            """,
            (
                dataset_name,
                source_system,
                mapping_version,
                trace_id,
                Json(watermark or {}),
                lease_owner,
                lease_expires_at,
                parent_run_id,
            ),
        )
        assert row is not None
        return row

    def get(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, dataset_name, mapping_version, status, started_at, finished_at, "
            "rows_read, rows_written, rows_rejected, error_detail, watermark, "
            "trace_id, lease_owner, lease_expires_at, parent_run_id "
            "FROM ops.normalization_run WHERE id = %s",
            (run_id,),
        )

    def get_active_run(self, dataset_name: str, *, now: Any) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT id, status, lease_owner, lease_expires_at, watermark "
            "FROM ops.normalization_run "
            "WHERE dataset_name = %s AND status = 'RUNNING' AND lease_expires_at > %s "
            "ORDER BY started_at DESC LIMIT 1",
            (dataset_name, now),
        )

    def latest_watermark(self, dataset_name: str) -> dict[str, Any] | None:
        """该数据集最近一次完成运行的持久化水位（续传语义）。"""
        row = self._fetchone(
            "SELECT watermark FROM ops.normalization_run "
            "WHERE dataset_name = %s "
            "AND status IN ('SUCCEEDED', 'PARTIAL_SUCCESS', 'FAILED_RETRYABLE') "
            "ORDER BY started_at DESC LIMIT 1",
            (dataset_name,),
        )
        return row["watermark"] if row else None

    def transition(self, run_id: uuid.UUID, target: str, **fields: Any) -> dict[str, Any]:
        current = self.get(run_id)
        if current is None:
            raise LookupError(f"normalization_run {run_id} not found")
        from src.domain.enums import ensure_transition

        ensure_transition("ingest_run", current["status"], target)
        sets = ["status = %s::ingest_run_status"]
        values: list[Any] = [target]
        if "finished_at" in fields:
            sets.append("finished_at = %s")
            values.append(fields["finished_at"])
        if "error_detail" in fields:
            sets.append("error_detail = %s")
            values.append(Json(fields["error_detail"]))
        values.append(run_id)
        row = self._fetchone(
            f"UPDATE ops.normalization_run SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, status",
            tuple(values),
        )
        assert row is not None
        return row

    def update_counts(
        self,
        run_id: uuid.UUID,
        *,
        rows_read: int = 0,
        rows_written: int = 0,
        rows_rejected: int = 0,
        watermark: dict[str, Any] | None = None,
        lease_expires_at: Any = None,
    ) -> None:
        sets = [
            "rows_read = rows_read + %s",
            "rows_written = rows_written + %s",
            "rows_rejected = rows_rejected + %s",
        ]
        values: list[Any] = [rows_read, rows_written, rows_rejected]
        if watermark is not None:
            sets.append("watermark = %s")
            values.append(Json(watermark))
        if lease_expires_at is not None:
            sets.append("lease_expires_at = %s")
            values.append(lease_expires_at)
        values.append(run_id)
        self._execute(
            f"UPDATE ops.normalization_run SET {', '.join(sets)} WHERE id = %s",
            tuple(values),
        )

    def find_stale_running(self, *, now: Any) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, dataset_name, watermark, lease_expires_at "
            "FROM ops.normalization_run "
            "WHERE status = 'RUNNING' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at < %s ORDER BY started_at",
            (now,),
        )

    def list_runs(
        self, *, dataset: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, dataset_name, mapping_version, status, started_at, finished_at, "
            "rows_read, rows_written, rows_rejected, error_detail, watermark, trace_id, "
            "parent_run_id FROM ops.normalization_run"
        )
        params: list[Any] = []
        if dataset:
            query += " WHERE dataset_name = %s"
            params.append(dataset)
        query += " ORDER BY started_at DESC LIMIT %s"
        params.append(limit)
        return self._fetchall(query, tuple(params))


class NormalizationEventRepository(Repository):
    def add(
        self,
        *,
        run_id: uuid.UUID,
        event_type: str,
        entity_type: str | None = None,
        entity_ref: str | None = None,
        detail: dict[str, Any] | None = None,
        source_record_id: uuid.UUID | None = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO ops.normalization_event
                (run_id, event_type, entity_type, entity_ref, detail, source_record_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (run_id, event_type, entity_type, entity_ref, Json(detail or {}), source_record_id),
        )

    def list_for_run(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, event_type, entity_type, entity_ref, detail, source_record_id, "
            "created_at FROM ops.normalization_event WHERE run_id = %s "
            "ORDER BY created_at DESC",
            (run_id,),
        )

    def list_rejections(
        self, *, run_id: uuid.UUID | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, run_id, entity_type, entity_ref, detail, source_record_id, "
            "created_at FROM ops.normalization_event WHERE event_type = 'REJECTED'"
        )
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = %s"
            params.append(run_id)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        return self._fetchall(query, tuple(params))
