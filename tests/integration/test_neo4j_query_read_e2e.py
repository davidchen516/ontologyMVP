"""真实 Neo4j 上的查询读路径端到端（issue #10 验收 8：路径正确性）。

修复审查 B1：此前 src/query/graph_executor.py 的 Cypher 读模板从未对
真实 Neo4j 执行过（写侧投影有 e2e，读侧只有 stub）——若模板对
Neo4j 5.26 语法错误，CI 依然全绿。本文件把完整查询链路对真库跑通：
真实投影写 → Neo4jQueryExecutor 白名单执行 → GraphCompiler 编译 →
QueryOrchestrator EXPLAIN_RELATION → Grounded 路径断言。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "")
_NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
_NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "")


class DriverExecutor:
    """neo4j driver → executor 端口适配（与 e2e 写侧共用模式）。"""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run(template, params)
            return [dict(record) for record in result]


@pytest.fixture(scope="module")
def query_neo4j():
    if not _NEO4J_URI or not _NEO4J_PASSWORD:
        pytest.skip(
            "NEO4J_TEST_URI/NEO4J_TEST_PASSWORD not set; read-path e2e "
            "is CI-only"
        )
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        _NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASSWORD),
        connection_timeout=10,
    )
    driver.verify_connectivity()
    executor = DriverExecutor(driver)
    executor.execute("MATCH (n) DETACH DELETE n", {})
    yield executor
    executor.execute("MATCH (n) DETACH DELETE n", {})
    driver.close()


def _seed_path_graph(executor: Any) -> tuple[str, str]:
    """投影：甲公司 -PRODUCES-> 减速器产品 <-SUPPLIES_TO- 乙公司。"""
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(executor)
    # 图节点 id 与 PG 实体对齐为 UUID（EntityRef 校验 UUID 格式）
    company_a = str(uuid.uuid4())
    product = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    projector.project_entity("Company", company_a, {"canonical_name": "甲公司"})
    projector.project_entity("Product", product, {"canonical_name": "减速器产品"})
    projector.project_entity("Company", company_b, {"canonical_name": "乙公司"})
    projector.project_business_edge(
        rel_type="PRODUCES", source_id=company_a, target_id=product,
        claim_id=f"claim-{uuid.uuid4()}", props={"business_stage": "MASS_PRODUCTION"},
    )
    projector.project_business_edge(
        rel_type="SUPPLIES_TO", source_id=company_b, target_id=product,
        claim_id=f"claim-{uuid.uuid4()}", props={"business_stage": "MASS_PRODUCTION"},
    )
    return company_a, company_b


def test_explain_relation_reads_real_graph(query_neo4j) -> None:
    """EXPLAIN_RELATION 全链路（真库）：编译 → 执行 → 路径解释。"""
    from src.query.compiler import GraphCompiler, QueryOrchestrator
    from src.query.graph_executor import Neo4jQueryExecutor
    from src.query.models import (
        EntityRef,
        QueryIntent,
        QueryPlan,
        SemanticFilter,
    )

    company_a, company_b = _seed_path_graph(query_neo4j)
    executor = Neo4jQueryExecutor.__new__(Neo4jQueryExecutor)
    executor._driver = query_neo4j._driver  # noqa: SLF001  直接绑定测试驱动

    plan = QueryPlan(
        intent=QueryIntent.EXPLAIN_RELATION,
        subject=EntityRef(entity_type="Company", entity_id=company_a),
        object_entity=EntityRef(entity_type="Company", entity_id=company_b),
        semantic_filter=SemanticFilter(max_hops=2),
    )
    compiled = GraphCompiler.compile(plan)
    assert compiled is not None
    template, params = compiled
    # 模板是白名单渲染产物（max_hops 已内联），真实驱动可解析
    raw = executor.execute(template, params)
    assert raw, "no path found in real Neo4j"
    assert "甲公司" in raw[0]["path_nodes"]
    assert "乙公司" in raw[0]["path_nodes"]
    assert set(raw[0]["path_rels"]) == {"PRODUCES", "SUPPLIES_TO"}
    assert len(raw[0]["path_rels"]) == 2  # max_hops=2 内恰好两跳

    orchestrator = QueryOrchestrator(graph_executor=executor)
    # PG 侧公司名加载用最小存根（本测试焦点是图读路径的真实执行；
    # Grounded 加载的 PG 行为由 tests/db stub 套件覆盖）
    class _PgStub:
        def execute(self, _sql: str, params: tuple) -> Any:
            company_id = str(params[0])
            name = (
                "甲公司" if company_id == company_a
                else "乙公司" if company_id == company_b else None
            )

            class _Cur:
                def fetchone(self) -> tuple | None:
                    return (name,) if name else None

                def fetchall(self) -> list:
                    return []

            return _Cur()

    response = orchestrator.execute(_PgStub(), plan, trace_id="read-path-e2e")
    assert response.status.value in ("SUCCEEDED", "DEGRADED")
    assert not response.degraded, f"graph is reachable: {response.degradation_notes}"
    assert len(response.results) == 2  # 两端公司都返回 Grounded 证据包
    path_steps = [s for s in response.results[0].reasoning_path
                  if s.startswith("graph path:")]
    assert path_steps, "reasoning path must describe the graph path"
    assert "甲公司" in path_steps[0] and "乙公司" in path_steps[0]
    assert "(2 hops)" in path_steps[0]


def test_concept_template_reads_real_graph(query_neo4j) -> None:
    """companies_by_concept 模板在真库可执行（参数化 Cypher 语法验证）。"""
    from src.projection.projector import Neo4jProjector
    from src.query.compiler import GRAPH_TEMPLATES
    from src.query.graph_executor import Neo4jQueryExecutor

    projector = Neo4jProjector(query_neo4j)
    concept_id = str(uuid.uuid4())
    company_id = str(uuid.uuid4())
    projector.project_entity("Concept", concept_id, {"canonical_name": "人形机器人"})
    projector.project_entity("Company", company_id, {"canonical_name": "概念公司"})
    projector.project_membership(
        rel_type="TAGGED_AS", source_id=company_id, target_id=concept_id,
        props={"snapshot_date": "2026-09-01"},
    )

    executor = Neo4jQueryExecutor.__new__(Neo4jQueryExecutor)
    executor._driver = query_neo4j._driver  # noqa: SLF001
    rows = executor.execute(
        GRAPH_TEMPLATES["companies_by_concept"],
        {"concept_name": "人形机器人"},
    )
    assert company_id in [str(r["company_id"]) for r in rows]


def test_query_executor_rejects_non_whitelisted_cypher(query_neo4j) -> None:
    """图执行器白名单：任意 Cypher 被拒，不触达驱动。"""
    from src.query.graph_executor import Neo4jQueryExecutor

    executor = Neo4jQueryExecutor.__new__(Neo4jQueryExecutor)
    executor._driver = query_neo4j._driver  # noqa: SLF001
    with pytest.raises(ValueError, match="not in whitelist"):
        executor.execute("MATCH (n) DETACH DELETE n RETURN n", {})
