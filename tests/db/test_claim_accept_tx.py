"""接受 Claim 原子事务：正常路径、四点故障注入回滚、并发冲突、幂等重放。"""

from __future__ import annotations

import uuid

import pytest
from src.db.repositories import ConcurrentClaimUpdateError
from src.domain.claim_service import ClaimAcceptanceError, accept_claim
from src.domain.enums import ClaimStatus, ReviewTaskStatus


def _seed_claim(uow, status: ClaimStatus | None = ClaimStatus.VALIDATED) -> dict:
    """建公司 + 文档 + 证据 + Claim（默认 EXTRACTED→VALIDATED），返回全部 id。"""

    company = uow.companies.insert(canonical_name="测试机器人公司")
    document = uow.documents.insert(
        document_type="ANNUAL_REPORT",
        source_system="CNINFO",
        title="2025 年年度报告",
        content_hash=uuid.uuid4().hex,
    )
    evidence = uow.claim_evidence.insert_evidence_fragment(
        document_id=document["id"], quote_text="机器人核心零部件已量产", checksum=uuid.uuid4().hex
    )
    claim = uow.claims.insert(
        subject_entity_type="Company",
        subject_entity_id=company["id"],
        predicate_code="PRODUCES",
        content_hash=uuid.uuid4().hex,
        extraction_method="LLM_ASSISTED",
        ontology_version="v1",
        confidence=0.9,
        business_stage="MASS_PRODUCTION",
        object_value={"product": "谐波减速器"},
    )
    if status is not None:
        uow.claims.update_status(claim["id"], ClaimStatus.VALIDATED)
        claim["claim_status"] = ClaimStatus.VALIDATED.value
    return {
        "company_id": company["id"],
        "document_id": document["id"],
        "evidence_id": evidence["id"],
        "claim_id": claim["id"],
    }


def test_accept_claim_writes_all_five_parts_atomically(uow_factory) -> None:
    with uow_factory.transaction() as uow:
        ids = _seed_claim(uow, status=ClaimStatus.VALIDATED)
        task = uow.review_tasks.create(task_type="CLAIM_REVIEW", claim_id=ids["claim_id"])
        uow.review_tasks.transition(task["id"], ReviewTaskStatus.IN_PROGRESS)

        result = accept_claim(
            uow,
            claim_id=ids["claim_id"],
            reviewed_by="reviewer-a",
            evidence_ids=[ids["evidence_id"]],
            provenance={
                "activity_id": "act-1",
                "agent_id": "agent-extractor",
                "source_document_id": ids["document_id"],
                "source_quote": "机器人核心零部件已量产",
                "checksum": uuid.uuid4().hex,
            },
            trace_id="trace-accept-1",
            review_task_id=task["id"],
        )

        assert result.status == ClaimStatus.ACCEPTED
        assert result.outbox_inserted is True
        assert uow.claims.get(ids["claim_id"])["claim_status"] == "ACCEPTED"
        assert uow.claim_evidence.count_for_claim(ids["claim_id"]) == 1
        assert uow.provenance.count_for_entity(str(ids["claim_id"])) == 1
        assert uow.audit_events.count_for_entity("Claim", ids["claim_id"]) == 1
        assert uow.graph_outbox.count_all() == 1
        assert uow.review_tasks.get(task["id"])["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "fail_at",
    ["link_evidence", "provenance", "audit_event", "outbox"],
)
def test_any_step_failure_rolls_back_everything(uow_factory, monkeypatch, fail_at) -> None:
    """Evidence/Provenance/审计/Outbox 任一步失败：Claim 不得进入 ACCEPTED，
    全部写入回滚，无半完成状态。"""
    with uow_factory.transaction() as setup:
        ids = _seed_claim(setup, status=ClaimStatus.VALIDATED)

    class Boom(RuntimeError):
        pass

    def boom(*args, **kwargs):
        raise Boom(f"injected at {fail_at}")

    # 异常必须逸出事务作用域：生产语义是调用方不吞异常、事务助手整体回滚，
    # 绝不允许在事务块内消化异常导致半完成写入被提交
    with pytest.raises(Boom):
        with uow_factory.transaction() as uow:
            if fail_at == "link_evidence":
                monkeypatch.setattr(uow.claim_evidence, "link", boom)
            elif fail_at == "provenance":
                monkeypatch.setattr(uow.provenance, "insert", boom)
            elif fail_at == "audit_event":
                monkeypatch.setattr(uow.audit_events, "insert", boom)
            else:
                monkeypatch.setattr(uow.graph_outbox, "insert_idempotent", boom)

            accept_claim(
                uow,
                claim_id=ids["claim_id"],
                reviewed_by="reviewer-a",
                evidence_ids=[ids["evidence_id"]],
                provenance={
                    "activity_id": "act-1",
                    "checksum": uuid.uuid4().hex,
                },
            )

    with uow_factory.transaction() as verify:
        claim = verify.claims.get(ids["claim_id"])
        assert claim["claim_status"] == "VALIDATED", "失败后不得进入 ACCEPTED"
        assert verify.claim_evidence.count_for_claim(ids["claim_id"]) == 0
        assert verify.provenance.count_for_entity(str(ids["claim_id"])) == 0
        assert verify.audit_events.count_for_entity("Claim", ids["claim_id"]) == 0
        assert verify.graph_outbox.count_all() == 0


