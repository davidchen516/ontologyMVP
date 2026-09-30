"""图查询编译器 + 财务查询编译器 + 编排器（issue #9）。

- 图编译器：只使用白名单参数化 Cypher 模板；max_hops/max_results 受限。
- 财务编译器：只使用白名单指标 + PeriodRule，且在编排器入口强制执行
  （非法指标/规则在计划校验阶段被拒绝，绝不进入执行）。
- 编排器：图语义候选 → SQL 财务过滤 → Claim/Evidence 加载 → Grounded 响应。
- 降级策略：图不可用/投影滞后 → 明确 DEGRADED + 说明；PG 无法评估的过滤
  （如 concept）诚实返回空候选并说明原因，绝不静默返回未过滤结果。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.query.models import (
    GroundedResult,
    PathOperator,
    QueryIntent,
    QueryPlan,
    QueryResponse,
    QueryStatus,
)

# 查询执行版本：响应与审计记录携带，模板/规则变更时递增（issue 回滚要求）
QUERY_EXECUTION_VERSION = "0.1.0"

# 白名单 Cypher 模板（图语义候选——参数化，无自由拼接）。
# explain_relation 的 {max_hops} 占位符在渲染时以 Schema 校验过的 int(1..4) 内联
# （Neo4j 不支持变长路径上界参数化）。
GRAPH_TEMPLATES: dict[str, str] = {
    "companies_by_concept":
        "MATCH (c:Company)-[t:TAGGED_AS]->(k:Concept) "
        "WHERE k.canonical_name = $concept_name "
        "RETURN DISTINCT c.id AS company_id, c.canonical_name AS company_name, "
        "k.canonical_name AS concept, t.snapshot_date AS snapshot",
    "companies_by_stage":
        "MATCH (c:Company)-[r:PRODUCES]->(p:Product) "
        "WHERE r.business_stage = $business_stage AND r.active = true "
        "RETURN DISTINCT c.id AS company_id, c.canonical_name AS company_name, "
        "r.business_stage AS stage",
    "explain_relation":
        "MATCH path = (c1:Company {id: $source_id})-"
        "[:PRODUCES|SUPPLIES_TO|USES_TECHNOLOGY*1..{max_hops}]-(c2) "
        "WHERE c2.id = $target_id "
        "RETURN [n IN nodes(path) | n.canonical_name] AS path_nodes, "
        "[r IN relationships(path) | type(r)] AS path_rels",
    "timeline_for_company":
        "MATCH (c:Company {id: $company_id})-[r:PRODUCES]->(p:Product) "
        "RETURN r.claim_id AS claim_id, r.business_stage AS stage, "
        "r.valid_from AS valid_from, r.valid_to AS valid_to, "
        "p.canonical_name AS product_name",
}


def render_explain_template(max_hops: int) -> str:
    """渲染 explain_relation 模板；max_hops 由 Schema 限定 1..4，内联为 int。"""
    hops = int(max_hops)
    if not 1 <= hops <= 4:
        raise ValueError("max_hops out of allowed range 1..4")
    return GRAPH_TEMPLATES["explain_relation"].replace("{max_hops}", str(hops))


# 执行器允许的完整模板集合（explain 的 {max_hops} 1..4 全部内联变体；
# 原始含占位符的模板不在允许集合中——真实驱动无法解析占位符）
ALLOWED_GRAPH_QUERIES: frozenset[str] = frozenset(
    {v for k, v in GRAPH_TEMPLATES.items() if k != "explain_relation"}
    | {render_explain_template(h) for h in range(1, 5)}
)

# 白名单财务指标（与 #4 standardize 注册的指标对齐）
ALLOWED_METRICS = frozenset({
    "NET_CF_OPERATING", "TOTAL_REVENUE", "NET_INCOME", "ROE", "NET_PROFIT_YOY",
})

# PeriodRule 白名单（口径标识，随响应/审计返回；与 operator/fiscal_years
# 的一致性由 NumericFilter 模型校验强制）
PERIOD_RULES = frozenset({
    "LAST_1_FY", "LAST_3_FY", "LAST_5_FY", "CONSECUTIVE_3_FY_POSITIVE",
    "LAST_3_FY_TOTAL_POSITIVE",
})


class GraphCompiler:
    """图查询编译器：QueryPlan → 白名单参数化 Cypher。"""

    @staticmethod
    def compile(plan: QueryPlan) -> tuple[str, dict[str, Any]] | None:
        """返回 (template, params) 或 None（该计划无需图查询）。

        计划不合法（如 EXPLAIN_RELATION 缺实体、TIMELINE 缺主体）时抛
        ValueError——由 API 层转为受控 422，不猜测执行。
        """
        sf = plan.semantic_filter

        # intent 专属模板优先（不依赖 semantic_filter）
        if plan.intent is QueryIntent.EXPLAIN_RELATION:
            subject_id = plan.subject.entity_id if plan.subject else None
            object_id = (
                plan.object_entity.entity_id if plan.object_entity else None
            )
            if not (subject_id and object_id):
                raise ValueError(
                    "EXPLAIN_RELATION requires both subject and object "
                    "with entity_id"
                )
            hops = sf.max_hops if sf is not None else 2
            return render_explain_template(min(hops, 4)), {
                "source_id": str(subject_id),
                "target_id": str(object_id),
            }
        if plan.intent is QueryIntent.TIMELINE:
            if not (plan.subject and plan.subject.entity_id):
                raise ValueError("TIMELINE requires subject entity_id")
            return GRAPH_TEMPLATES["timeline_for_company"], {
                "company_id": str(plan.subject.entity_id),
            }

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
        return None


class FinancialCompiler:
    """财务查询编译器：QueryPlan → 参数化 SQL（白名单指标 + PeriodRule）。

    PeriodRule/Operator 语义（验收 5，「合计为正」≠「连续均为正」）：
    - TOTAL_POSITIVE       → 窗口内 N 个完整 FY 合计 > threshold（默认 0）；
    - CONSECUTIVE_POSITIVE → 窗口内 N 个完整 FY 每一年 > threshold（默认 0）；
    - MIN_VALUE / MAX_VALUE→ 最近一个完整 FY 值 >= / <= threshold（必须给 threshold）。
    完整 FY = period_end 为 12-31 的年报口径观察且 value 非 NULL；
    窗口不足 N 个完整 FY 时判 insufficient，不把缺失当 0。
    双重上市（多证券映射同一公司）时同一财年只计一次（最新公告优先），
    避免同财年双计、更早财年被挤出窗口。
    """

    @staticmethod
    def compile(plan: QueryPlan) -> tuple[str, dict[str, Any]] | None:
        """校验并编译财务过滤；非法指标/规则/算子抛 ValueError（→ 422）。"""
        nf = plan.numeric_filter
        if nf is None:
            return None
        if nf.metric_code not in ALLOWED_METRICS:
            raise ValueError(
                f"metric {nf.metric_code!r} not in allowed catalog"
            )
        if nf.period_rule not in PERIOD_RULES:
            raise ValueError(f"period_rule {nf.period_rule!r} not supported")
        if (
            nf.operator in (PathOperator.MIN_VALUE, PathOperator.MAX_VALUE)
            and nf.threshold is None
        ):
            raise ValueError(
                f"operator {nf.operator.value} requires threshold"
            )
        # 只读参数化 SQL——当前值视图（重述按明示规则选择）+ 双时态过滤；
        # 同财年多证券行按 announced_at 降序排列，Python 侧按 period_end 去重
        sql = """
        SELECT cs.company_id, fo.security_id, fo.period_end, fo.value,
               fo.currency, fo.report_type, fo.announced_at
        FROM finance.v_financial_observation_current fo
        JOIN master.company_security cs ON cs.security_id = fo.security_id
        WHERE fo.metric_code = %(metric_code)s
          AND EXTRACT(MONTH FROM fo.period_end) = 12
          AND EXTRACT(DAY FROM fo.period_end) = 31
          AND fo.value IS NOT NULL
          AND (%(as_of_date)s::date IS NULL OR fo.period_end <= %(as_of_date)s::date)
          AND (%(known_at)s::timestamptz IS NULL OR fo.announced_at IS NULL
               OR fo.announced_at <= %(known_at)s::timestamptz)
        ORDER BY fo.period_end DESC, fo.announced_at DESC NULLS LAST
        """
        as_of_date = plan.as_of.date() if plan.as_of else None
        return sql, {
            "metric_code": nf.metric_code,
            "as_of_date": as_of_date,
            "known_at": plan.known_at,
        }


def evaluate_numeric_filter(
    conn: Any, plan: QueryPlan
) -> dict[Any, dict[str, Any]]:
    """按财务编译器的 SQL 载入观察值，逐公司评估 operator/PeriodRule 语义。

    必须在 plan.numeric_filter 存在时调用；返回
    {company_id: {sufficient, passed, value, reason, currency, detail, available}}。
    """
    compiled = FinancialCompiler.compile(plan)  # 校验在此发生（ValueError → 422）
    if compiled is None:
        return {}
    sql, params = compiled
    rows = conn.execute(sql, params).fetchall()  # noqa: SLF001

    # 按公司分组，同一财年（period_end）只保留最新公告的观察（双重上市去重）
    by_company: dict[Any, dict[dt.date, tuple[float, str | None]]] = {}
    for row in rows:
        company_id, _security_id, period_end, value, cur = (
            row[0], row[1], row[2], row[3], row[4]
        )
        fy_map = by_company.setdefault(company_id, {})
        if period_end not in fy_map:  # 行序：period_end/announced_at 均降序
            fy_map[period_end] = (float(value), cur)

    nf = plan.numeric_filter
    assert nf is not None  # 由调用方保证
    threshold = nf.threshold if nf.threshold is not None else 0.0

    evaluations: dict[Any, dict[str, Any]] = {}
    for company_key, fy_map in by_company.items():
        company_id = str(company_key)
        fys = sorted(fy_map.items(), key=lambda item: item[0], reverse=True)
        window = fys[: nf.fiscal_years]
        values = [entry[0] for _, entry in window]
        currencies = [entry[1] for _, entry in window]
        currency = next((c for c in currencies if c), None)
        entry: dict[str, Any] = {
            "sufficient": len(window) == nf.fiscal_years,
            "currency": currency,
            "detail": [
                {"period_end": period.isoformat(), "value": value}
                for period, (value, _cur) in window
            ],
            "available": len(fy_map),
        }
        if len(window) < nf.fiscal_years:
            entry.update(
                passed=False, value=None,
                reason="insufficient_complete_fiscal_years",
            )
        elif nf.operator is PathOperator.TOTAL_POSITIVE:
            total = sum(values)
            entry.update(value=total, passed=total > threshold, reason=None)
        elif nf.operator is PathOperator.CONSECUTIVE_POSITIVE:
            weakest = min(values)
            entry.update(
                value=weakest, passed=all(v > threshold for v in values),
                reason=None,
            )
        elif nf.operator is PathOperator.MIN_VALUE:
            entry.update(value=values[0], passed=values[0] >= threshold,
                         reason=None)
        else:  # MAX_VALUE
            entry.update(value=values[0], passed=values[0] <= threshold,
                         reason=None)
        evaluations[company_id] = entry
    return evaluations


def _load_claims_for_company(conn: Any, company_id: Any, plan: QueryPlan) -> list[dict]:
    """加载 ACCEPTED Claim，按 as_of/known_at 分别过滤（双时态不混用）。"""
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
               c.confidence, c.valid_from, c.valid_to, c.recorded_at,
               c.object_entity_id
        FROM fact.claim c
        WHERE {' AND '.join(conditions)}
        ORDER BY c.recorded_at DESC
        LIMIT %s
        """,
        (*params, plan.max_results),
    ).fetchall()
    columns = ["id", "predicate_code", "business_stage", "evidence_state",
               "confidence", "valid_from", "valid_to", "recorded_at",
               "object_entity_id"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _count_conflicted_claims(conn: Any, company_id: Any) -> int:
    """冲突 Claim 计数：同主体存在 CONTRADICTED/SUPERSEDED 时结果带警告。"""
    return conn.execute(  # noqa: SLF001
        "SELECT count(*) FROM fact.claim "
        "WHERE subject_entity_id = %s "
        "AND claim_status IN ('CONTRADICTED', 'SUPERSEDED')",
        (company_id,),
    ).fetchone()[0]


class QueryOrchestrator:
    """查询编排器：图候选 → 财务过滤 → Claim/Evidence → Grounded 响应。"""

    def __init__(self, graph_executor: Any | None = None) -> None:
        self._graph_executor = graph_executor

    def execute(self, conn: Any, plan: QueryPlan, trace_id: str | None = None) -> QueryResponse:
        """执行受控查询计划（只读）。"""
        if plan.intent is QueryIntent.EXPLAIN_RELATION:
            return self._execute_explain_relation(conn, plan, trace_id)

        results: list[GroundedResult] = []
        excluded: list[str] = []
        unknowns: list[str] = []
        conflicts: list[str] = []
        notes: list[str] = []

        # 0. 财务计划编译 + 评估（白名单校验在此发生，非法值 → ValueError → 422）
        fin_evals: dict[Any, dict[str, Any]] = {}
        if plan.numeric_filter:
            fin_evals = evaluate_numeric_filter(conn, plan)

        # 1. 语义候选：图优先；图不可用/滞后时 PG 降级（明确说明）
        candidates, candidate_source = self._semantic_candidates(conn, plan, notes)

        # 2. 逐候选：财务过滤 + Claim/Evidence 加载 + 冲突检查
        seen_companies: set[str] = set()
        for candidate in candidates[: plan.max_results]:
            raw_id = candidate.get("company_id")
            if raw_id is None:
                continue
            company_id = str(raw_id)  # 图候选为 str，PG 候选为 UUID——统一
            if company_id in seen_companies:
                continue
            seen_companies.add(company_id)
            row = conn.execute(  # noqa: SLF001
                "SELECT canonical_name FROM master.company WHERE id = %s",
                (company_id,),
            ).fetchone()
            company_name = row[0] if row else "Unknown"

            claims = _load_claims_for_company(conn, company_id, plan)
            if not claims:
                excluded.append(f"{company_name}: no matching claims")
                continue

            # 财务过滤（PeriodRule 语义评估）
            fin = None
            if plan.numeric_filter:
                fin = fin_evals.get(company_id)
                if fin is None:
                    excluded.append(
                        f"{company_name}: no financial observations for "
                        f"metric {plan.numeric_filter.metric_code}"
                    )
                    continue
                if not fin["sufficient"]:
                    excluded.append(
                        f"{company_name}: insufficient financial data "
                        f"({fin['reason']}: {fin['available']} complete FY, "
                        f"need {plan.numeric_filter.fiscal_years})"
                    )
                    continue
                if not fin["passed"]:
                    excluded.append(
                        f"{company_name}: financial filter not passed "
                        f"({plan.numeric_filter.operator.value}="
                        f"{fin['value']})"
                    )
                    continue

            # Evidence 检查 + 可定位引文（来源/页码/原文——issue #11 验收 8）
            evidence_ids: list[Any] = []
            evidence_quotes: list[dict[str, Any]] = []
            for claim in claims:
                ev_rows = conn.execute(  # noqa: SLF001
                    "SELECT f.id, f.page_number, f.quote_text "
                    "FROM fact.claim_evidence ce "
                    "JOIN fact.evidence_fragment f ON f.id = ce.evidence_id "
                    "WHERE ce.claim_id = %s LIMIT 5",
                    (claim["id"],),
                ).fetchall()
                evidence_ids.extend(r[0] for r in ev_rows)
                evidence_quotes.extend({
                    "evidence_id": str(r[0]),
                    "page_number": r[1],
                    "quote_text": r[2],
                } for r in ev_rows)

            if plan.evidence_required and not evidence_ids:
                unknowns.append(
                    f"{company_name}: claim without evidence (excluded from results)"
                )
                continue

            conflicted = _count_conflicted_claims(conn, company_id)
            if conflicted:
                conflicts.append(
                    f"{company_name}: {conflicted} contradicted/superseded "
                    "claim(s) present - results carry this warning"
                )

            # 证券代码 + 标准产品（PRODUCES 对象实体）
            security_row = conn.execute(  # noqa: SLF001
                "SELECT s.ts_code FROM master.company_security cs "
                "JOIN master.security s ON s.id = cs.security_id "
                "WHERE cs.company_id = %s LIMIT 1",
                (company_id,),
            ).fetchone()
            product_row = None
            for claim in claims:
                if not claim.get("object_entity_id"):
                    continue
                product_row = conn.execute(  # noqa: SLF001
                    "SELECT canonical_name FROM master.product "
                    "WHERE id = %s LIMIT 1",
                    (claim["object_entity_id"],),
                ).fetchone()
                if product_row:
                    break

            # 推理路径：只描述实际执行过的步骤
            reasoning = self._reasoning_path(plan, candidate_source, claims,
                                             evidence_ids, fin)
            results.append(GroundedResult(
                company_id=company_id,
                company_name=company_name,
                security_code=security_row[0] if security_row else None,
                standard_product=product_row[0] if product_row else None,
                claim_ids=[c["id"] for c in claims[:5]],
                business_stage=claims[0].get("business_stage"),
                evidence_state=claims[0].get("evidence_state"),
                valid_from=str(claims[0]["valid_from"])
                if claims[0].get("valid_from") else None,
                valid_to=str(claims[0]["valid_to"])
                if claims[0].get("valid_to") else None,
                financial_value=fin["value"] if fin else None,
                report_period=self._report_period(fin),
                currency=fin["currency"] if fin else None,
                financial_detail=fin["detail"] if fin else [],
                evidence_ids=[str(e) for e in evidence_ids[:5]],
                evidence_quotes=evidence_quotes[:5],
                reasoning_path=reasoning,
                data_freshness=self._freshness(candidate_source),
            ))

        degraded = candidate_source in ("pg-stale", "pg-unavailable")
        return QueryResponse(
            query_id=plan.plan_id, status=QueryStatus.DEGRADED if degraded
            else QueryStatus.SUCCEEDED,
            intent=plan.intent,
            results=results, excluded=excluded, unknowns=unknowns,
            conflicts=conflicts,
            period_rule=plan.numeric_filter.period_rule
            if plan.numeric_filter else None,
            degraded=degraded, degradation_notes=notes, trace_id=trace_id,
        )

    # ---- 语义候选 ----

    def _semantic_candidates(
        self, conn: Any, plan: QueryPlan, notes: list[str]
    ) -> tuple[list[dict[str, Any]], str]:
        """返回 (candidates, source)。source ∈ graph|pg-stale|pg-unavailable|pg。

        图执行成功 → 图结果权威（空即空）；图返回空但 PG 可验证出候选 →
        投影滞后，降级到 PG 并明确说明；图不可用 → PG 降级。
        PG 无法评估的过滤（concept）在降级时返回空候选 + 说明，绝不静默
        返回未过滤结果。
        """
        graph_query = GraphCompiler.compile(plan)
        if graph_query is not None and self._graph_executor is not None:
            template, params = graph_query
            try:
                rows = self._graph_executor.execute(template, params)
            except Exception:  # noqa: BLE001
                notes.append(
                    "graph backend unreachable - degraded to PG-only candidates"
                )
                return self._pg_semantic_candidates(conn, plan, notes,
                                                    "pg-unavailable")
            candidates = [dict(r) for r in rows]
            if not candidates:
                # 图空结果：可能是权威空，也可能是投影滞后——PG 可验证时区分。
                # 探测用一次性 notes，避免把降级式说明泄露到权威成功响应里
                pg_candidates, _probe_notes = self._pg_semantic_candidates(
                    conn, plan, [], "pg-stale"
                )
                if pg_candidates:
                    notes.append(
                        f"graph projection stale: graph returned 0 candidates "
                        f"but PG has {len(pg_candidates)} accepted-claim "
                        "candidates - degraded to PG-only"
                    )
                    return pg_candidates, "pg-stale"
            return candidates, "graph"
        if graph_query is not None:
            notes.append(
                "graph backend not configured - degraded to PG-only candidates"
            )
            return self._pg_semantic_candidates(conn, plan, notes,
                                                "pg-unavailable")
        # 无图查询的计划（纯 PG 过滤，如 ENTITY_LOOKUP 主体直查/纯财务筛选）
        return self._pg_semantic_candidates(conn, plan, notes, "pg")

    def _pg_semantic_candidates(
        self, conn: Any, plan: QueryPlan, notes: list[str], source: str
    ) -> tuple[list[dict[str, Any]], str]:
        """PG 降级候选：business_stage/主体可在 PG 验证；concept 不可评估。"""
        sf = plan.semantic_filter
        if sf is not None and sf.concept_name:
            # PG 没有 concept 投影——诚实返回空 + 说明，不伪造过滤结果
            notes.append(
                f"semantic concept filter {sf.concept_name!r} requires graph "
                "projection and is not evaluable in PG fallback - returning "
                "no candidates rather than unfiltered results"
            )
            return [], source
        if plan.subject and plan.subject.entity_id and sf is None:
            return [{"company_id": plan.subject.entity_id}], source
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
        return [{"company_id": row[0]} for row in rows], source

    # ---- EXPLAIN_RELATION 专用路径 ----

    def _execute_explain_relation(
        self, conn: Any, plan: QueryPlan, trace_id: str | None
    ) -> QueryResponse:
        """关系解释：需要图路径；图不可用/无路径时诚实降级说明。"""
        notes: list[str] = []
        unknowns: list[str] = []
        results: list[GroundedResult] = []

        graph_query = GraphCompiler.compile(plan)  # 校验主体/客体（→ 422）
        template, params = graph_query  # type: ignore[misc]
        assert plan.subject and plan.subject.entity_id
        assert plan.object_entity and plan.object_entity.entity_id

        paths: list[dict[str, Any]] = []
        degraded = False
        if self._graph_executor is None:
            degraded = True
            notes.append(
                "relation path requires graph projection - not evaluable "
                "in PG fallback"
            )
        else:
            try:
                paths = [dict(r) for r in
                         self._graph_executor.execute(template, params)]
            except Exception:  # noqa: BLE001
                degraded = True
                notes.append(
                    "graph backend unreachable - relation path not evaluable"
                )

        reasoning: list[str] = []
        if paths:
            first = paths[0]
            nodes = [str(n) for n in first.get("path_nodes", [])]
            rels = [str(r) for r in first.get("path_rels", [])]
            segments: list[str] = []
            for idx, node in enumerate(nodes):
                segments.append(node)
                if idx < len(rels):
                    segments.append(f"-[{rels[idx]}]->")
            reasoning.append(
                "graph path: " + " - ".join(segments) + f" ({len(rels)} hops)"
            )
        elif degraded:
            unknowns.append(
                "relation path not evaluable: graph projection unavailable"
            )
        else:
            unknowns.append(
                "no relation path found between subject and object "
                "within max_hops"
            )

        # 关系两端公司仍从 PG 加载 Grounded 证据（路径存在时）
        if paths:
            for entity in (plan.subject, plan.object_entity):
                company_id = entity.entity_id if entity else None
                if company_id is None:
                    continue
                row = conn.execute(  # noqa: SLF001
                    "SELECT canonical_name FROM master.company WHERE id = %s",
                    (company_id,),
                ).fetchone()
                if not row:
                    continue
                claims = _load_claims_for_company(conn, company_id, plan)
                results.append(GroundedResult(
                    company_id=company_id,
                    company_name=row[0],
                    claim_ids=[c["id"] for c in claims[:5]],
                    business_stage=claims[0].get("business_stage") if claims else None,
                    evidence_state=claims[0].get("evidence_state") if claims else None,
                    reasoning_path=list(reasoning),
                    data_freshness=self._freshness("graph"),
                ))

        return QueryResponse(
            query_id=plan.plan_id,
            status=QueryStatus.DEGRADED if degraded else QueryStatus.SUCCEEDED,
            intent=plan.intent, results=results, unknowns=unknowns,
            period_rule=None, degraded=degraded, degradation_notes=notes,
            trace_id=trace_id,
        )

    # ---- 解释辅助 ----

    @staticmethod
    def _reasoning_path(
        plan: QueryPlan, candidate_source: str, claims: list[dict],
        evidence_ids: list[Any], fin: dict[str, Any] | None,
    ) -> list[str]:
        """推理路径只描述实际执行的步骤（不伪造未应用的过滤）。"""
        steps: list[str] = []
        sf = plan.semantic_filter
        if candidate_source == "graph":
            if sf and sf.concept_name:
                steps.append(f"graph concept filter applied: {sf.concept_name}")
            elif sf and sf.business_stage:
                steps.append(f"graph stage filter applied: {sf.business_stage}")
            else:
                steps.append("graph semantic candidates")
        elif candidate_source == "pg-stale":
            steps.append(
                "graph projection stale - PG fallback "
                f"(business_stage={sf.business_stage if sf else None} "
                "verified in PG)"
            )
        elif candidate_source == "pg-unavailable":
            steps.append("graph unavailable - PG fallback candidates")
        else:
            steps.append("PG candidates (no graph query required)")
        if plan.numeric_filter:
            nf = plan.numeric_filter
            if fin:
                steps.append(
                    f"financial filter applied: metric={nf.metric_code} "
                    f"operator={nf.operator.value} window={nf.fiscal_years}FY "
                    f"period_rule={nf.period_rule} -> value={fin['value']} "
                    f"passed={fin['passed']}"
                )
            else:
                steps.append(
                    f"financial filter requested: metric={nf.metric_code} "
                    "operator=" + nf.operator.value
                )
        steps.append(f"claims loaded: {len(claims)} (ACCEPTED, "
                     "as_of/known_at applied)")
        steps.append(f"evidence attached: {len(evidence_ids)}")
        return steps

    @staticmethod
    def _report_period(fin: dict[str, Any] | None) -> str | None:
        """实际报告期：窗口 FY 范围（单 FY 时即该期）。"""
        if not fin or not fin.get("detail"):
            return None
        detail = fin["detail"]
        if len(detail) == 1:
            return detail[0]["period_end"]
        return f"{detail[-1]['period_end']}..{detail[0]['period_end']}"

    @staticmethod
    def _freshness(candidate_source: str) -> str:
        return {
            "graph": "graph+pg",
            "pg-stale": "pg-only (graph stale)",
            "pg-unavailable": "pg-only (graph unavailable)",
            "pg": "pg",
        }.get(candidate_source, "pg")
