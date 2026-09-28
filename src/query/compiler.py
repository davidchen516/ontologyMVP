"""图查询编译器 + 财务查询编译器 + 编排器（issue #9）。

- 图编译器：只使用白名单参数化 Cypher 模板；max_hops/max_results 受限。
- 财务编译器：只使用 financial_metric_catalog 白名单指标 + PeriodRule。
- 编排器：图语义候选 → SQL 财务过滤 → Claim/Evidence 加载 → Grounded 响应。
"""

from __future__ import annotations

from typing import Any

from src.query.models import (
    GroundedResult,
    QueryPlan,
    QueryResponse,
    QueryStatus,
)

# 白名单 Cypher 模板（图语义候选——参数化，无自由拼接）
GRAPH_TEMPLATES: dict[str, str] = {
    "companies_by_concept":
        "MATCH (c:Company)-[t:TAGGED_AS]->(k:Concept) "
        "WHERE k.canonical_name = $concept_name "
        "RETURN c.id AS company_id, c.canonical_name AS company_name, "
        "k.canonical_name AS concept, t.snapshot_date AS snapshot",
    "companies_by_stage":
        "MATCH (c:Company)-[r:PRODUCES]->(p:Product) "
        "WHERE r.business_stage = $business_stage AND r.active = true "
        "RETURN c.id AS company_id, c.canonical_name AS company_name, "
        "r.claim_id AS claim_id, r.business_stage AS stage",
    "explain_relation":
        "MATCH path = (c1:Company {id: $source_id})-"
        "[:PRODUCES|SUPPLIES_TO|USES_TECHNOLOGY*1..$max_hops]-(c2) "
        "WHERE c2.id = $target_id "
        "RETURN [n IN nodes(path) | n.canonical_name] AS path_nodes, "
        "[r IN relationships(path) | type(r)] AS path_rels",
    "timeline_for_company":
        "MATCH (c:Company {id: $company_id})-[r:PRODUCES]->(p:Product) "
        "RETURN r.claim_id AS claim_id, r.business_stage AS stage, "
        "r.valid_from AS valid_from, r.valid_to AS valid_to, "
        "p.canonical_name AS product_name",
}

# 白名单财务指标（与 #4 standardize 注册的指标对齐）
ALLOWED_METRICS = frozenset({
    "NET_CF_OPERATING", "TOTAL_REVENUE", "NET_INCOME", "ROE", "NET_PROFIT_YOY",
})

PERIOD_RULES = frozenset({
    "LAST_3_FY", "LAST_5_FY", "CONSECUTIVE_3_FY_POSITIVE",
    "LAST_3_FY_TOTAL_POSITIVE",
})


class GraphCompiler:
    """图查询编译器：QueryPlan → 参数化 Cypher。"""

    @staticmethod
    def compile(plan: QueryPlan) -> tuple[str, dict[str, Any]] | None:
        """返回 (template, params) 或 None（无需图查询）。"""
        sf = plan.semantic_filter
        if sf is None:
            return None
        if sf.concept_name:
            return GRAPH_TEMPLATES["companies_by_concept"], {
                "concept_name": sf.concept_name,
            }
        if sf.business_stage:
            return GRAPH_TEMPLATES["companies_by_stage"], {
                "business_stage": sf.business_stage,
            }
        if plan.intent.value == "EXPLAIN_RELATION" and plan.subject and plan.subject.entity_id:
            return GRAPH_TEMPLATES["explain_relation"], {
                "source_id": str(plan.subject.entity_id),
                "target_id": str(plan.subject.entity_id),  # MVP：简化
                "max_hops": min(sf.max_hops, 4),
            }
        if plan.intent.value == "TIMELINE" and plan.subject and plan.subject.entity_id:
            return GRAPH_TEMPLATES["timeline_for_company"], {
                "company_id": str(plan.subject.entity_id),
            }
        return None


class FinancialCompiler:
    """财务查询编译器：QueryPlan → 参数化 SQL（白名单指标 + PeriodRule）。"""

    @staticmethod
    def compile(plan: QueryPlan) -> tuple[str, dict[str, Any]] | None:
        nf = plan.numeric_filter
        if nf is None:
            return None
        # 白名单校验
        if nf.metric_code not in ALLOWED_METRICS:
            raise ValueError(
                f"metric {nf.metric_code!r} not in allowed catalog"
            )
        if nf.period_rule not in PERIOD_RULES:
            raise ValueError(f"period_rule {nf.period_rule!r} not supported")
        # 只读参数化 SQL——只查 finance.financial_observation
        sql = """
        SELECT fo.security_id, fo.metric_code, fo.period_end, fo.value,
               fo.currency, fo.report_type
        FROM finance.v_financial_observation_current fo
        WHERE fo.metric_code = %(metric_code)s
          AND fo.period_end >= %(period_start)s
          AND fo.period_end <= %(period_end)s
        ORDER BY fo.period_end DESC
        """
        return sql, {
            "metric_code": nf.metric_code,
            "period_start": "2020-12-31",
            "period_end": "2030-12-31",
        }


