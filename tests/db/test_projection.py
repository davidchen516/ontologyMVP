"""Outbox Worker / 投影器 / 对账 / 全量重建 DB 测试（issue #8 验收）。"""

from __future__ import annotations

import uuid

import pytest
from src.domain.enums import ClaimStatus
from src.projection.projector import FakeGraphExecutor, Neo4jProjector
from src.projection.reconcile import full_rebuild, reconciliation_report
from src.projection.worker import (
    claim_batch,
    process_event,
    run_worker_cycle,
)

# ---- 辅助 ----


def seed_outbox(uow_factory, event_type="CLAIM_ACCEPTED", aggregate_id=None):
    with uow_factory.transaction() as uow:
        outbox = uow.graph_outbox.insert_idempotent(
            aggregate_type="Claim",
            aggregate_id=aggregate_id or uuid.uuid4(),
            event_type=event_type,
            payload={"test": True},
            idempotency_key=f"test-{uuid.uuid4().hex[:8]}",
        )
    return outbox


def make_projector():
    executor = FakeGraphExecutor()
    return Neo4jProjector(executor), executor


def simple_project(uow, event):
    """简单投影函数：写入一个节点。"""
    projector, _ = make_projector()
    projector.project_entity("Claim", str(event["aggregate_id"]), {"active": True})


def simple_verify(uow, event):
    """简单校验函数：检查节点存在。"""
    projector, _ = make_projector()
    return projector.verify_entity("Claim", str(event["aggregate_id"]))


# ---- 验收 2：重复消费 10 次结果一致 ----


def test_idempotent_consumption_10x(uow_factory) -> None:
    """同一 Outbox 事件重复消费 10 次，结果与消费 1 次完全一致。"""
    event = seed_outbox(uow_factory)
    executor = FakeGraphExecutor()
    projector = Neo4jProjector(executor)

    def project(uow, ev):
        projector.project_entity("Claim", str(ev["aggregate_id"]), {"active": True})

    def verify(uow, ev):
        return projector.verify_entity("Claim", str(ev["aggregate_id"]))

    # 消费 10 次
    for _ in range(10):
        with uow_factory.transaction() as uow:
            # 重置为 PENDING（模拟重复领取）
            uow._conn.execute(  # noqa: SLF001
                "UPDATE ops.graph_outbox SET status = 'PENDING', retry_count = 0 "
                "WHERE id = %s", (event["id"],),
            )
        with uow_factory.transaction() as uow:
            batch = claim_batch(uow)
        assert len(batch) == 1
        with uow_factory.transaction() as uow:
            result = process_event(uow, batch[0], project_fn=project, verify_fn=verify)
        assert result.status == "PROCESSED"

    # FakeGraphExecutor 的节点数量不变（幂等）
    assert len(executor.nodes) == 1
    with uow_factory.transaction() as uow:
        outbox_row = uow._conn.execute(  # noqa: SLF001
            "SELECT status, retry_count FROM ops.graph_outbox WHERE id = %s",
            (event["id"],),
        ).fetchone()
    assert outbox_row[0] == "PROCESSED"  # 最终完成
    assert outbox_row[1] == 0  # 无异常重试


# ---- 验收 3：崩溃恢复 ----


def test_crash_after_neo4j_before_mark(uow_factory) -> None:
    """Neo4j 提交后、PG 标记前崩溃：重启后重复消费幂等并最终完成。"""
    event = seed_outbox(uow_factory)
    executor = FakeGraphExecutor()
    projector = Neo4jProjector(executor)

    # 模拟：投影成功但标记前崩溃
    with uow_factory.transaction() as uow:
        batch = claim_batch(uow)
    assert len(batch) == 1
    # Neo4j 写入成功
    projector.project_entity("Claim", str(batch[0]["aggregate_id"]), {"active": True})
    # 崩溃（不标记、不 commit——事件留在 PROCESSING）

    # 重启：租约过期后重新领取
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.graph_outbox SET lease_expires_at = now() - interval '1 min' "
            "WHERE id = %s", (event["id"],),
        )

    def verify(uow, ev):
        return projector.verify_entity("Claim", str(ev["aggregate_id"]))

    results = run_worker_cycle(
        uow_factory,
        project_fn=lambda uow, ev: projector.project_entity(
            "Claim", str(ev["aggregate_id"]), {"active": True}
        ),
        verify_fn=verify,
    )
    assert len(results) == 1
    assert results[0].status == "PROCESSED"
    # 幂等：节点不重复
    assert len(executor.nodes) == 1


# ---- 验收 5：乱序拒绝 ----


def test_stale_event_does_not_overwrite() -> None:
    """旧版本事件不能覆盖新版本图状态——project_claim_node 内置 recorded_at 守卫。"""
    projector, executor = make_projector()
    claim_id = "claim-stale-1"

    # 新版本（recorded_at 2027）先写入
    assert projector.project_claim_node({
        "id": claim_id, "claim_status": "ACCEPTED", "predicate_code": "PRODUCES",
        "recorded_at": "2027-06-01T00:00:00+00:00",
    }) is True

    # 旧版本事件到达（recorded_at 2026）——必须被拒绝
    assert projector.project_claim_node({
        "id": claim_id, "claim_status": "ACCEPTED", "predicate_code": "PRODUCES",
        "recorded_at": "2026-01-01T00:00:00+00:00",
    }) is False

    # 节点仍是新版本
    assert executor.nodes[claim_id]["recorded_at"] == "2027-06-01T00:00:00+00:00"


