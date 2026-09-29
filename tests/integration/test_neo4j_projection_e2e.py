"""Neo4j 真实服务集成测试（issue #10 验收 3：CI 隔离服务启动并清理）。

本地无 Neo4j 时自动跳过；CI 的 db job 以 service 容器启动 Neo4j 5.26
并注入 NEO4J_TEST_URI 后必然执行。测试前后清空图库（一次性容器，
允许全库清理），确保运行可重复。
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
    """neo4j driver → projector executor 端口适配（白名单校验在投影器内）。"""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run(template, params)
            return [dict(record) for record in result]


@pytest.fixture(scope="module")
def neo4j_env():
    if not _NEO4J_URI or not _NEO4J_PASSWORD:
        pytest.skip(
            "NEO4J_TEST_URI/NEO4J_TEST_PASSWORD not set; real-Neo4j e2e "
            "is CI-only"
        )
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        _NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASSWORD),
        connection_timeout=10,
    )
    driver.verify_connectivity()
    executor = DriverExecutor(driver)
    # 命名空间清理：一次性 service 容器，测试前后全库清空保证可重复
    executor.execute("MATCH (n) DETACH DELETE n", {})
    yield executor
    executor.execute("MATCH (n) DETACH DELETE n", {})
    driver.close()


def test_entity_projection_idempotent(neo4j_env) -> None:
    """实体 MERGE 幂等：重复投影不产生重复节点，props 覆盖更新。"""
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(neo4j_env)
    company_id = f"company-{uuid.uuid4()}"
    projector.project_entity("Company", company_id, {
        "canonical_name": "图投影公司", "status": "ACTIVE",
    })
    assert projector.verify_entity("Company", company_id)
    # 幂等重放（props 变更 → 覆盖）
    projector.project_entity("Company", company_id, {
        "canonical_name": "图投影公司", "status": "RETIRED",
    })
    rows = neo4j_env.execute(
        "MATCH (n:Company {id: $id}) RETURN count(n) AS c, "
        "collect(n.status)[0] AS status",
        {"id": company_id},
    )
    assert rows[0]["c"] == 1
    assert rows[0]["status"] == "RETIRED"


def test_business_edge_lifecycle(neo4j_env) -> None:
    """经营边全生命周期：投影 → 幂等重放 → 失效 → 验证。"""
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(neo4j_env)
    company_id = f"company-{uuid.uuid4()}"
    product_id = f"product-{uuid.uuid4()}"
    claim_id = f"claim-{uuid.uuid4()}"
    projector.project_entity("Company", company_id, {"canonical_name": "边公司"})
    projector.project_entity("Product", product_id, {"canonical_name": "边产品"})

    projector.project_business_edge(
        rel_type="PRODUCES", source_id=company_id, target_id=product_id,
        claim_id=claim_id,
        props={"business_stage": "MASS_PRODUCTION", "claim_status": "ACCEPTED"},
    )
    assert projector.verify_business_edge(
        rel_type="PRODUCES", source_id=company_id, target_id=product_id,
        claim_id=claim_id,
    )
    # 幂等重放：同 (source, target, claim_id) 不产生第二条边
    projector.project_business_edge(
        rel_type="PRODUCES", source_id=company_id, target_id=product_id,
        claim_id=claim_id,
        props={"business_stage": "MASS_PRODUCTION", "claim_status": "ACCEPTED"},
    )
    rows = neo4j_env.execute(
        "MATCH (:Company {id: $s})-[r:PRODUCES]->(:Product {id: $t}) "
        "RETURN count(r) AS c",
        {"s": company_id, "t": product_id},
    )
    assert rows[0]["c"] == 1

    # 失效：ACCEPTED → CONTRADICTED 物化边保留历史但 active=false
    invalidated = projector.invalidate_business_edge(
        rel_type="PRODUCES", claim_id=claim_id,
    )
    assert invalidated == 1
    rows = neo4j_env.execute(
        "MATCH (:Company {id: $s})-[r:PRODUCES]->(:Product {id: $t}) "
        "RETURN r.active AS active, r.invalidated_at IS NOT NULL AS marked",
        {"s": company_id, "t": product_id},
    )
    assert rows[0]["active"] is False
    assert rows[0]["marked"] is True


def test_claim_node_stale_rejection(neo4j_env) -> None:
    """乱序守卫：旧 recorded_at 事件被拒绝覆盖新版本图状态。"""
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(neo4j_env)
    claim_id = f"claim-{uuid.uuid4()}"
    fresh = {
        "id": claim_id, "claim_status": "ACCEPTED",
        "business_stage": "MASS_PRODUCTION", "predicate_code": "PRODUCES",
        "recorded_at": "2026-06-01T00:00:00+00:00",
        "confidence": 0.9, "ontology_version": "0.1.0",
    }
    assert projector.project_claim_node(fresh) is True
    stale = {**fresh, "claim_status": "NEEDS_REVIEW",
             "recorded_at": "2026-01-01T00:00:00+00:00"}
    assert projector.project_claim_node(stale) is False  # 旧事件被拒
    rows = neo4j_env.execute(
        "MATCH (n:Claim {id: $id}) RETURN n.claim_status AS status, "
        "n.recorded_at AS rat",
        {"id": claim_id},
    )
    assert rows[0]["status"] == "ACCEPTED"  # 保留新版本状态
    assert rows[0]["rat"] == "2026-06-01T00:00:00+00:00"


def test_membership_projection_and_injection_guard(neo4j_env) -> None:
    """成员关系投影 + label/rel_type 白名单拒绝任意 Cypher 注入。"""
    from src.projection.projector import Neo4jProjector

    projector = Neo4jProjector(neo4j_env)
    company_id = f"company-{uuid.uuid4()}"
    industry_id = f"industry-{uuid.uuid4()}"
    projector.project_entity("Company", company_id, {"canonical_name": "成员公司"})
    projector.project_entity("Industry", industry_id, {"name": "机器人制造"})

    projector.project_membership(
        rel_type="BELONGS_TO_INDUSTRY", source_id=company_id,
        target_id=industry_id, props={"snapshot_date": "2026-09-01"},
    )
    rows = neo4j_env.execute(
        "MATCH (:Company {id: $s})-[r:BELONGS_TO_INDUSTRY]->"
        "(:Industry {id: $t}) RETURN r.snapshot_date AS snap",
        {"s": company_id, "t": industry_id},
    )
    assert rows[0]["snap"] == "2026-09-01"

    # 白名单：非白名单 label/rel_type 拒绝（即使模板拼接也不可注入）
    with pytest.raises(ValueError, match="not whitelisted"):
        projector.project_entity("EvilLabel", "x", {})
    with pytest.raises(ValueError, match="not whitelisted"):
        projector.project_business_edge(
            rel_type="EVIL_REL", source_id="a", target_id="b", claim_id="c",
        )
    # business edge 缺 claim_id 拒绝
    with pytest.raises(ValueError, match="claim_id"):
        projector.project_business_edge(
            rel_type="PRODUCES", source_id="a", target_id="b", claim_id="",
        )
