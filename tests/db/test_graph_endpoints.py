"""issue #32 新端点测试：受控子图 + Claim lineage + 文档证据。"""

from __future__ import annotations

import uuid

import psycopg

from tests.db.test_query_api import make_client, seed_claim_with_evidence


class StubGraphExecutor:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.executed: list[tuple[str, dict]] = []

    def execute(self, template, params):
        self.executed.append((template, params))
        if self.error:
            raise self.error
        return self.rows


def _stub_rows():
    return [{
        "center_id": "c-1",
        "center_name": "中心公司",
        "node_path": [
            {"id": "c-1", "labels": ["Company"], "name": "中心公司"},
            {"id": "p-1", "labels": ["Product"], "name": "谐波减速器"},
        ],
        "edge_path": [{"type": "PRODUCES", "claim_id": "cl-1"}],
    }]


def test_subgraph_succeeded(uow_factory, main_dsn) -> None:
    seed_claim_with_evidence(uow_factory)
    client = make_client(main_dsn)
    stub = StubGraphExecutor(_stub_rows())
    client.app.state.query_graph_executor = stub

    r = client.get("/api/v1/graph/subgraph", params={"company_id": "c-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "SUCCEEDED"
    assert len(body["nodes"]) == 2
    assert body["edges"] == [{"type": "PRODUCES", "claim_id": "cl-1"}]
    assert body["center"]["name"] == "中心公司"
    # 白名单模板被执行（受控 Cypher——非自由查询）
    template, params = stub.executed[0]
    assert "MATCH (c:Company" in template
    assert "*1..1" in template
    assert params["company_id"] == "c-1"


def test_subgraph_degraded_without_executor(uow_factory, main_dsn) -> None:
    """GWT：图后端不可用 → DEGRADED + 原因（不静默空）。"""
    client = make_client(main_dsn)  # executor 已被 make_client 置 None
    r = client.get("/api/v1/graph/subgraph", params={"company_id": "c-1"})
    body = r.json()
    assert body["status"] == "DEGRADED"
    assert "not configured" in body["reason"]


def test_subgraph_unreachable_graph(uow_factory, main_dsn) -> None:
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(
        error=RuntimeError("connection refused")
    )
    r = client.get("/api/v1/graph/subgraph", params={"company_id": "c-1"})
    body = r.json()
    assert body["status"] == "DEGRADED"
    assert "RuntimeError" in body["reason"]


def test_subgraph_rejects_missing_company_and_bad_hops(main_dsn) -> None:
    client = make_client(main_dsn)
    # 缺 company_id → REJECTED（不猜测）
    body = client.get("/api/v1/graph/subgraph").json()
    assert body["status"] == "REJECTED"
    # hops 越界 → FastAPI 422
    assert client.get(
        "/api/v1/graph/subgraph",
        params={"company_id": "x", "hops": 5},
    ).status_code == 422


def test_subgraph_truncation_respects_max_nodes(uow_factory, main_dsn) -> None:
    """GWT：节点超预算 → 裁剪 + truncated 说明（不冻结浏览器）。"""
    many = [{
        "center_id": "c-1", "center_name": "中心",
        "node_path": [
            {"id": f"n-{i}", "labels": ["Product"], "name": f"产品{i}"}
            for i in range(50)
        ],
        "edge_path": [],
    }]
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor(many)

    body = client.get(
        "/api/v1/graph/subgraph",
        params={"company_id": "c-1", "max_nodes": 10},
    ).json()
    assert len(body["nodes"]) == 10
    assert body["truncated"] is True


def test_claim_lineage_full_chain(uow_factory, main_dsn) -> None:
    """GWT：经营边 → claim_id → Claim → Evidence → Document 追溯链。"""
    seeded = seed_claim_with_evidence(uow_factory)
    # seed 默认无页码——补定位信息（lineage 展示页码原文）
    with psycopg.connect(main_dsn) as conn:
        conn.execute(
            "UPDATE fact.evidence_fragment SET page_number = 5, "
            "char_start = 0, char_end = 6 WHERE id = %s",
            (str(seeded["evidence"]["id"]),),
        )
        conn.commit()
    client = make_client(main_dsn)

    r = client.get(f"/api/v1/claims/{seeded['claim']['id']}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["claim"]["predicate_code"] == "PRODUCES"
    assert body["claim"]["claim_status"] == "ACCEPTED"
    assert len(body["evidence"]) == 1
    assert "量产已实现" in body["evidence"][0]["quote_text"]
    assert body["evidence"][0]["page_number"] is not None
    assert body["documents"][0] is not None
    assert body["documents"][0]["source_system"] == "CNINFO"
    # 版本缺失状态如实呈现（seed 未建版本 → None，不伪造）
    assert body["documents"][0]["version"] is None
    # 404
    assert client.get(
        f"/api/v1/claims/{uuid.uuid4()}/lineage"
    ).status_code == 404


def test_document_evidence_with_fragments(uow_factory, main_dsn) -> None:
    seeded = seed_claim_with_evidence(uow_factory)
    with psycopg.connect(main_dsn) as conn:
        conn.execute(
            "UPDATE fact.evidence_fragment SET page_number = 5, "
            "char_start = 0, char_end = 6 WHERE id = %s",
            (str(seeded["evidence"]["id"]),),
        )
        conn.commit()
        doc_id = conn.execute(
            "SELECT document_id FROM fact.evidence_fragment WHERE id = %s",
            (str(seeded["evidence"]["id"]),),
        ).fetchone()[0]
    client = make_client(main_dsn)

    r = client.get(f"/api/v1/documents/{doc_id}/evidence")
    assert r.status_code == 200
    body = r.json()
    assert body["document"]["title"] == "年报"
    assert body["count"] >= 1
    assert body["fragments"][0]["quote_text"]
    assert "char_start" in body["fragments"][0]
    # 404
    assert client.get(
        f"/api/v1/documents/{uuid.uuid4()}/evidence"
    ).status_code == 404


def test_graph_endpoints_read_only(main_dsn) -> None:
    """图谱/lineage 端点不产生事实层写入。"""
    from src.query.api import _connect_read_only

    with _connect_read_only(main_dsn) as conn:
        import pytest

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM fact.claim")


def test_lineage_non_uuid_422(uow_factory, main_dsn) -> None:
    """M2 回归：非 UUID 路径参数 → 422（而非 500）。"""
    client = make_client(main_dsn)
    assert client.get("/api/v1/claims/abc/lineage").status_code == 422
    assert client.get("/api/v1/documents/abc/evidence").status_code == 422


def test_subgraph_stale_when_graph_empty_but_pg_has_claims(
    uow_factory, main_dsn
) -> None:
    """B2 回归（GWT-2）：图返回 0 路径但 PG 有 ACCEPTED Claim → STALE + 回退。

    绝不静默当"权威空"——投影水位落后必须可见并附带 PG 事实。
    """
    seeded = seed_claim_with_evidence(uow_factory, company_name="水位公司")
    client = make_client(main_dsn)
    # 图执行器健康但返回空（模拟投影未跟上 PG 新写入）
    client.app.state.query_graph_executor = StubGraphExecutor([])

    r = client.get(
        "/api/v1/graph/subgraph",
        params={"company_id": str(seeded["company"]["id"])},
    )
    body = r.json()
    assert body["status"] == "STALE"
    assert "projection lagging" in body["reason"]
    assert body["pg_fallback"] is not None
    assert len(body["pg_fallback"]) >= 1
    assert body["pg_fallback"][0]["predicate_code"] == "PRODUCES"


def test_subgraph_authoritative_empty_for_unknown_company(
    uow_factory, main_dsn
) -> None:
    """PG 中不存在的公司 + 图空 → SUCCEEDED 空（权威空，不猜测 STALE）。"""
    client = make_client(main_dsn)
    client.app.state.query_graph_executor = StubGraphExecutor([])
    r = client.get(
        "/api/v1/graph/subgraph",
        params={"company_id": str(uuid.uuid4())},
    )
    assert r.json()["status"] == "SUCCEEDED"
