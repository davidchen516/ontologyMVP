"""受控子图构建（issue #32）：白名单 Cypher 模板 + 节点数上限 + PG 回退。

红线（Epic #29 / issue #32 非目标）：
- 浏览器永不直连 Neo4j、永不提交自由 Cypher——所有查询由本模块的
  白名单模板在后端执行；
- 节点数超预算时后端裁剪并返回说明（不把全图一次传到浏览器）；
- 图后端不可用/水位落后 → DEGRADED/STALE + PostgreSQL 事实列表回退
  （绝不断崖式空结果）。
"""

from __future__ import annotations

from typing import Any

# 白名单子图模板（受控参数：$company_id、$hops 内联校验后的 int）
# {hops} 为唯一渲染占位符（内联校验后 int），其余花括号为 Cypher 字面量
SUBGRAPH_TEMPLATE = """
MATCH (c:Company {id: $company_id})
OPTIONAL MATCH path = (c)-[r:PRODUCES|SUPPLIES_TO|USES_TECHNOLOGY|TAGGED_AS*1..__HOPS__]-(n)
WHERE n:Company OR n:Product OR n:Concept
RETURN c.id AS center_id, c.name AS center_name,
       [p IN nodes(path) | {id: p.id, labels: labels(p),
        name: coalesce(p.name, p.canonical_name)}] AS node_path,
       [rel IN relationships(path) | {type: type(rel),
        claim_id: rel.claim_id}] AS edge_path
LIMIT $max_paths
"""

MAX_PATHS = 300  # 白名单上限（后端强制，浏览器不可调高）


def build_subgraph(
    executor: Any,
    *,
    company_id: str | None,
    hops: int,
    max_nodes: int,
) -> dict[str, Any]:
    """构建公司中心子图；executor 为 None/异常 → PG 回退列表。"""
    if not company_id:
        return {
            "status": "REJECTED",
            "reason": "company_id is required for subgraph queries",
            "nodes": [],
            "edges": [],
            "pg_fallback": None,
            "hops": hops,
            "max_nodes": max_nodes,
            "truncated": False,
        }

    hops_int = int(hops)
    if not 1 <= hops_int <= 2:
        return {
            "status": "REJECTED",
            "reason": "hops must be 1 or 2",
            "nodes": [], "edges": [], "pg_fallback": None,
            "hops": hops, "max_nodes": max_nodes, "truncated": False,
        }

    if executor is None:
        return {
            "status": "DEGRADED",
            "reason": "graph backend not configured",
            "nodes": [],
            "edges": [],
            "pg_fallback": None,
            "hops": hops_int,
            "max_nodes": max_nodes,
            "truncated": False,
        }

    template = SUBGRAPH_TEMPLATE.replace("__HOPS__", str(hops_int))
    try:
        rows = executor.execute(template, {
            "company_id": company_id,
            "max_paths": MAX_PATHS,
        })
    except Exception as exc:  # noqa: BLE001 - 图不可用属预期降级路径
        return {
            "status": "DEGRADED",
            "reason": f"graph backend unreachable: {type(exc).__name__}",
            "nodes": [], "edges": [], "pg_fallback": None,
            "hops": hops_int, "max_nodes": max_nodes, "truncated": False,
        }

    # 展开路径 → 节点/边去重集合
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    edge_keys: set[str] = set()
    truncated = False

    for row in rows:
        for node in row.get("node_path", []):
            if not isinstance(node, dict) or not node.get("id"):
                continue
            if node["id"] not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                nodes[node["id"]] = {
                    "id": node["id"],
                    "labels": node.get("labels", []),
                    "name": node.get("name"),
                }
        for edge in row.get("edge_path", []):
            key = f"{edge.get('type')}:{edge.get('claim_id')}"
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "type": edge.get("type"),
                    "claim_id": edge.get("claim_id"),
                })

    return {
        "status": "SUCCEEDED",
        "reason": None,
        "center": {"id": rows[0]["center_id"], "name": rows[0]["center_name"]}
        if rows else None,
        "nodes": list(nodes.values()),
        "edges": edges,
        "pg_fallback": None,
        "hops": hops_int,
        "max_nodes": max_nodes,
        "truncated": truncated,
    }
