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

    @router.get("/data-freshness")
    async def data_freshness() -> dict[str, Any]:
        fresh = await run_in_threadpool(_freshness, settings.postgres_dsn)
        return {"datasets": fresh}

    return router
