"""关键状态机唯一口径（issue #2）。

代码枚举与数据库原生枚举（migrations/versions/0001_*）逐值同步，
`docs/database-schema.sql` 与 `docs/data-sources-and-ingestion.md` 同步维护。
新增状态或迁移路径时必须四处同步，并有 tests/db/test_state_machines.py
的"PG 枚举与 Python 枚举一致"测试守护。
"""

from __future__ import annotations

from enum import StrEnum


class IngestRunStatus(StrEnum):
    """采集任务状态机（统一 data-sources-and-ingestion.md 的 7 态）。"""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


class ClaimStatus(StrEnum):
    """Claim 状态机（ADR-0002）。历史 Claim 不物理删除。

    成员顺序与 DDL 原生枚举 claim_status 逐位一致（tests 守护）。
    """

    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ACCEPTED = "ACCEPTED"
    CONTRADICTED = "CONTRADICTED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class ReviewTaskStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GraphOutboxStatus(StrEnum):
    """图投影 Outbox 状态机。DEAD_LETTERED 为终态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    DEAD_LETTERED = "DEAD_LETTERED"


class DocumentParseStatus(StrEnum):
    """披露文档解析状态机（沿用采集任务的可重试/最终失败区分）。"""

    PENDING = "PENDING"
    PARSING = "PARSING"
    PARSED = "PARSED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    SKIPPED = "SKIPPED"


class IllegalTransitionError(Exception):
    """非法状态迁移：必须失败且不得产生部分写入。"""

    def __init__(self, machine: str, current: str, target: str) -> None:
        super().__init__(
            f"illegal {machine} transition: {current} -> {target}"
        )
        self.machine = machine
        self.current = current
        self.target = target


INGEST_RUN_TRANSITIONS: dict[IngestRunStatus, frozenset[IngestRunStatus]] = {
    IngestRunStatus.CREATED: frozenset({IngestRunStatus.RUNNING}),
    IngestRunStatus.RUNNING: frozenset(
        {
            IngestRunStatus.PARTIAL_SUCCESS,
            IngestRunStatus.SUCCEEDED,
            IngestRunStatus.FAILED_RETRYABLE,
            IngestRunStatus.FAILED_FINAL,
            IngestRunStatus.CANCELLED,
        }
    ),
    IngestRunStatus.FAILED_RETRYABLE: frozenset(
        {
            IngestRunStatus.RUNNING,
            IngestRunStatus.FAILED_FINAL,
            IngestRunStatus.CANCELLED,
        }
    ),
    IngestRunStatus.PARTIAL_SUCCESS: frozenset(
        {
            IngestRunStatus.RUNNING,
            IngestRunStatus.SUCCEEDED,
            IngestRunStatus.FAILED_FINAL,
        }
    ),
    IngestRunStatus.SUCCEEDED: frozenset(),
    IngestRunStatus.FAILED_FINAL: frozenset(),
    IngestRunStatus.CANCELLED: frozenset(),
}

CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.EXTRACTED: frozenset(
        {ClaimStatus.VALIDATED, ClaimStatus.NEEDS_REVIEW, ClaimStatus.REJECTED}
    ),
    ClaimStatus.VALIDATED: frozenset({ClaimStatus.ACCEPTED, ClaimStatus.NEEDS_REVIEW}),
    ClaimStatus.NEEDS_REVIEW: frozenset({ClaimStatus.ACCEPTED, ClaimStatus.REJECTED}),
    ClaimStatus.ACCEPTED: frozenset({ClaimStatus.CONTRADICTED, ClaimStatus.SUPERSEDED}),
    ClaimStatus.CONTRADICTED: frozenset({ClaimStatus.ACCEPTED, ClaimStatus.SUPERSEDED}),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.SUPERSEDED: frozenset(),
}

REVIEW_TASK_TRANSITIONS: dict[ReviewTaskStatus, frozenset[ReviewTaskStatus]] = {
    ReviewTaskStatus.OPEN: frozenset(
        {ReviewTaskStatus.IN_PROGRESS, ReviewTaskStatus.COMPLETED, ReviewTaskStatus.CANCELLED}
    ),
    ReviewTaskStatus.IN_PROGRESS: frozenset(
        {ReviewTaskStatus.COMPLETED, ReviewTaskStatus.CANCELLED}
    ),
    ReviewTaskStatus.COMPLETED: frozenset(),
    ReviewTaskStatus.CANCELLED: frozenset(),
}

GRAPH_OUTBOX_TRANSITIONS: dict[GraphOutboxStatus, frozenset[GraphOutboxStatus]] = {
    GraphOutboxStatus.PENDING: frozenset({GraphOutboxStatus.PROCESSING}),
    GraphOutboxStatus.PROCESSING: frozenset(
        {
            GraphOutboxStatus.PROCESSED,
            GraphOutboxStatus.PENDING,  # 重试：保留 retry_count，重新可见
            GraphOutboxStatus.DEAD_LETTERED,
        }
    ),
    GraphOutboxStatus.PROCESSED: frozenset(),
    GraphOutboxStatus.DEAD_LETTERED: frozenset(),
}

DOCUMENT_PARSE_TRANSITIONS: dict[DocumentParseStatus, frozenset[DocumentParseStatus]] = {
    DocumentParseStatus.PENDING: frozenset(
        {DocumentParseStatus.PARSING, DocumentParseStatus.SKIPPED}
    ),
    DocumentParseStatus.PARSING: frozenset(
        {
            DocumentParseStatus.PARSED,
            DocumentParseStatus.FAILED_RETRYABLE,
            DocumentParseStatus.FAILED_FINAL,
        }
    ),
    DocumentParseStatus.FAILED_RETRYABLE: frozenset(
        {
            DocumentParseStatus.PARSING,
            DocumentParseStatus.FAILED_FINAL,
            DocumentParseStatus.SKIPPED,
        }
    ),
    DocumentParseStatus.PARSED: frozenset(),
    DocumentParseStatus.FAILED_FINAL: frozenset(),
    DocumentParseStatus.SKIPPED: frozenset(),
}

MACHINES: dict[str, type] = {
    "ingest_run": IngestRunStatus,
    "claim": ClaimStatus,
    "review_task": ReviewTaskStatus,
    "graph_outbox": GraphOutboxStatus,
    "document_parse": DocumentParseStatus,
}


def transition_table(machine: str) -> dict[str, frozenset[str]]:
    tables = {
        "ingest_run": INGEST_RUN_TRANSITIONS,
        "claim": CLAIM_TRANSITIONS,
        "review_task": REVIEW_TASK_TRANSITIONS,
        "graph_outbox": GRAPH_OUTBOX_TRANSITIONS,
        "document_parse": DOCUMENT_PARSE_TRANSITIONS,
    }
    return {current.value: {target.value for target in targets}
            for current, targets in tables[machine].items()}


def ensure_transition(machine: str, current: str, target: str) -> str:
    """校验状态迁移合法；非法时抛 IllegalTransitionError（调用方不得写入）。"""
    if target not in transition_table(machine).get(current, frozenset()):
        raise IllegalTransitionError(machine, current, target)
    return target
