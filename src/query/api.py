"""FastAPI 业务接口：/api/v1/query + /api/v1/screen + 只读实体端点（issue #9）。

不变量：
- 查询执行使用只读事务（SET TRANSACTION READ ONLY，服务端强制拒绝写入）；
- 除查询审计（query.query_audit，独立连接写入）外无任何外部副作用；
- 每次查询保存审计记录：原问题、规范化 QueryPlan、执行版本、口径、
  trace_id、状态与错误类别；query_id 唯一约束保证重试不产生矛盾记录；
- timeout_seconds 映射为 statement_timeout（事务级，防止长期运行查询）。
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Json
from pydantic import BaseModel, Field, field_validator, model_validator

from src.query.compiler import QUERY_EXECUTION_VERSION, QueryOrchestrator
from src.query.graph_subgraph import build_subgraph
from src.query.models import (
    NumericFilter,
    PathOperator,
    QueryIntent,
    QueryPlan,
    QueryResponse,
    SemanticFilter,
)

router = APIRouter(prefix="/api/v1", tags=["query"])

log = structlog.get_logger(__name__)


class QueryRequest(BaseModel):
    """查询请求：直接传入受控 QueryPlan（LLM Planner 后续可选）。"""

    plan: QueryPlan
    natural_question: str | None = Field(default=None, max_length=2000)


class ScreenRequest(BaseModel):
    """筛选请求：语义条件 + 财务条件的简化入口。

    前置约束与 QueryPlan 模型一致（非法组合在请求层即 422）；
    注入载荷在计划构造层被拒（同样走受控 422）。
    """

    concept_name: str | None = Field(default=None, max_length=200)
    business_stage: str | None = Field(default=None, max_length=50)
    metric_code: str | None = Field(default=None, max_length=64)
    operator: PathOperator = PathOperator.TOTAL_POSITIVE
    threshold: float | None = None
    period_rule: str | None = Field(
        default=None, max_length=50
    )  # None = 按 fiscal_years/operator 推导口径
    fiscal_years: int = Field(default=3)
    evidence_required: bool = True
    as_of: dt.datetime | None = None
    known_at: dt.datetime | None = None
    max_results: int = Field(default=50, ge=1, le=100)

    @field_validator("fiscal_years")
    @classmethod
    def _fy_choices(cls, value: int) -> int:
        if value not in (3, 5):
            raise ValueError("fiscal_years must be 3 or 5")
        return value

    @model_validator(mode="after")
    def _operator_requires_threshold(self) -> ScreenRequest:
        if (
            self.operator in (PathOperator.MIN_VALUE, PathOperator.MAX_VALUE)
            and self.threshold is None
        ):
            raise ValueError(
                f"operator {self.operator.value} requires threshold"
            )
        return self


def _connect_read_only(dsn: str) -> psycopg.Connection:
    """只读事务连接：SET TRANSACTION READ ONLY 使写入在服务端被拒绝。"""
    conn = psycopg.connect(dsn)
    conn.execute("SET TRANSACTION READ ONLY")
    return conn


def _apply_statement_timeout(conn: psycopg.Connection, plan: QueryPlan) -> None:
    """timeout_seconds → 事务级 statement_timeout（不遗留长期运行查询）。"""
    conn.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (f"{plan.timeout_seconds * 1000}ms",),
    )


def _record_query_audit(
    settings: Any, *, plan: QueryPlan, status: str,
    natural_question: str | None = None, trace_id: str | None = None,
    error_category: str | None = None, duration_ms: int | None = None,
    response: QueryResponse | None = None,
) -> None:
    """写入查询审计记录（独立可写连接；查询路径本身保持只读）。

    query_id 唯一 + 既有记录保持：同一计划重试安全重执行且不产生多条
    矛盾审计记录。同一 query_id 携带不同 plan 重放属于客户端冲突——
    保留首条记录并以结构化告警留痕（不静默丢弃后至计划）。
    """
    plan_json = plan.model_dump(mode="json")
    with psycopg.connect(settings.postgres_dsn) as conn:
        existing = conn.execute(
            "SELECT plan FROM query.query_audit WHERE query_id = %s",
            (plan.plan_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != plan_json:
                log.warning(
                    "query_audit_conflict",
                    query_id=str(plan.plan_id), trace_id=trace_id,
                    detail="query_id reused with a different plan; "
                           "first audit record retained",
                )
            return
        conn.execute(
            """
            INSERT INTO query.query_audit
                (query_id, natural_question, plan, intent, status,
                 error_category, trace_id, execution_version, period_rule,
                 duration_ms, response)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                plan.plan_id,
                natural_question,
                Json(plan_json),
                plan.intent.value,
                status,
                error_category,
                trace_id,
                QUERY_EXECUTION_VERSION,
                plan.numeric_filter.period_rule
                if plan.numeric_filter else None,
                duration_ms,
                Json(response.model_dump(mode="json")) if response else None,
            ),
        )


