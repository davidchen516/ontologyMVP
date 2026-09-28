"""FastAPI 业务接口：/api/v1/query + /api/v1/screen + 只读实体端点（issue #9）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.query.compiler import QueryOrchestrator
from src.query.models import (
    QueryPlan,
    QueryResponse,
)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    """查询请求：直接传入受控 QueryPlan（LLM Planner 后续可选）。"""

    plan: QueryPlan
    natural_question: str | None = Field(default=None, max_length=2000)


class ScreenRequest(BaseModel):
    """筛选请求：语义条件 + 财务条件的简化入口。"""

    concept_name: str | None = Field(default=None, max_length=200)
    business_stage: str | None = Field(default=None, max_length=50)
    metric_code: str | None = Field(default=None, max_length=64)
    min_fy_count: int = Field(default=3, ge=1, le=10)
    evidence_required: bool = True
    as_of: str | None = None
    max_results: int = Field(default=50, ge=1, le=100)


@router.post("/query", response_model=QueryResponse)
async def execute_query(body: QueryRequest, request: Request) -> QueryResponse:
    """执行受控查询计划。只读——不修改事实/财务/图。"""
    plan = body.plan
    trace_id = request.headers.get("x-trace-id")
    settings = request.app.state.settings

    # 只读连接
    import psycopg


    orchestrator = QueryOrchestrator()
    with psycopg.connect(settings.postgres_dsn) as conn:
        try:
            return orchestrator.execute(conn, plan, trace_id=trace_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/screen", response_model=QueryResponse)
async def execute_screen(body: ScreenRequest, request: Request) -> QueryResponse:
    """筛选：概念/阶段 + 财务条件的简化查询。"""
    from src.query.models import NumericFilter, QueryIntent, SemanticFilter

    semantic = None
    if body.concept_name or body.business_stage:
        semantic = SemanticFilter(
            concept_name=body.concept_name,
            business_stage=body.business_stage,
        )
    numeric = None
    if body.metric_code:
        numeric = NumericFilter(
            metric_code=body.metric_code,
            operator="TOTAL_POSITIVE",
            period_rule="LAST_3_FY",
        )
    plan = QueryPlan(
        intent=QueryIntent.SEMANTIC_SCREEN,
        semantic_filter=semantic,
        numeric_filter=numeric,
        evidence_required=body.evidence_required,
        max_results=body.max_results,
    )
    trace_id = request.headers.get("x-trace-id")
    settings = request.app.state.settings

    import psycopg


    orchestrator = QueryOrchestrator()
    with psycopg.connect(settings.postgres_dsn) as conn:
        try:
            return orchestrator.execute(conn, plan, trace_id=trace_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- 只读实体接口 ----


@router.get("/companies/{company_id}")
async def get_company(company_id: UUID, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    import psycopg

    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT id, canonical_name, unified_social_credit_code, company_type, "
            "status, created_at FROM master.company WHERE id = %s",
            (company_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="company not found")
    cols = ["id", "canonical_name", "unified_social_credit_code",
            "company_type", "status", "created_at"]
    return dict(zip(cols, row, strict=True))


@router.get("/companies/{company_id}/claims")
async def get_company_claims(
    company_id: UUID, request: Request, limit: int = 50
) -> dict[str, Any]:
    settings = request.app.state.settings
    import psycopg

    with psycopg.connect(settings.postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT id, predicate_code, claim_status, business_stage, "
            "evidence_state, confidence, valid_from, valid_to, recorded_at "
            "FROM fact.claim WHERE subject_entity_id = %s "
            "ORDER BY recorded_at DESC LIMIT %s",
            (company_id, min(limit, 200)),
        ).fetchall()
    cols = ["id", "predicate_code", "claim_status", "business_stage",
            "evidence_state", "confidence", "valid_from", "valid_to", "recorded_at"]
    claims = [dict(zip(cols, row, strict=True)) for row in rows]
    return {"company_id": str(company_id), "claims": claims, "count": len(claims)}


@router.get("/companies/{company_id}/timeline")
async def get_company_timeline(company_id: UUID, request: Request) -> dict[str, Any]:
    """主体时间线：ACCEPTED Claim 的双时态视图。"""
    settings = request.app.state.settings
    import psycopg

    with psycopg.connect(settings.postgres_dsn) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.predicate_code, c.business_stage, c.evidence_state,
                   c.valid_from, c.valid_to, c.recorded_at, c.superseded_at
            FROM fact.claim c
            WHERE c.subject_entity_id = %s AND c.claim_status = 'ACCEPTED'
            ORDER BY c.recorded_at
            """,
            (company_id,),
        ).fetchall()
    cols = ["id", "predicate_code", "business_stage", "evidence_state",
            "valid_from", "valid_to", "recorded_at", "superseded_at"]
    timeline = [dict(zip(cols, row, strict=True)) for row in rows]
    return {"company_id": str(company_id), "timeline": timeline,
            "count": len(timeline)}
