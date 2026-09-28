"""只读管理接口：能力矩阵与采集运行状态（issue #3）。

全部为 GET；不暴露 Token、完整参数或任何 Secret（detail 中的错误信息
已由探针层脱敏）。路由由工厂构建，Settings 通过闭包注入——绝不作为
请求参数暴露给客户端。
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from src.core.config import Settings


def _fetch_all_rows(dsn: str, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(dsn) as conn:
        cur = conn.execute(query, params)
        columns = [desc.name for desc in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def build_admin_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    def _freshness(dsn: str) -> dict[str, str | None]:
        """数据新鲜度：每数据集最近一次成功运行的完成时间或最新 Raw 时间。"""
        rows = _fetch_all_rows(
            dsn,
            """
            SELECT dataset_name, max(finished_at) AS last_success_at
            FROM ops.ingest_run
            WHERE status IN ('SUCCEEDED', 'PARTIAL_SUCCESS')
            GROUP BY dataset_name
            """,
        )
        raw_rows = _fetch_all_rows(
            dsn,
            "SELECT api_name, max(retrieved_at) AS last_raw_at "
            "FROM raw.source_record GROUP BY api_name",
        )
        raw_map = {row["api_name"]: row["last_raw_at"] for row in raw_rows}
        return {
            row["dataset_name"]: raw_map.get(row["dataset_name"]) or row["last_success_at"]
            for row in rows
        }

    @router.get("/capabilities")
    async def capabilities() -> list[dict[str, Any]]:
        return await run_in_threadpool(
            _fetch_all_rows,
            settings.postgres_dsn,
            """
            SELECT source_system, api_name, status, checked_at, response_latency_ms, detail
            FROM ops.source_capability
            ORDER BY source_system, api_name
            """,
        )

    @router.get("/ingest-runs")
    async def ingest_runs(
        dataset: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        query = (
            "SELECT id, dataset_name, source_system, status, started_at, finished_at, "
            "request_count, rows_received, rows_inserted, rows_rejected, error_detail, "
            "cursor_state, lease_owner, lease_expires_at, parent_run_id, trace_id "
            "FROM ops.ingest_run"
        )
        params: tuple[Any, ...] = ()
        if dataset:
            query += " WHERE dataset_name = %s"
            params = (dataset,)
        query += " ORDER BY started_at DESC LIMIT %s"
        params = (*params, limit)
        rows = await run_in_threadpool(_fetch_all_rows, settings.postgres_dsn, query, params)
        return {"runs": rows, "count": len(rows)}

    @router.get("/ingest-runs/{run_id}")
    async def ingest_run_detail(run_id: str) -> dict[str, Any]:
        rows = await run_in_threadpool(
            _fetch_all_rows,
            settings.postgres_dsn,
            """
            SELECT id, dataset_name, source_system, status, started_at, finished_at,
                   request_count, rows_received, rows_inserted, rows_rejected,
                   error_detail, cursor_state, lease_owner, lease_expires_at,
                   parent_run_id, trace_id
            FROM ops.ingest_run WHERE id::text = %s
            """,
            (run_id,),
        )
        if not rows:
            raise HTTPException(status_code=404, detail="ingest run not found")
        return rows[0]

    @router.get("/normalization-runs")
    async def normalization_runs(
        dataset: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        from src.standardize.repositories import NormalizationRunRepository

        def fetch() -> dict[str, Any]:
            with psycopg.connect(settings.postgres_dsn) as conn:
                repo = NormalizationRunRepository(conn)
                runs = repo.list_runs(dataset=dataset, limit=limit)
                return {"runs": runs, "count": len(runs)}

        return await run_in_threadpool(fetch)

    @router.get("/normalization-runs/{run_id}")
    async def normalization_run_detail(run_id: str) -> dict[str, Any]:
        from src.standardize.repositories import (
            NormalizationEventRepository,
            NormalizationRunRepository,
        )

        def fetch() -> dict[str, Any] | None:
            with psycopg.connect(settings.postgres_dsn) as conn:
                try:
                    runs = NormalizationRunRepository(conn)
                    detail = runs.get(run_id)
                except Exception:  # noqa: BLE001 - 非法 uuid 视为不存在
                    return None
                if detail is None:
                    return None
                events = NormalizationEventRepository(conn).list_for_run(detail["id"])
                return {"run": detail, "events": events}

        detail = await run_in_threadpool(fetch)
        if detail is None:
            raise HTTPException(status_code=404, detail="normalization run not found")
        return detail

    @router.get("/normalization/rejections")
    async def normalization_rejections(
        run_id: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)
    ) -> dict[str, Any]:
        import uuid as _uuid

        from src.standardize.repositories import NormalizationEventRepository

        parsed_run_id: str | None = None
        if run_id is not None:
            try:
                parsed_run_id = str(_uuid.UUID(run_id))
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid run id") from None

        def fetch() -> dict[str, Any]:
            with psycopg.connect(settings.postgres_dsn) as conn:
                events = NormalizationEventRepository(conn).list_rejections(
                    run_id=parsed_run_id, limit=limit
                )
                return {"rejections": events, "count": len(events)}

        return await run_in_threadpool(fetch)

    @router.get("/financial/current/{security_id}")
    async def financial_current(
        security_id: str, metric: str | None = Query(default=None)
    ) -> dict[str, Any]:
        from src.db.uow import UnitOfWorkFactory
        from src.standardize.financial import current_financial_observations

        def fetch() -> dict[str, Any]:
            factory = UnitOfWorkFactory(settings.postgres_dsn)
            with factory.transaction() as uow:
                return current_financial_observations(
                    uow, security_id=security_id, metric_code=metric
                )

        try:
            return await run_in_threadpool(fetch)
        except psycopg.errors.InvalidTextRepresentation:
            raise HTTPException(status_code=422, detail="invalid security id") from None

    @router.get("/financial/operating-cashflow/{security_id}")
    async def operating_cashflow(security_id: str) -> dict[str, Any]:
        """最近三个完整财年经营现金流（可复现查询；含选择策略与充足性判定）。"""
        from src.db.uow import UnitOfWorkFactory
        from src.standardize.financial import recent_three_fy_operating_cashflow

        def fetch() -> dict[str, Any]:
            factory = UnitOfWorkFactory(settings.postgres_dsn)
            with factory.transaction() as uow:
                return recent_three_fy_operating_cashflow(uow, security_id=security_id)

        try:
            return await run_in_threadpool(fetch)
        except psycopg.errors.InvalidTextRepresentation:
            raise HTTPException(status_code=422, detail="invalid security id") from None

    @router.get("/documents")
    async def documents(
        source_system: str | None = Query(default=None, max_length=50),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        query = (
            "SELECT d.id, d.document_type, d.source_system, d.external_id, "
            "d.company_id, d.title, d.published_at, d.source_url, "
            "d.content_hash, d.mime_type, d.parse_status, d.download_status "
            "FROM fact.document d"
        )
        params: list[Any] = []
        if source_system:
            query += " WHERE d.source_system = %s"
            params.append(source_system)
        query += " ORDER BY d.created_at DESC LIMIT %s"
        params.append(limit)
        rows = await run_in_threadpool(_fetch_all_rows, settings.postgres_dsn, query, tuple(params))
        return {"documents": rows, "count": len(rows)}

    @router.get("/documents/{document_id}")
    async def document_detail(document_id: str) -> dict[str, Any]:
        import uuid as _uuid

        try:
            parsed_id = _uuid.UUID(document_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid document id") from None

        def fetch() -> dict[str, Any] | None:
            docs = _fetch_all_rows(
                settings.postgres_dsn,
                "SELECT id, document_type, source_system, external_id, company_id, "
                "title, published_at, source_url, content_hash, mime_type, "
                "parse_status, download_status FROM fact.document WHERE id = %s",
                (parsed_id,),
            )
            if not docs:
                return None
            versions = _fetch_all_rows(
                settings.postgres_dsn,
                "SELECT id, version, source_url, content_hash, file_hash, "
                "storage_key, file_size, mime_type, download_status, parse_status, "
                "parser_version, page_count, text_stats, created_at "
                "FROM fact.document_version WHERE document_id = %s ORDER BY version DESC",
                (parsed_id,),
            )
            return {"document": docs[0], "versions": versions}

        detail = await run_in_threadpool(fetch)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return detail

    @router.get("/evidence")
    async def evidence_fragments(
        document_version_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        import uuid as _uuid

        query = (
            "SELECT ef.id, ef.document_id, ef.document_version_id, ef.page_number, "
            "ef.section_title, ef.paragraph_index, ef.char_start, ef.char_end, "
            "ef.quote_text, ef.checksum, ef.created_at "
            "FROM fact.evidence_fragment ef"
        )
        params: list[Any] = []
        if document_version_id is not None:
            try:
                version_uuid = _uuid.UUID(document_version_id)
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid version id") from None
            query += " WHERE ef.document_version_id = %s"
            params.append(version_uuid)
        query += " ORDER BY ef.document_id, ef.page_number, ef.paragraph_index LIMIT %s"
        params.append(limit)
        rows = await run_in_threadpool(_fetch_all_rows, settings.postgres_dsn, query, tuple(params))
        return {"fragments": rows, "count": len(rows)}

    @router.get("/data-freshness")
    async def data_freshness() -> dict[str, Any]:
        def fetch_documents() -> dict[str, Any]:
            fresh = _fetch_all_rows(
                settings.postgres_dsn,
                """
                SELECT d.source_system,
                       max(dv.downloaded_at) AS last_download_at,
                       count(dv.id) AS downloaded_versions
                FROM fact.document d
                JOIN fact.document_version dv ON dv.document_id = d.id
                GROUP BY d.source_system
                """,
            )
            catalog = _fetch_all_rows(
                settings.postgres_dsn,
                "SELECT api_name, status, checked_at, detail "
                "FROM ops.source_capability WHERE source_system = 'DOCUMENT_CATALOG'",
            )
            return {"documents_by_source": fresh, "catalog_selection": catalog}

        fresh = await run_in_threadpool(_freshness, settings.postgres_dsn)
        documents = await run_in_threadpool(fetch_documents)
        return {"datasets": fresh, **documents}

    return router