def _load_claims_for_company(conn: Any, company_id: Any, plan: QueryPlan) -> list[dict]:
    """加载 ACCEPTED Claim + Evidence，按 as_of/known_at 过滤。"""
    params: list[Any] = [company_id]
    conditions = ["c.claim_status = 'ACCEPTED'", "c.subject_entity_id = %s"]
    if plan.as_of:
        conditions.append(
            "(c.valid_from IS NULL OR c.valid_from <= %s) "
            "AND (c.valid_to IS NULL OR c.valid_to > %s)"
        )
        params.extend([plan.as_of, plan.as_of])
    if plan.known_at:
        conditions.append("c.recorded_at <= %s")
        params.append(plan.known_at)

    rows = conn.execute(  # noqa: SLF001
        f"""
        SELECT c.id, c.predicate_code, c.business_stage, c.evidence_state,
               c.confidence, c.valid_from, c.valid_to, c.recorded_at
        FROM fact.claim c
        WHERE {' AND '.join(conditions)}
        ORDER BY c.recorded_at DESC
        LIMIT %s
        """,
        (*params, plan.max_results),
    ).fetchall()
    columns = ["id", "predicate_code", "business_stage", "evidence_state",
               "confidence", "valid_from", "valid_to", "recorded_at"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


class QueryOrchestrator:
    """查询编排器：图候选 → 财务过滤 → Claim/Evidence → Grounded 响应。"""

    def __init__(self, graph_executor: Any | None = None) -> None:
        self._graph_executor = graph_executor

    def execute(self, conn: Any, plan: QueryPlan, trace_id: str | None = None) -> QueryResponse:
        """执行受控查询计划。"""
        results: list[GroundedResult] = []
        excluded: list[str] = []
        unknowns: list[str] = []
        degraded = False
        notes: list[str] = []

        # 1. 图语义候选
        candidates: list[dict[str, Any]] = []
        graph_query = GraphCompiler.compile(plan)
        if graph_query:
            template, params = graph_query
            if self._graph_executor:
                try:
                    candidates = self._graph_executor.execute(template, params)
                except Exception:  # noqa: BLE001
                    degraded = True
                    notes.append("graph backend unavailable - degraded to PG-only")
            else:
                degraded = True
                notes.append("graph backend not configured - degraded to PG-only")

        # 2. Claim/Evidence 加载（PG 直查——图不可用时的降级路径）
        if not candidates:
            candidates = self._pg_semantic_candidates(conn, plan)

        # 3. 逐候选加载 Claim/Evidence + 财务
        for candidate in candidates[: plan.max_results]:
            company_id = candidate.get("company_id")
            if company_id is None:
                continue
            row = conn.execute(  # noqa: SLF001
                "SELECT canonical_name FROM master.company WHERE id = %s",
                (company_id,),
            ).fetchone()
            company_name = row[0] if row else "Unknown"

            claims = _load_claims_for_company(conn, company_id, plan)
            if not claims:
                excluded.append(f"{company_name}: no matching claims")
                continue

            # Evidence 检查
            evidence_ids: list[Any] = []
            for claim in claims:
                ev_rows = conn.execute(  # noqa: SLF001
                    "SELECT evidence_id FROM fact.claim_evidence WHERE claim_id = %s LIMIT 5",
                    (claim["id"],),
                ).fetchall()
                evidence_ids.extend(r[0] for r in ev_rows)

            if plan.evidence_required and not evidence_ids:
                unknowns.append(
                    f"{company_name}: claim without evidence (excluded from results)"
                )
                continue

            # 财务值
            fin_value = None
            report_period = None
            if plan.numeric_filter:
                from src.standardize.financial import recent_three_fy_operating_cashflow

                sec_row = conn.execute(  # noqa: SLF001
                    "SELECT cs.security_id FROM master.company_security cs "
                    "WHERE cs.company_id = %s LIMIT 1", (company_id,),
                ).fetchone()
                if sec_row:
                    fin_result = recent_three_fy_operating_cashflow(
                        conn, security_id=sec_row[0]
                    )
                    fys = fin_result["fiscal_years"]
                    fin_value = fys[0]["value"] if fys else None
                    report_period = fys[0]["period_end"] if fys else None
                    if not fin_result["sufficient"]:
                        excluded.append(
                            f"{company_name}: insufficient financial data "
                            f"({fin_result['reason']})"
                        )
                        continue

            stage = claims[0].get("business_stage") if claims else None
            evidence_state = claims[0].get("evidence_state") if claims else None
            results.append(GroundedResult(
                company_id=company_id,
                company_name=company_name,
                claim_ids=[c["id"] for c in claims[:5]],
                business_stage=stage,
                evidence_state=evidence_state,
                financial_value=fin_value,
                report_period=report_period,
                currency="CNY",
                evidence_ids=[str(e) for e in evidence_ids[:5]],
                reasoning_path=[
                    f"concept filter -> {plan.semantic_filter.concept_name}"
                    if plan.semantic_filter and plan.semantic_filter.concept_name
                    else "direct query",
                    f"claims loaded: {len(claims)}",
                    f"evidence attached: {len(evidence_ids)}",
                ],
                data_freshness="realtime" if not degraded else "pg-only",
            ))

        status = QueryStatus.DEGRADED if degraded else QueryStatus.SUCCEEDED
        return QueryResponse(
            query_id=plan.plan_id, status=status, intent=plan.intent,
            results=results, excluded=excluded, unknowns=unknowns,
            period_rule=plan.numeric_filter.period_rule if plan.numeric_filter else None,
            degraded=degraded, degradation_notes=notes, trace_id=trace_id,
        )

    def _pg_semantic_candidates(self, conn: Any, plan: QueryPlan) -> list[dict]:
        """降级路径：直接从 PG 查 ACCEPTED Claim 作为候选。"""
        sf = plan.semantic_filter
        conditions = ["c.claim_status = 'ACCEPTED'"]
        params: list[Any] = []
        if sf and sf.business_stage:
            conditions.append("c.business_stage = %s")
            params.append(sf.business_stage)
        rows = conn.execute(  # noqa: SLF001
            f"""
            SELECT DISTINCT c.subject_entity_id AS company_id
            FROM fact.claim c
            WHERE {' AND '.join(conditions)}
            LIMIT %s
            """,
            (*params, plan.max_results),
        ).fetchall()
        return [{"company_id": row[0]} for row in rows]
