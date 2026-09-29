"""状态机唯一口径验证：代码枚举 ↔ PG 原生枚举 ↔ 文档三方一致 + 合法/非法迁移。"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from src.domain.enums import (
    CLAIM_TRANSITIONS,
    DOCUMENT_PARSE_TRANSITIONS,
    GRAPH_OUTBOX_TRANSITIONS,
    INGEST_RUN_TRANSITIONS,
    QUERY_TRANSITIONS,
    REVIEW_TASK_TRANSITIONS,
    ClaimStatus,
    DocumentParseStatus,
    DownloadStatus,
    GraphOutboxStatus,
    IllegalTransitionError,
    IngestRunStatus,
    QueryStatus,
    ReviewTaskStatus,
    ensure_transition,
    transition_table,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

PG_ENUM_NAMES = {
    "ingest_run": "ingest_run_status",
    "claim": "claim_status",
    "review_task": "review_task_status",
    "graph_outbox": "graph_outbox_status",
    "document_parse": "document_parse_status",
    "document_download": "download_status",
}


def test_pg_native_enums_match_python_enums(main_dsn) -> None:
    """代码枚举与 DDL 原生枚举逐值一致（含顺序）。"""
    machine_to_python = {
        "ingest_run": IngestRunStatus,
        "claim": ClaimStatus,
        "review_task": ReviewTaskStatus,
        "graph_outbox": GraphOutboxStatus,
        "document_parse": DocumentParseStatus,
        "document_download": DownloadStatus,
    }
    with psycopg.connect(main_dsn) as conn:
        for machine, enum_cls in machine_to_python.items():
            pg_values = [
                row[0]
                for row in conn.execute(
                    f"SELECT unnest(enum_range(NULL::{PG_ENUM_NAMES[machine]}))"
                ).fetchall()
            ]
            assert pg_values == [member.value for member in enum_cls], (
                f"{machine}: PG {pg_values} != Python {[m.value for m in enum_cls]}"
            )


def test_status_enums_consistent_with_design_document() -> None:
    """文档（docs/database-schema.sql）包含全部状态值（代码/DDL/文档一致）。"""
    doc = (REPO_ROOT / "docs" / "database-schema.sql").read_text(encoding="utf-8")
    for machine in (
        INGEST_RUN_TRANSITIONS,
        CLAIM_TRANSITIONS,
        REVIEW_TASK_TRANSITIONS,
        GRAPH_OUTBOX_TRANSITIONS,
        DOCUMENT_PARSE_TRANSITIONS,
    ):
        for current, targets in machine.items():
            assert current.value in doc, f"文档缺少状态值 {current.value}"
            for target in targets:
                assert target.value in doc, f"文档缺少状态值 {target.value}"


@pytest.mark.parametrize("machine", list(PG_ENUM_NAMES))
def test_all_legal_transitions_pass(machine: str) -> None:
    for current, targets in transition_table(machine).items():
        for target in targets:
            assert ensure_transition(machine, current, target) == target


@pytest.mark.parametrize("machine", list(PG_ENUM_NAMES))
def test_all_illegal_transitions_fail(machine: str) -> None:
    legal = transition_table(machine)
    all_values = set(legal)
    for current, targets in legal.items():
        for illegal in all_values - {current} - set(targets):
            with pytest.raises(IllegalTransitionError):
                ensure_transition(machine, current, illegal)


def test_claim_state_machine_matches_issue_specification() -> None:
    """与 issue #2 给定的 Claim 状态机逐边核对。"""
    assert CLAIM_TRANSITIONS[ClaimStatus.EXTRACTED] == frozenset(
        {ClaimStatus.VALIDATED, ClaimStatus.NEEDS_REVIEW, ClaimStatus.REJECTED}
    )
    assert CLAIM_TRANSITIONS[ClaimStatus.VALIDATED] == frozenset(
        {ClaimStatus.ACCEPTED, ClaimStatus.NEEDS_REVIEW}
    )
    assert CLAIM_TRANSITIONS[ClaimStatus.NEEDS_REVIEW] == frozenset(
        {ClaimStatus.ACCEPTED, ClaimStatus.REJECTED}
    )
    assert CLAIM_TRANSITIONS[ClaimStatus.ACCEPTED] == frozenset(
        {ClaimStatus.CONTRADICTED, ClaimStatus.SUPERSEDED}
    )
    assert CLAIM_TRANSITIONS[ClaimStatus.CONTRADICTED] == frozenset(
        {ClaimStatus.ACCEPTED, ClaimStatus.SUPERSEDED}
    )
    for terminal in (ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED):
        assert CLAIM_TRANSITIONS[terminal] == frozenset()