def _execute_plan(
    request: Request, plan_source: QueryPlan | Callable[[], QueryPlan],
    natural_question: str | None,
) -> QueryResponse:
    """共用执行路径：只读事务 + 超时 + 审计 + 受控错误类别。

    plan_source 可以是已构造的 QueryPlan（/query）或计划工厂（/screen）。
    计划构造（含注入黑名单与交叉校验）发生在受控 try 块内——
    pydantic ValidationError 是 ValueError 子类，非法计划得到 422
    而非未捕获 500。
    """
    settings = request.app.state.settings
    # 优先外部透传头；缺失时用 TraceIdMiddleware 自动生成的 trace_id
    # （中间件为每个请求生成并回写 X-Trace-Id 响应头）
    from src.core.trace import get_trace_id

    trace_id = request.headers.get("x-trace-id") or get_trace_id()
    executor = getattr(request.app.state, "query_graph_executor", None)
    orchestrator = QueryOrchestrator(graph_executor=executor)
    started = time.perf_counter()
    plan: QueryPlan | None = None

    try:
        plan = plan_source() if callable(plan_source) else plan_source
        assert plan is not None
        with _connect_read_only(settings.postgres_dsn) as conn:
            _apply_statement_timeout(conn, plan)
            result = orchestrator.execute(conn, plan, trace_id=trace_id)
    except ValueError as exc:
        if plan is not None:
            _record_query_audit(
                settings, plan=plan, status="REJECTED",
                natural_question=natural_question, trace_id=trace_id,
                error_category="plan_validation",
            )
        else:
            # 构造期拒绝：尚无规范化计划可审计——结构化留痕，不静默
            log.warning(
                "query_plan_construction_rejected", error=str(exc),
                trace_id=trace_id, natural_question=natural_question,
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.errors.QueryCanceled as exc:
        assert plan is not None
        log.warning("query_timeout", trace_id=trace_id)
        _record_query_audit(
            settings, plan=plan, status="FAILED",
            natural_question=natural_question, trace_id=trace_id,
            error_category="query_timeout",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise HTTPException(status_code=504, detail="query timed out") from exc
    except Exception as exc:
        category = type(exc).__name__
        log.error("query_execution_failed", error_category=category,
                  trace_id=trace_id)
        if plan is not None:
            _record_query_audit(
                settings, plan=plan, status="FAILED",
                natural_question=natural_question, trace_id=trace_id,
                error_category=category,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        raise HTTPException(status_code=500, detail="query execution failed") \
            from exc

    assert plan is not None
    duration_ms = int((time.perf_counter() - started) * 1000)
    _record_query_audit(
        settings, plan=plan, status=result.status.value,
        natural_question=natural_question, trace_id=trace_id,
        duration_ms=duration_ms, response=result,
    )
    return result


# ---- 产品图谱/证据浏览器（issue #32：受控子图 + Claim lineage + 证据详情）----


@router.get("/graph/subgraph")
async def graph_subgraph(
    request: Request,
    company_id: str = Query(default=None, max_length=64),
    hops: int = Query(default=1, ge=1, le=2),
    max_nodes: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    """受控子图：白名单 Cypher 模板（后端执行），限制跳数与节点数。

    图后端不可用/超限时返回 DEGRADED/STALE + PG 事实列表回退
    （issue #32 GWT：明确降级且保留 PostgreSQL 事实）。
    """
    executor = getattr(request.app.state, "query_graph_executor", None)
    return build_subgraph(
        executor,
        company_id=company_id,
        hops=hops,
        max_nodes=max_nodes,
    )


@router.get("/claims/{claim_id}/lineage")
async def claim_lineage(claim_id: str, request: Request) -> dict[str, Any]:
    """Claim lineage：经营边 → Claim → Evidence → Document 的追溯链。"""
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        claim = conn.execute(
            """
            SELECT id, predicate_code, claim_status, business_stage,
                   evidence_state, confidence, valid_from, valid_to,
                   recorded_at, object_entity_id
            FROM fact.claim WHERE id = %s
            """,
            (claim_id,),
        ).fetchone()
        if claim is None:
            raise HTTPException(status_code=404, detail="claim not found")
        evidence = conn.execute(
            """
            SELECT f.id, f.document_id, f.page_number, f.quote_text,
                   f.char_start, f.char_end, f.document_version_id
            FROM fact.claim_evidence ce
            JOIN fact.evidence_fragment f ON f.id = ce.evidence_id
            WHERE ce.claim_id = %s
            """,
            (claim_id,),
        ).fetchall()
        documents = []
        for row in evidence:
            doc = conn.execute(
                """
                SELECT d.id, d.document_type, d.source_system, d.title,
                       d.published_at, dv.version, dv.parse_status
                FROM fact.document d
                LEFT JOIN fact.document_version dv ON dv.id = %s
                WHERE d.id = %s
                """,
                (row[6], row[1]),
            ).fetchone()
            documents.append(doc)

    claim_cols = ["id", "predicate_code", "claim_status", "business_stage",
                  "evidence_state", "confidence", "valid_from", "valid_to",
                  "recorded_at", "object_entity_id"]
    ev_cols = ["id", "document_id", "page_number", "quote_text",
               "char_start", "char_end", "document_version_id"]
    doc_cols = ["id", "document_type", "source_system", "title",
                "published_at", "version", "parse_status"]

    return {
        "claim": dict(zip(claim_cols, claim, strict=True)),
        "evidence": [dict(zip(ev_cols, r, strict=True)) for r in evidence],
        "documents": [
            dict(zip(doc_cols, d, strict=True)) if d else None
            for d in documents
        ],
    }


@router.get("/documents/{document_id}/evidence")
async def document_evidence(
    document_id: str, request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """文档证据列表：片段（页码/原文/字符区间）+ 版本状态。"""
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        doc = conn.execute(
            """
            SELECT d.id, d.document_type, d.source_system, d.title,
                   d.published_at, d.parse_status, dv.version, dv.id AS version_id
            FROM fact.document d
            LEFT JOIN fact.document_version dv
              ON dv.document_id = d.id
             AND dv.version = (SELECT max(version) FROM fact.document_version
                               WHERE document_id = d.id)
            WHERE d.id = %s
            """,
            (document_id,),
        ).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        fragments = conn.execute(
            """
            SELECT f.id, f.page_number, f.quote_text, f.char_start,
                   f.char_end, f.document_version_id
            FROM fact.evidence_fragment f
            WHERE f.document_id = %s
            ORDER BY f.page_number, f.char_start
            LIMIT %s
            """,
            (document_id, limit),
        ).fetchall()

    doc_cols = ["id", "document_type", "source_system", "title",
                "published_at", "parse_status", "version", "version_id"]
    frag_cols = ["id", "page_number", "quote_text", "char_start",
                 "char_end", "document_version_id"]
    return {
        "document": dict(zip(doc_cols, doc, strict=True)),
        "fragments": [dict(zip(frag_cols, r, strict=True)) for r in fragments],
        "count": len(fragments),
    }


@router.post("/query", response_model=QueryResponse)
async def execute_query(body: QueryRequest, request: Request) -> QueryResponse:
    """执行受控查询计划。只读——不修改事实/财务/图（除查询审计）。"""
    return _execute_plan(request, body.plan, body.natural_question)


@router.post("/screen", response_model=QueryResponse)
async def execute_screen(body: ScreenRequest, request: Request) -> QueryResponse:
    """筛选：概念/阶段 + 财务条件的简化查询。

    计划构造经由工厂延迟到 _execute_plan 的受控 try 块内执行——
    非法组合与注入载荷得到 422，而非未捕获 500。
    """

    def build_plan() -> QueryPlan:
        semantic = None
        if body.concept_name or body.business_stage:
            semantic = SemanticFilter(
                concept_name=body.concept_name,
                business_stage=body.business_stage,
            )
        numeric = None
        if body.metric_code:
            # 口径推导：默认按窗口/算子映射（验收 5 不同口径）；
            # 显式 period_rule 由模型交叉校验一致性
            rule = body.period_rule
            if rule is None:
                if body.fiscal_years == 5:
                    rule = "LAST_5_FY"
                elif body.operator is PathOperator.TOTAL_POSITIVE:
                    rule = "LAST_3_FY_TOTAL_POSITIVE"
                elif body.operator is PathOperator.CONSECUTIVE_POSITIVE:
                    rule = "CONSECUTIVE_3_FY_POSITIVE"
                else:
                    rule = "LAST_3_FY"
            numeric = NumericFilter(
                metric_code=body.metric_code,
                operator=body.operator,
                threshold=body.threshold,
                period_rule=rule,
                fiscal_years=body.fiscal_years,
            )
        return QueryPlan(
            intent=QueryIntent.SEMANTIC_SCREEN,
            semantic_filter=semantic,
            numeric_filter=numeric,
            evidence_required=body.evidence_required,
            as_of=body.as_of,
            known_at=body.known_at,
            max_results=body.max_results,
        )

    return _execute_plan(request, build_plan, None)


# ---- 只读实体接口 ----


@router.get("/companies/{company_id}")
async def get_company(company_id: UUID, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT id, canonical_name, unified_social_credit_code, "
            "company_type, status, created_at FROM master.company "
            "WHERE id = %s",
            (company_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="company not found")
    cols = ["id", "canonical_name", "unified_social_credit_code",
            "company_type", "status", "created_at"]
    return dict(zip(cols, row, strict=True))


@router.get("/companies/{company_id}/claims")
async def get_company_claims(
    company_id: UUID, request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT id, predicate_code, claim_status, business_stage, "
            "evidence_state, confidence, valid_from, valid_to, recorded_at "
            "FROM fact.claim WHERE subject_entity_id = %s "
            "ORDER BY recorded_at DESC LIMIT %s",
            (company_id, limit),
        ).fetchall()
    cols = ["id", "predicate_code", "claim_status", "business_stage",
            "evidence_state", "confidence", "valid_from", "valid_to",
            "recorded_at"]
    claims = [dict(zip(cols, row, strict=True)) for row in rows]
    return {"company_id": str(company_id), "claims": claims, "count": len(claims)}


@router.get("/companies/{company_id}/timeline")
async def get_company_timeline(company_id: UUID, request: Request) -> dict[str, Any]:
    """主体时间线：ACCEPTED Claim 的双时态视图。"""
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
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


# ---- 公司列表/搜索与首页汇总（issue #31：受控只读端点）----


@router.get("/companies")
async def list_companies(
    request: Request,
    q: str = Query(default=None, max_length=200),
    stage: str = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict[str, Any]:
    """公司列表/搜索：名称前缀或包含匹配（参数化 LIKE，无自由 SQL）。

    每行携带证券代码与 ACCEPTED 量产 Claim 计数（列表页核心信息）；
    排序固定 canonical_name（确定性分页）。
    """
    settings = request.app.state.settings
    # 搜索词转义 LIKE 通配符（%/_ 按字面处理）
    if q is not None:
        escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
    else:
        pattern = None

    with _connect_read_only(settings.postgres_dsn) as conn:
        conditions = []
        params: list[Any] = []
        if pattern is not None:
            conditions.append("c.canonical_name ILIKE %s")
            params.append(pattern)
        if stage is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM fact.claim k WHERE "
                "k.subject_entity_id = c.id AND k.claim_status = 'ACCEPTED' "
                "AND k.business_stage = %s)"
            )
            params.append(stage)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = conn.execute(
            f"""
            SELECT c.id, c.canonical_name, c.unified_social_credit_code,
                   c.company_type, c.status, c.created_at,
                   (SELECT s.ts_code FROM master.company_security cs
                    JOIN master.security s ON s.id = cs.security_id
                    WHERE cs.company_id = c.id LIMIT 1) AS security_code,
                   (SELECT count(*) FROM fact.claim k
                    WHERE k.subject_entity_id = c.id
                      AND k.claim_status = 'ACCEPTED'
                      AND k.predicate_code = 'PRODUCES') AS produces_claims
            FROM master.company c {where}
            ORDER BY c.canonical_name
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(
            f"SELECT count(*) FROM master.company c {where}", tuple(params)
        ).fetchone()[0]

    cols = ["id", "canonical_name", "unified_social_credit_code",
            "company_type", "status", "created_at", "security_code",
            "produces_claims"]
    companies = [dict(zip(cols, row, strict=True)) for row in rows]
    return {"companies": companies, "total": total, "limit": limit,
            "offset": offset}


@router.get("/overview/stats")
async def overview_stats(request: Request) -> dict[str, Any]:
    """首页真实统计（issue #31）：全部来自事实库真实计数，无示例数字。

    携带数据新鲜度（最新 Claim/财务观察记录时间）——Epic #29 不变量：
    所有统计携带快照/更新时间。
    """
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        companies = conn.execute(
            "SELECT count(*) FROM master.company"
        ).fetchone()[0]
        accepted_claims = conn.execute(
            "SELECT count(*) FROM fact.claim WHERE claim_status = 'ACCEPTED'"
        ).fetchone()[0]
        pending_reviews = conn.execute(
            "SELECT count(*) FROM fact.claim "
            "WHERE claim_status = 'NEEDS_REVIEW'"
        ).fetchone()[0]
        evidence_fragments = conn.execute(
            "SELECT count(*) FROM fact.evidence_fragment"
        ).fetchone()[0]
        financial_observations = conn.execute(
            "SELECT count(*) FROM finance.financial_observation"
        ).fetchone()[0]
        latest_claim_at = conn.execute(
            "SELECT max(recorded_at) FROM fact.claim"
        ).fetchone()[0]
        latest_observation_at = conn.execute(
            "SELECT max(recorded_at) FROM finance.financial_observation"
        ).fetchone()[0]

    return {
        "companies": companies,
        "accepted_claims": accepted_claims,
        "pending_reviews": pending_reviews,
        "evidence_fragments": evidence_fragments,
        "financial_observations": financial_observations,
        "data_freshness": {
            "latest_claim_at": latest_claim_at,
            "latest_observation_at": latest_observation_at,
        },
        "snapshot_note": (
            "统计数据基于当前数据库内容；若为合成快照，公司数据非真实上市公司。"
        ),
    }


@router.get("/overview/recent-runs")
async def overview_recent_runs(
    request: Request, limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """最近采集/标准化运行（首页"最近活动"卡片）。"""
    settings = request.app.state.settings

    with _connect_read_only(settings.postgres_dsn) as conn:
        ingest = conn.execute(
            """
            SELECT id, dataset_name, source_system, status, finished_at
            FROM ops.ingest_run
            WHERE finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
        normalization = conn.execute(
            """
            SELECT id, dataset_name, status, finished_at
            FROM ops.normalization_run
            WHERE finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
    ingest_cols = ["id", "dataset_name", "source_system", "status",
                   "finished_at"]
    norm_cols = ["id", "dataset_name", "status", "finished_at"]
    return {
        "ingest_runs": [dict(zip(ingest_cols, r, strict=True)) for r in ingest],
        "normalization_runs": [
            dict(zip(norm_cols, r, strict=True)) for r in normalization
        ],
    }