def test_accept_without_evidence_is_rejected(uow_factory) -> None:
    """经营 Claim 接受必须关联 Evidence（ADR-0002 #4）。"""
    with uow_factory.transaction() as setup:
        ids = _seed_claim(setup, status=ClaimStatus.VALIDATED)

    with pytest.raises(ClaimAcceptanceError):
        with uow_factory.transaction() as uow:
            accept_claim(
                uow,
                claim_id=ids["claim_id"],
                reviewed_by="reviewer-a",
                evidence_ids=[],
                provenance={"activity_id": "act-1", "checksum": uuid.uuid4().hex},
            )


def test_accept_with_missing_evidence_is_rejected(uow_factory) -> None:
    """Evidence 不存在：不得进入 ACCEPTED。"""
    with uow_factory.transaction() as setup:
        ids = _seed_claim(setup, status=ClaimStatus.VALIDATED)

    ghost = uuid.uuid4()
    with pytest.raises(ClaimAcceptanceError):
        with uow_factory.transaction() as uow:
            accept_claim(
                uow,
                claim_id=ids["claim_id"],
                reviewed_by="reviewer-a",
                evidence_ids=[ghost],
                provenance={"activity_id": "act-1", "checksum": uuid.uuid4().hex},
            )
    with uow_factory.transaction() as verify:
        assert verify.claims.get(ids["claim_id"])["claim_status"] == "VALIDATED"


def test_illegal_accept_on_terminal_status_is_rejected(uow_factory) -> None:
    """非法迁移：EXTRACTED 直接接受 → 失败且不产生部分写入。"""
    with uow_factory.transaction() as setup:
        ids = _seed_claim(setup, status=None)  # 保持 EXTRACTED

    with pytest.raises(ClaimAcceptanceError):
        with uow_factory.transaction() as uow:
            accept_claim(
                uow,
                claim_id=ids["claim_id"],
                reviewed_by="reviewer-a",
                evidence_ids=[ids["evidence_id"]],
                provenance={"activity_id": "act-1", "checksum": uuid.uuid4().hex},
            )
    with uow_factory.transaction() as verify:
        assert verify.claims.get(ids["claim_id"])["claim_status"] == "EXTRACTED"
        assert verify.audit_events.count_for_entity("Claim", ids["claim_id"]) == 0


