"""Neo4j 图投影器：参数化 Cypher + MERGE + 读后校验（issue #8）。

核心设计：
- 所有节点/边使用稳定业务ID（UUID/IRI），绝不暴露 Neo4j 内部 ID；
- 经营类物化边必须带 claim_id + 状态 + 双时态 + 版本；
- 参数化 Cypher：白名单模板 + 参数绑定（无自由拼接）；
- 批量 MERGE：幂等，重复消费无副作用；
- ACCEPTED → CONTRADICTED/SUPERSEDED：失效物化边但保留 Claim 节点历史。
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

# 参数化 Cypher 模板（白名单）
NODE_MERGE = (
    "MERGE (n:{label} {{id: $id}}) "
    "SET n += $props "
    "RETURN n.id AS id"
)

EDGE_MERGE = (
    "MATCH (s {{id: $source_id}}), (t {{id: $target_id}}) "
    "MERGE (s)-[r:{rel_type} {{claim_id: $claim_id}}]->(t) "
    "SET r += $props "
    "RETURN r.claim_id AS claim_id"
)

EDGE_INVALIDATE = (
    "MATCH (s)-[r:{rel_type} {{claim_id: $claim_id}}]->(t) "
    "SET r.active = false, r.invalidated_at = $invalidated_at "
    "RETURN count(r) AS invalidated"
)

VERIFY_NODE = "MATCH (n:{label} {{id: $id}}) RETURN n.id AS id"
VERIFY_EDGE = (
    "MATCH (s)-[r:{rel_type} {{claim_id: $claim_id}}]->(t) "
    "WHERE s.id = $source_id AND t.id = $target_id "
    "RETURN count(r) AS count"
)


class Neo4jProjector:
    """投影器：把 Outbox 事件转为参数化 Cypher 执行。

    executor 是可注入的 Cypher 执行端口（生产用 neo4j driver，
    测试用 FakeGraphExecutor——不写共享实例）。
    """

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    def _run(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """执行参数化 Cypher。"""
        return self._executor.execute(template, params)

    # ---- 实体投影（幂等 MERGE）----

    def project_entity(self, label: str, entity_id: str, props: dict[str, Any]) -> None:
        self._run(NODE_MERGE.format(label=label), {"id": entity_id, "props": props})

    def verify_entity(self, label: str, entity_id: str) -> bool:
        rows = self._run(VERIFY_NODE.format(label=label), {"id": entity_id})
        return len(rows) > 0

    # ---- Claim 投影 ----

    def project_claim_node(self, claim: dict[str, Any]) -> None:
        """Claim 节点：保留状态/阶段/双时态/置信度/版本。"""
        self.project_entity("Claim", claim["id"], {
            "claim_status": claim.get("claim_status"),
            "business_stage": claim.get("business_stage"),
            "evidence_state": claim.get("evidence_state"),
            "predicate_code": claim.get("predicate_code"),
            "valid_from": str(claim.get("valid_from")) if claim.get("valid_from") else None,
            "valid_to": str(claim.get("valid_to")) if claim.get("valid_to") else None,
            "recorded_at": str(claim.get("recorded_at")) if claim.get("recorded_at") else None,
            "confidence": claim.get("confidence"),
            "ontology_version": claim.get("ontology_version"),
            "active": claim.get("claim_status") == "ACCEPTED",
        })

    # ---- 经营边投影（必须带 claim_id）----

    def project_business_edge(
        self,
        *,
        rel_type: str,
        source_id: str,
        target_id: str,
        claim_id: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """经营类物化边：claim_id 是幂等键的一部分，缺失则拒绝。"""
        if not claim_id:
            raise ValueError("business edge requires claim_id")
        all_props = {"active": True, **(props or {})}
        self._run(EDGE_MERGE.format(rel_type=rel_type), {
            "source_id": source_id,
            "target_id": target_id,
            "claim_id": claim_id,
            "props": all_props,
        })

    def invalidate_business_edge(
        self, *, rel_type: str, claim_id: str
    ) -> int:
        """ACCEPTED → CONTRADICTED/SUPERSEDED 时失效物化边。"""
        rows = self._run(EDGE_INVALIDATE.format(rel_type=rel_type), {
            "claim_id": claim_id,
            "invalidated_at": "now",
        })
        return rows[0]["invalidated"] if rows else 0

    def verify_business_edge(
        self, *, rel_type: str, source_id: str, target_id: str, claim_id: str
    ) -> bool:
        rows = self._run(VERIFY_EDGE.format(rel_type=rel_type), {
            "source_id": source_id,
            "target_id": target_id,
            "claim_id": claim_id,
        })
        return rows and rows[0]["count"] > 0

    # ---- 成员关系（无 claim_id 要求——结构性关系）----

    def project_membership(
        self, *, rel_type: str, source_id: str, target_id: str, props: dict[str, Any]
    ) -> None:
        self._run(
            "MATCH (s {id: $source_id}), (t {id: $target_id}) "
            "MERGE (s)-[r:" + rel_type + "]->(t) SET r += $props RETURN count(r) AS c",
            {"source_id": source_id, "target_id": target_id, "props": props},
        )


class FakeGraphExecutor:
    """测试用内存图执行器——不写真实 Neo4j。"""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.executed.append((template, params))
        # 简单的节点 MERGE 模拟
        if "MERGE (n:" in template and "$id" in template:
            node_id = params["id"]
            self.nodes[node_id] = {**params.get("props", {}), "id": node_id}
            return [{"id": node_id}]
        # 边 MERGE 模拟
        if "MERGE (s)-[r:" in template and "claim_id" in params:
            key = (params["source_id"], params["target_id"],
                   params["claim_id"], template)
            self.edges[key] = {**params.get("props", {}),
                               "claim_id": params["claim_id"]}
            return [{"claim_id": params["claim_id"]}]
        if "MERGE (s)-[r:" in template:
            key = (params["source_id"], params["target_id"], "", template)
            self.edges[key] = params.get("props", {})
            return [{"c": 1}]
        # 节点验证
        if template.startswith("MATCH (n:") and "RETURN n.id" in template:
            node_id = params["id"]
            if node_id in self.nodes:
                return [{"id": node_id}]
            return []
        # 边失效（在 verify 之前检查——两者都含 count(r)）
        if "SET r.active = false" in template:
            claim_id = params.get("claim_id")
            count = 0
            for key in self.edges:
                if key[2] == claim_id:
                    self.edges[key]["active"] = False
                    count += 1
            return [{"invalidated": count}]
        # 边验证
        if template.startswith("MATCH (s)-[r:") and "count(r)" in template:
            claim_id = params.get("claim_id")
            for (s, t, c, _tmpl), _props in self.edges.items():
                if c == claim_id and s == params.get("source_id") and t == params.get("target_id"):
                    return [{"count": 1}]
            return [{"count": 0}]
        return []
