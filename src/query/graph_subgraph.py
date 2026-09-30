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


def _pg_fallback_for_company(
    conn: Any, company_id: str, limit: int = 50,
) -> list[dict[str, Any]] | None:
    """PG 事实回退：该公司 ACCEPTED Claim 摘要列表（图谱不可信时的真相源）。

    公司不存在 → None（与"存在但图无数据"区分）；存在 → Claim 列表
    （可能为空——公司确实无关系时回退也为空列表，但 status 由调用方
    按 PG 有无该公司决定）。
    """
    exists = conn.execute(
        "SELECT 1 FROM master.company WHERE id = %s", (company_id,)
    ).fetchone()
    if exists is None:
        return None
    rows = conn.execute(
        """
        SELECT c.id, c.predicate_code, c.business_stage, c.claim_status
        FROM fact.claim c
        WHERE c.subject_entity_id = %s AND c.claim_status = 'ACCEPTED'
        ORDER BY c.recorded_at DESC
        LIMIT %s
        """,
        (company_id, limit),
    ).fetchall()
    cols = ["id", "predicate_code", "business_stage", "claim_status"]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def build_subgraph(
    executor: Any,
    *,
    company_id: str | None,
    hops: int,
    max_nodes: int,
    pg_conn: Any = None,
) -> dict[str, Any]:
    """构建公司中心子图。

    降级矩阵（issue #32 GWT-2）：
    - 图不可用（executor None/异常）→ DEGRADED + 原因 + pg_fallback；
    - 图可用但结果为空 且 PG 中公司存在（或 PG 有该公司 Claim）→
      STALE：投影水位落后，绝不静默当"权威空"返回（复用 compiler.py
      的 pg-stale 先例语义）；
    - PG 中公司不存在 → SUCCEEDED 空（权威空——不猜测）。
    """
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
            # 去重键含端点信息：无 claim_id 的边（如 TAGGED_AS 成员）不因
            # 同 type 塌缩为一条——每条路径实例独立保留
            key = (
                f"{edge.get('type')}:{edge.get('claim_id')}"
                f":{edge.get('source_id', '')}:{edge.get('target_id', '')}"
                f":{len(edges)}"
            )
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "type": edge.get("type"),
                    "claim_id": edge.get("claim_id"),
                })

    # GWT-2 水位落后：图查询成功但零路径，而 PG 事实层该公司存在/有
    # ACCEPTED Claim → 投影落后，STALE + PG 事实回退（绝不静默空）
    if not rows and pg_conn is not None:
        fallback = _pg_fallback_for_company(pg_conn, company_id)
        if fallback is not None:
            has_graph_center = False
            pg_has_claims = len(fallback) > 0
            if pg_has_claims or not has_graph_center:
                return {
                    "status": "STALE",
                    "reason": (
                        "graph projection returned no paths while PG holds "
                        f"{len(fallback)} accepted claim(s) for this company "
                        "- projection lagging; PG facts attached"
                    ),
                    "center": None,
                    "nodes": [], "edges": [],
                    "pg_fallback": fallback,
                    "hops": hops_int, "max_nodes": max_nodes,
                    "truncated": False,
                }

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
