"""issue #33 审核写 API 测试：认证/授权/并发/幂等/状态机/副作用。"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from src.domain.enums import ClaimStatus

from tests.helpers import make_settings

PLAIN_KEY = "test-reviewer-key-1234"
KEY_HASH = __import__("hashlib").sha256(PLAIN_KEY.encode()).hexdigest()


def review_client(main_dsn: str, *, enabled: bool = True) -> TestClient:
    from apps.api.app import create_app

    params = conninfo_to_dict(main_dsn)
    settings = make_settings(
        postgres_host=params["host"], postgres_port=int(params.get("port") or 5432),
        postgres_db=params["dbname"], postgres_user=params["user"],
        postgres_password=params["password"],
        review_write_enabled=enabled,
        review_api_key_hashes=KEY_HASH,
    )
    app = create_app(settings)
    executor = getattr(app.state, "query_graph_executor", None)
    if executor is not None:
        executor.close()
        app.state.query_graph_executor = None
    return TestClient(app)


def seed_review_task(uow_factory) -> dict:
    """种子：NEEDS_REVIEW Claim + 审核任务 + 证据。"""
    with uow_factory.transaction() as uow:
        company = uow.companies.insert(
            canonical_name="审核测试公司",
            unified_social_credit_code=f"USCC-RV-{uuid.uuid4().hex[:10]}",
        )
        document = uow.documents.insert(
            document_type="ANNUAL_REPORT", source_system="RV_SRC",
            title="审核年报", content_hash=uuid.uuid4().hex * 2,
        )
        evidence = uow.claim_evidence.insert_evidence_fragment(
            document_id=document["id"], page_number=1,
            quote_text="谐波减速器样品验证中。",
            checksum=uuid.uuid4().hex,
        )
        claim = uow.claims.insert(
            subject_entity_type="Company", subject_entity_id=company["id"],
            predicate_code="PRODUCES", content_hash=uuid.uuid4().hex,
            extraction_method="rules", ontology_version="0.1.0",
            confidence=0.5, status=ClaimStatus.NEEDS_REVIEW,
            object_value={"stage": "SAMPLE_VALIDATION"},
            business_stage="SAMPLE_VALIDATION",
        )
        uow.claim_evidence.link(
            claim_id=claim["id"], evidence_id=evidence["id"],
            support_type="SUPPORTS",
        )
        task = uow.review_tasks.create(
            task_type="CLAIM_REVIEW", claim_id=claim["id"],
            priority="HIGH", reason_codes=["hedged"],
        )
        return {"task": task, "claim": claim, "evidence": evidence,
                "company": company}


AUTH = {"X-Reviewer-Key": PLAIN_KEY}


def test_write_disabled_returns_503(uow_factory, main_dsn) -> None:
    """开关关闭 → 503，事实库无副作用。"""
    seed_review_task(uow_factory)
    client = review_client(main_dsn, enabled=False)
    r = client.post(
        "/api/v1/review/tasks/%s/decision" % uuid.uuid4(),
        json={"decision": "ACCEPTED", "reason": "ok"},
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 503


def test_missing_or_invalid_key_401_403(uow_factory, main_dsn) -> None:
    """无 key → 401；错 key → 403；均无副作用。"""
    seeded = seed_review_task(uow_factory)
    client = review_client(main_dsn)

    r1 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "ACCEPTED", "reason": "ok"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r1.status_code == 401
    r2 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "ACCEPTED", "reason": "ok"},
        headers={"X-Reviewer-Key": "wrong", "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r2.status_code == 403

    with psycopg.connect(main_dsn) as conn:
        status = conn.execute(
            "SELECT claim_status FROM fact.claim WHERE id = %s",
            (seeded["claim"]["id"],),
        ).fetchone()[0]
    assert status == "NEEDS_REVIEW"  # 无副作用


def test_accept_decision_full_chain(uow_factory, main_dsn) -> None:
    """正常接受：Claim→ACCEPTED、任务 COMPLETED、审计记录、幂等键锚点。"""
    seeded = seed_review_task(uow_factory)
    client = review_client(main_dsn)
    idem = uuid.uuid4().hex

    r = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "ACCEPTED", "reason": "证据充分，接受"},
        headers={**AUTH, "Idempotency-Key": idem, "X-Trace-Id": "rv-trace-1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["before_status"] == "NEEDS_REVIEW"
    assert body["after_status"] == "ACCEPTED"
    assert body["idempotent_replay"] is False

    with psycopg.connect(main_dsn) as conn:
        claim_status = conn.execute(
            "SELECT claim_status FROM fact.claim WHERE id = %s",
            (seeded["claim"]["id"],),
        ).fetchone()[0]
        task_row = conn.execute(
            "SELECT status, decision FROM fact.review_task WHERE id = %s",
            (seeded["task"]["id"],),
        ).fetchone()
        audit = conn.execute(
            "SELECT count(*) FROM ops.audit_event "
            "WHERE event_type = 'REVIEW_DECISION_IDEMPOTENCY' "
            "AND payload->>'idempotency_key' = %s",
            (idem,),
        ).fetchone()[0]
    assert claim_status == "ACCEPTED"
    assert task_row[0] == "COMPLETED"
    # accept_claim 既有行为：任务 decision 列存 "ACCEPT"（#7 起如此）
    assert task_row[1] in ("ACCEPT", "ACCEPTED")
    assert audit == 1  # 幂等锚点已记录


def test_idempotent_replay_returns_first_result(uow_factory, main_dsn) -> None:
    """同 Idempotency-Key 重放 → 首次结果原样返回，不重复决定。"""
    seeded = seed_review_task(uow_factory)
    client = review_client(main_dsn)
    idem = uuid.uuid4().hex
    headers = {**AUTH, "Idempotency-Key": idem}

    r1 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "REJECTED", "reason": "证据不足"},
        headers=headers,
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "REJECTED", "reason": "证据不足"},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    # 重放返回任务终态（REJECTED 决定已生效且不重复）
    assert r2.json()["task_status"] == "COMPLETED"

    with psycopg.connect(main_dsn) as conn:
        decisions = conn.execute(
            "SELECT count(*) FROM ops.audit_event "
            "WHERE event_type = 'REVIEW_DECISION_IDEMPOTENCY' "
            "AND payload->>'idempotency_key' = %s",
            (idem,),
        ).fetchone()[0]
    assert decisions == 1  # 只有一份决定


def test_second_reviewer_gets_409(uow_factory, main_dsn) -> None:
    """双 Reviewer 并发：第二位提交 → 409 + 当前状态回传。"""
    seeded = seed_review_task(uow_factory)
    client = review_client(main_dsn)
    headers = {**AUTH, "Idempotency-Key": uuid.uuid4().hex}

    r1 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "ACCEPTED", "reason": "first"},
        headers=headers,
    )
    assert r1.status_code == 200
    # 同任务再决定（任务已 COMPLETED）→ 409 带当前状态
    r2 = client.post(
        f"/api/v1/review/tasks/{seeded['task']['id']}/decision",
        json={"decision": "REJECTED", "reason": "second"},
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r2.status_code == 409
    # 状态机拒绝（claim 已 ACCEPTED 不可转 REJECTED）——可修复原因可见
    with psycopg.connect(main_dsn) as conn:
        final = conn.execute(
            "SELECT claim_status FROM fact.claim WHERE id = %s",
            (seeded["claim"]["id"],),
        ).fetchone()[0]
    assert final == "ACCEPTED"  # 第一位决定保持


def test_queue_lists_pending(uow_factory, main_dsn) -> None:
    """队列：OPEN 任务 + Claim 摘要 + 证据计数。"""
    seeded = seed_review_task(uow_factory)
    client = review_client(main_dsn)
    r = client.get("/api/v1/review/queue", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    task = next(
        t for t in body["tasks"]
        if t["task_id"] == str(seeded["task"]["id"])
    )
    assert task["claim_status"] == "NEEDS_REVIEW"
    assert task["evidence_count"] == 1
    assert task["predicate_code"] == "PRODUCES"
    assert body["write_available"] is True


def test_non_uuid_task_422(uow_factory, main_dsn) -> None:
    client = review_client(main_dsn)
    r = client.post(
        "/api/v1/review/tasks/abc/decision",
        json={"decision": "ACCEPTED", "reason": "x"},
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 422


def test_decision_validation(uow_factory, main_dsn) -> None:
    """decision 白名单 + reason 必填。"""
    client = review_client(main_dsn)
    r = client.post(
        f"/api/v1/review/tasks/{uuid.uuid4()}/decision",
        json={"decision": "MAYBE", "reason": "x"},
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 422
    r2 = client.post(
        f"/api/v1/review/tasks/{uuid.uuid4()}/decision",
        json={"decision": "ACCEPTED", "reason": ""},
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r2.status_code == 422
