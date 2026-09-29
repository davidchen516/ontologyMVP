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
    """披露文档解析状态机（沿用采集任务的可重试/最终失败区分）。

    PARSE_NEEDS_REVIEW：纯扫描/低文本文档显式进入人工审核，
    不进入自动 Claim 抽取（migration 0006）。
    """

    PENDING = "PENDING"
    PARSING = "PARSING"
    PARSED = "PARSED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    SKIPPED = "SKIPPED"
    PARSE_NEEDS_REVIEW = "PARSE_NEEDS_REVIEW"


class DownloadStatus(StrEnum):
    """文档下载状态机（issue #6）：任一处理中状态可转失败。"""

    DISCOVERED = "DISCOVERED"
    DOWNLOAD_PENDING = "DOWNLOAD_PENDING"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    DOWNLOAD_FAILED_RETRYABLE = "DOWNLOAD_FAILED_RETRYABLE"
    DOWNLOAD_FAILED_FINAL = "DOWNLOAD_FAILED_FINAL"


DOWNLOAD_TRANSITIONS: dict[DownloadStatus, frozenset[DownloadStatus]] = {
    DownloadStatus.DISCOVERED: frozenset({DownloadStatus.DOWNLOAD_PENDING}),
    DownloadStatus.DOWNLOAD_PENDING: frozenset({DownloadStatus.DOWNLOADING}),
    DownloadStatus.DOWNLOADING: frozenset(
        {
            DownloadStatus.DOWNLOADED,
            DownloadStatus.DOWNLOAD_FAILED_RETRYABLE,
            DownloadStatus.DOWNLOAD_FAILED_FINAL,
        }
    ),
    DownloadStatus.DOWNLOADED: frozenset({DownloadStatus.DOWNLOAD_PENDING}),
    DownloadStatus.DOWNLOAD_FAILED_RETRYABLE: frozenset(
        {DownloadStatus.DOWNLOAD_PENDING}
    ),
    DownloadStatus.DOWNLOAD_FAILED_FINAL: frozenset(),
}

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
            DocumentParseStatus.PARSE_NEEDS_REVIEW,
            DocumentParseStatus.FAILED_RETRYABLE,
            DocumentParseStatus.FAILED_FINAL,
        }
    ),
    DocumentParseStatus.PARSE_NEEDS_REVIEW: frozenset(
        # 人工审核后重解析或跳过
        {DocumentParseStatus.PENDING, DocumentParseStatus.SKIPPED}
    ),
    DocumentParseStatus.FAILED_RETRYABLE: frozenset(
        {
            # issue #6 状态机：FAILED_RETRYABLE -> 对应 PENDING 状态（重试回到排队）
            DocumentParseStatus.PENDING,
            DocumentParseStatus.PARSING,
            DocumentParseStatus.FAILED_FINAL,
            DocumentParseStatus.SKIPPED,
        }
    ),
    DocumentParseStatus.PARSED: frozenset(),
    DocumentParseStatus.FAILED_FINAL: frozenset(),
    DocumentParseStatus.SKIPPED: frozenset(),
}


class QueryStatus(StrEnum):
    """查询执行状态机（issue #9：RECEIVED -> PLANNED -> RUNNING -> 终态/降级）。

    纯应用层状态（审计表 VARCHAR，非 PG 原生枚举）；REJECTED 记录在
    审计错误类别，DEGRADED 是带明确说明的成功变体。
    注意：本机当前仅规格守护（合法/非法迁移表测试 + 审计状态值域检查），
    无运行时写入点——查询编译器一次性写终态（SUCCEEDED/DEGRADED），
    ensure_transition("query", ...) 留给后续异步执行/取消路径接入时强制。
    与 src/query/models.py 的 QueryStatus 值域一致性由测试守护（M2）。
    """

    RECEIVED = "RECEIVED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"


QUERY_TRANSITIONS: dict[QueryStatus, frozenset[QueryStatus]] = {
    QueryStatus.RECEIVED: frozenset(
        {QueryStatus.PLANNED, QueryStatus.REJECTED}
    ),
    QueryStatus.PLANNED: frozenset(
        {QueryStatus.RUNNING, QueryStatus.REJECTED}
    ),
    QueryStatus.RUNNING: frozenset(
        {
            QueryStatus.SUCCEEDED,
            QueryStatus.DEGRADED,
            QueryStatus.FAILED,
            QueryStatus.CANCELLED,
        }
    ),
    QueryStatus.SUCCEEDED: frozenset(),
    QueryStatus.DEGRADED: frozenset(),
    QueryStatus.FAILED: frozenset(),
    QueryStatus.CANCELLED: frozenset(),
    QueryStatus.REJECTED: frozenset(),
}

MACHINES: dict[str, type] = {
    "ingest_run": IngestRunStatus,
    "claim": ClaimStatus,
    "review_task": ReviewTaskStatus,
    "graph_outbox": GraphOutboxStatus,
    "document_parse": DocumentParseStatus,
    "document_download": DownloadStatus,
    "query": QueryStatus,
}


def transition_table(machine: str) -> dict[str, frozenset[str]]:
    tables = {
        "ingest_run": INGEST_RUN_TRANSITIONS,
        "claim": CLAIM_TRANSITIONS,
        "review_task": REVIEW_TASK_TRANSITIONS,
        "graph_outbox": GRAPH_OUTBOX_TRANSITIONS,
        "document_parse": DOCUMENT_PARSE_TRANSITIONS,
        "document_download": DOWNLOAD_TRANSITIONS,
        "query": QUERY_TRANSITIONS,
    }
    return {current.value: {target.value for target in targets}
            for current, targets in tables[machine].items()}


def ensure_transition(machine: str, current: str, target: str) -> str:
    """校验状态迁移合法；非法时抛 IllegalTransitionError（调用方不得写入）。"""
    if target not in transition_table(machine).get(current, frozenset()):
        raise IllegalTransitionError(machine, current, target)
    return target