def test_concurrent_review_only_one_wins(uow_factory) -> None:
    """两个审核者并发处理同一 Claim：恰好一个成功，另一个得到冲突。"""
    with uow_factory.transaction() as setup:
        ids = _seed_claim(setup, status=ClaimStatus.VALIDATED)

    reviewer_a = uow_factory.open()  # 独立连接/事务
    uow_a = reviewer_a
    accept_claim(
        uow_a,
        claim_id=ids["claim_id"],
        reviewed_by="reviewer-a",
        evidence_ids=[ids["evidence_id"]],
        provenance={"activity_id": "act-1", "checksum": uuid.uuid4().hex},
    )  # 持有行锁，未提交

    uow_b = uow_factory.open()
    with pytest.raises(ConcurrentClaimUpdateError):
        accept_claim(
            uow_b,
            claim_id=ids["claim_id"],
            reviewed_by="reviewer-b",
            evidence_ids=[ids["evidence_id"]],
            provenance={"activity_id": "act-2", "checksum": uuid.uuid4().hex},
        )
    uow_b.rollback()

    uow_a.commit()
    uow_a.close()
    uow_b.close()

    with uow_factory.transaction() as verify:
        claim = verify.claims.get(ids["claim_id"])
        assert claim["claim_status"] == "ACCEPTED"
        assert claim["reviewed_by"] == "reviewer-a", "不得覆盖第一个决定"
        assert verify.graph_outbox.count_all() == 1, "不得产生双重 Outbox 事件"
        assert verify.audit_events.count_for_entity("Claim", ids["claim_id"]) == 1


def test_replay_same_fixture_twice_produces_no_duplicates(uow_factory) -> None:
    """重放同一 Fixture 两次：SourceRecord / Claim / Outbox 均收敛为一条。"""
    payload_hash = uuid.uuid4().hex
    content_hash = uuid.uuid4().hex

    # 主体/文档/证据只种子一次（重放消费的是同一 Fixture，不重建标准实体）
    with uow_factory.transaction() as setup:
        company = setup.companies.insert(
            canonical_name="重放测试公司", unified_social_credit_code="USCC-REPLAY-1"
        )
        document = setup.documents.insert(
            document_type="ANNUAL_REPORT", source_system="CNINFO",
            title="年报", content_hash=uuid.uuid4().hex,
        )
        evidence = setup.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], quote_text="证据", checksum=uuid.uuid4().hex
        )

    for round_number in (1, 2):
        with uow_factory.transaction() as uow:
            run = uow.ingest_runs.create(
                dataset_name="tushare:stock_basic", source_system="TUSHARE",
                trace_id=f"trace-{round_number}",
            )
            record = uow.source_records.insert_idempotent(
                source_system="TUSHARE",
                api_name="stock_basic",
                payload_hash=payload_hash,
                raw_payload={"code": "000001.SZ", "round": round_number},
                ingest_run_id=run["id"],
            )
            claim = uow.claims.insert(
                subject_entity_type="Company",
                subject_entity_id=company["id"],
                predicate_code="PRODUCES",
                content_hash=content_hash,
                extraction_method="RULE",
                ontology_version="v1",
                confidence=0.9,
                object_value={"x": 1},
            )
            if claim["inserted"]:
                uow.claims.update_status(claim["id"], ClaimStatus.VALIDATED)
                accept_claim(
                    uow,
                    claim_id=claim["id"],
                    reviewed_by="reviewer-a",
                    evidence_ids=[evidence["id"]],
                    provenance={"activity_id": "act-replay", "checksum": uuid.uuid4().hex},
                )
            if round_number == 2:
                assert record["inserted"] is False
                assert claim["inserted"] is False  # 幂等：重放不产生新 Claim

    with uow_factory.transaction() as verify:
        assert verify._conn.execute(
            "SELECT count(*) FROM raw.source_record WHERE payload_hash = %s",
            (payload_hash,),
        ).fetchone()[0] == 1
        assert verify._conn.execute(
            "SELECT count(*) FROM fact.claim WHERE content_hash = %s", (content_hash,)
        ).fetchone()[0] == 1
        assert verify.graph_outbox.count_all() == 1
        assert verify.audit_events.count_for_entity(
            "Claim", verify._conn.execute(
                "SELECT id FROM fact.claim WHERE content_hash = %s", (content_hash,)
            ).fetchone()[0]
        ) == 1