def test_ingest_run_state_machine_matches_issue_specification() -> None:
    assert INGEST_RUN_TRANSITIONS[IngestRunStatus.CREATED] == frozenset(
        {IngestRunStatus.RUNNING}
    )
    assert INGEST_RUN_TRANSITIONS[IngestRunStatus.RUNNING] == frozenset(
        {
            IngestRunStatus.PARTIAL_SUCCESS,
            IngestRunStatus.SUCCEEDED,
            IngestRunStatus.FAILED_RETRYABLE,
            IngestRunStatus.FAILED_FINAL,
            IngestRunStatus.CANCELLED,
        }
    )
    assert INGEST_RUN_TRANSITIONS[IngestRunStatus.FAILED_RETRYABLE] == frozenset(
        {IngestRunStatus.RUNNING, IngestRunStatus.FAILED_FINAL, IngestRunStatus.CANCELLED}
    )
    assert INGEST_RUN_TRANSITIONS[IngestRunStatus.PARTIAL_SUCCESS] == frozenset(
        {IngestRunStatus.RUNNING, IngestRunStatus.SUCCEEDED, IngestRunStatus.FAILED_FINAL}
    )
    for terminal in (
        IngestRunStatus.SUCCEEDED,
        IngestRunStatus.FAILED_FINAL,
        IngestRunStatus.CANCELLED,
    ):
        assert INGEST_RUN_TRANSITIONS[terminal] == frozenset()


def test_repository_rejects_illegal_ingest_transition_without_partial_write(
    uow_factory,
) -> None:
    """非法迁移失败且不产生部分写入（状态保持 CREATED）。"""
    with uow_factory.transaction() as uow:
        run = uow.ingest_runs.create(
            dataset_name="tushare:stock_basic", source_system="TUSHARE", trace_id="t-1"
        )
        run_id = run["id"]
        with pytest.raises(IllegalTransitionError):
            uow.ingest_runs.transition(run_id, IngestRunStatus.SUCCEEDED)  # CREATED -> SUCCEEDED
        # 事务回滚后状态仍是 CREATED
    with uow_factory.transaction() as uow:
        current = uow.ingest_runs.get(run_id)
        assert current["status"] == "CREATED"


# ---- Query 状态机（issue #9/#10；纯应用层，非 PG 原生枚举）----


def test_query_state_machine_matches_issue_specification() -> None:
    """与 issue #9 给定的查询状态机逐边核对。"""
    assert QUERY_TRANSITIONS[QueryStatus.RECEIVED] == frozenset(
        {QueryStatus.PLANNED, QueryStatus.REJECTED}
    )
    assert QUERY_TRANSITIONS[QueryStatus.PLANNED] == frozenset(
        {QueryStatus.RUNNING, QueryStatus.REJECTED}
    )
    assert QUERY_TRANSITIONS[QueryStatus.RUNNING] == frozenset(
        {
            QueryStatus.SUCCEEDED,
            QueryStatus.DEGRADED,
            QueryStatus.FAILED,
            QueryStatus.CANCELLED,
        }
    )
    for terminal in (
        QueryStatus.SUCCEEDED,
        QueryStatus.DEGRADED,
        QueryStatus.FAILED,
        QueryStatus.CANCELLED,
        QueryStatus.REJECTED,
    ):
        assert QUERY_TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("RECEIVED", "PLANNED"),
        ("RECEIVED", "REJECTED"),
        ("PLANNED", "RUNNING"),
        ("PLANNED", "REJECTED"),
        ("RUNNING", "SUCCEEDED"),
        ("RUNNING", "DEGRADED"),
        ("RUNNING", "FAILED"),
        ("RUNNING", "CANCELLED"),
    ],
)
def test_query_legal_transitions_pass(current: str, target: str) -> None:
    assert ensure_transition("query", current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("RECEIVED", "RUNNING"),  # 跳过计划阶段
        ("RECEIVED", "SUCCEEDED"),
        ("PLANNED", "SUCCEEDED"),  # 未执行不得成功（半结果不得标 SUCCEEDED）
        ("PLANNED", "DEGRADED"),
        ("RUNNING", "PLANNED"),  # 终态/降级不得回退
        ("SUCCEEDED", "RUNNING"),
        ("SUCCEEDED", "FAILED"),
        ("DEGRADED", "SUCCEEDED"),  # 降级是带说明的成功变体，不可逆
        ("REJECTED", "RUNNING"),  # 被拒计划必须重新 RECEIVED（新 query_id）
        ("FAILED", "SUCCEEDED"),
        ("CANCELLED", "RUNNING"),
    ],
)
def test_query_illegal_transitions_fail(current: str, target: str) -> None:
    with pytest.raises(IllegalTransitionError):
        ensure_transition("query", current, target)


def test_query_audit_status_values_respect_state_machine(main_dsn) -> None:
    """审计表记录的状态必须落在状态机值域内（防止自由字符串）。"""
    from src.query.compiler import QUERY_EXECUTION_VERSION

    valid_statuses = {member.value for member in QueryStatus}
    with psycopg.connect(main_dsn) as conn:
        conn.execute(
            "INSERT INTO query.query_audit (query_id, plan, intent, status, "
            "execution_version) VALUES (%s, %s, %s, %s, %s)",
            ("11111111-1111-1111-1111-111111111111", "{}", "ENTITY_LOOKUP",
             "SUCCEEDED", QUERY_EXECUTION_VERSION),
        )
        row = conn.execute(
            "SELECT status FROM query.query_audit "
            "WHERE query_id = '11111111-1111-1111-1111-111111111111'"
        ).fetchone()
    assert row[0] in valid_statuses