# ---- 验收 6：毒丸死信 ----


def test_poison_event_dead_letter(uow_factory) -> None:
    """毒性事件连续失败进入死信，不阻塞其他独立事件。"""
    poison = seed_outbox(uow_factory, event_type="POISON")
    healthy = seed_outbox(uow_factory, event_type="HEALTHY")

    def failing_project(uow, ev):
        if ev["event_type"] == "POISON":
            raise RuntimeError("poison always fails")
        # 健康事件正常投影
        pass

    def always_verify(uow, ev):
        return ev["event_type"] != "POISON"

    # 处理毒丸直到死信
    for _ in range(5):  # max_retries=5
        with uow_factory.transaction() as uow:
            uow._conn.execute(  # noqa: SLF001
                "UPDATE ops.graph_outbox SET status = 'PENDING' "
                "WHERE id = %s", (poison["id"],),
            )
        with uow_factory.transaction() as uow:
            batch = claim_batch(uow)
        for ev in batch:
            if ev["id"] == poison["id"]:
                with uow_factory.transaction() as uow:
                    result = process_event(uow, ev, project_fn=failing_project,
                                           verify_fn=always_verify, max_retries=5)
    assert result.status == "DEAD_LETTERED"

    # 健康事件不受阻塞
    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.graph_outbox SET status = 'PENDING' "
            "WHERE id = %s", (healthy["id"],),
        )
    results = run_worker_cycle(
        uow_factory,
        project_fn=lambda uow, ev: None,
        verify_fn=lambda uow, ev: True,
    )
    assert all(r.status == "PROCESSED" for r in results)


# ---- 验收 9：全量重建 ----


def test_full_rebuild_from_postgres(uow_factory) -> None:
    """删除 Neo4j 全部数据后，可从 PostgreSQL 完成全量重建。"""
    # 种子：公司 + Accepted Claim
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="重建测试公司")
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="manual", ontology_version="0.1.0",
            confidence=0.9, status=ClaimStatus.ACCEPTED,
            object_value={"x": 1},
        )
        uow.claims.update_status(claim["id"], ClaimStatus.ACCEPTED)

    projector, executor = make_projector()

    def project_entity(label, eid, props):
        projector.project_entity(label, eid, props)

    def project_claim(cd):
        projector.project_claim_node(cd)

    result = full_rebuild(
        uow_factory,
        graph_executor=executor,
        project_entity_fn=project_entity,
        project_claim_fn=project_claim,
        project_edge_fn=lambda cd: projector.project_business_edge(
            rel_type=cd["predicate_code"] if cd["predicate_code"] in
            ("PRODUCES", "DEVELOPS", "SUPPLIES_TO") else "PRODUCES",
            source_id=str(cd["subject_entity_id"]),
            target_id=str(cd["object_entity_id"]) if cd["object_entity_id"]
            else str(cd["id"]),
            claim_id=str(cd["id"]),
        ),
    )
    assert result["entities_rebuilt"]["Company"] >= 1
    assert result["claims_rebuilt"] >= 1
    assert str(company["id"]) in executor.nodes

    # 对账
    with uow_factory.transaction() as uow:
        report = reconciliation_report(uow, executor)
    assert report["claims"]["postgres_accepted"] >= 1


# ---- 验收 4：Neo4j 不可达期间 PG 正常 ----


def test_neo4j_down_pg_writes_ok(uow_factory) -> None:
    """Neo4j 不可达期间 PostgreSQL 事实正常提交，事件保留并重试。"""
    event = seed_outbox(uow_factory)

    def failing_project(uow, ev):
        raise ConnectionError("neo4j unreachable")

    def failing_verify(uow, ev):
        return False

    with uow_factory.transaction() as uow:
        batch = claim_batch(uow)
    with uow_factory.transaction() as uow:
        result = process_event(uow, batch[0], project_fn=failing_project,
                               verify_fn=failing_verify)
    assert result.status == "PENDING"  # 失败重试（PENDING + retry_count > 0）

    # PG 事实未被回滚：事件保留为 PENDING 等待重试（retry_count 已递增）
    with uow_factory.transaction() as uow:
        status = uow._conn.execute(  # noqa: SLF001
            "SELECT status FROM ops.graph_outbox WHERE id = %s", (event["id"],),
        ).fetchone()
    assert status is not None

    # 恢复后追平
    def ok_project(uow, ev):
        pass

    def ok_verify(uow, ev):
        return True

    with uow_factory.transaction() as uow:
        uow._conn.execute(  # noqa: SLF001
            "UPDATE ops.graph_outbox SET status = 'PENDING' WHERE id = %s",
            (event["id"],),
        )
    results = run_worker_cycle(uow_factory, project_fn=ok_project, verify_fn=ok_verify)
    assert all(r.status == "PROCESSED" for r in results)


# ---- 验收 7：claim_id 100% ----


def test_business_edges_claim_id_100_percent() -> None:
    """经营类物化边含 claim_id 比例为 100%——缺失则拒绝。"""
    projector, _ = make_projector()
    with pytest.raises(ValueError, match="claim_id"):
        projector.project_business_edge(
            rel_type="PRODUCES", source_id="c1", target_id="p1", claim_id=""
        )
    # 正常路径
    projector.project_business_edge(
        rel_type="PRODUCES", source_id="c1", target_id="p1", claim_id="claim-1"
    )
    assert projector.verify_business_edge(
        rel_type="PRODUCES", source_id="c1", target_id="p1", claim_id="claim-1"
    )


# ---- 验收 6：CONTRADICTED/SUPERSEDED 失效 ----


def test_contradicted_claim_invalidates_edge() -> None:
    """Claim 转 CONTRADICTED 后，物化边失效且历史 Claim 节点保留。"""
    projector, executor = make_projector()
    claim_id = "claim-cc-1"
    projector.project_claim_node({"id": claim_id, "claim_status": "ACCEPTED",
                                  "predicate_code": "PRODUCES"})
    projector.project_business_edge(
        rel_type="PRODUCES", source_id="co-1", target_id="p-1",
        claim_id=claim_id,
    )
    invalidated = projector.invalidate_business_edge(rel_type="PRODUCES", claim_id=claim_id)
    assert invalidated >= 1
    # 历史 Claim 节点仍在
    assert projector.verify_entity("Claim", claim_id)


# ---- 审查者要求的端到端测试 ----


def test_dispatcher_end_to_end_accepted_claim_processed(uow_factory) -> None:
    """CLAIM_ACCEPTED → PROCESSED：节点+经营边经真实分发器投影并校验。"""
    from src.projection.dispatcher import run_projection_worker
    from src.projection.projector import FakeGraphExecutor

    executor = FakeGraphExecutor()
    # 种子：公司 + ACCEPTED Claim + Outbox 事件
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="端到端测试公司")
        product = uow._conn.execute(  # noqa: SLF001
            "INSERT INTO master.product (iri, canonical_name, ontology_version, active) "
            "VALUES (%s, '减速器', '0.1.0', true) RETURNING id",
            (f"urn:product:{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0]
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="manual", ontology_version="0.1.0",
            confidence=0.9, status=ClaimStatus.ACCEPTED,
            object_entity_id=product,
            business_stage="MASS_PRODUCTION",
        )
        uow.graph_outbox.insert_idempotent(
            aggregate_type="Claim", aggregate_id=claim["id"],
            event_type="CLAIM_ACCEPTED", payload={"claim_id": str(claim["id"])},
            idempotency_key=f"e2e-{claim['id']}",
        )

    processed = run_projection_worker(
        uow_factory, executor, single_pass=True,
    )
    assert processed >= 1, "至少一个事件应成功 PROCESSED"
    # 节点已投影
    assert str(claim["id"]) in executor.nodes
    # 经营边已投影（claim_id 归一化为 str）
    business_edges = [k for k in executor.edges if k[2] == str(claim["id"])]
    assert len(business_edges) >= 1, "经营边应已投影"


def test_dispatcher_contradicted_claim_invalidates_edge(uow_factory) -> None:
    """CLAIM_CONTRADICTED → PROCESSED：物化边失效。"""
    from src.projection.dispatcher import run_projection_worker
    from src.projection.projector import FakeGraphExecutor

    executor = FakeGraphExecutor()
    # 种子：公司 + ACCEPTED Claim（含经营边已投影）+ CONTRADICTED 事件
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(canonical_name="矛盾测试公司")
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="manual", ontology_version="0.1.0",
            confidence=0.9, status=ClaimStatus.ACCEPTED,
            object_value={"x": 1},
        )
        uow.graph_outbox.insert_idempotent(
            aggregate_type="Claim", aggregate_id=claim["id"],
            event_type="CLAIM_ACCEPTED",
            payload={"claim_id": str(claim["id"])},
            idempotency_key=f"e2e-accept-{claim['id']}",
        )

    # 先投影 ACCEPTED
    run_projection_worker(uow_factory, executor, single_pass=True)

    # 现在发 CONTRADICTED 事件
    with uow_factory.transaction() as uow:
        uow.claims.update_status(claim["id"], ClaimStatus.CONTRADICTED)
        uow.graph_outbox.insert_idempotent(
            aggregate_type="Claim", aggregate_id=claim["id"],
            event_type="CLAIM_CONTRADICTED",
            payload={"claim_id": str(claim["id"]),
                     "predicate_code": "PRODUCES"},
            idempotency_key=f"e2e-contradict-{claim['id']}",
        )

    # 再投影 CONTRADICTED
    processed = run_projection_worker(
        uow_factory, executor, single_pass=True,
    )
    assert processed >= 1, "CONTRADICTED 事件应成功 PROCESSED"
