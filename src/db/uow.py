"""Unit of Work：显式事务边界。

不变量（issue #2）：一个事务内的全部写入要么全部提交、要么全部回滚；
仓储永远不得自行 commit。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from src.db.repositories import (
    AuditEventRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    CompanyRepository,
    DocumentRepository,
    GraphOutboxRepository,
    IngestRunRepository,
    ProvenanceRepository,
    ReviewTaskRepository,
    SecurityRepository,
    SourceCapabilityRepository,
    SourceRecordRepository,
)


class UnitOfWork:
    """绑定单个 PostgreSQL 连接/事务的仓储集合。"""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self.ingest_runs = IngestRunRepository(conn)
        self.source_records = SourceRecordRepository(conn)
        self.source_capabilities = SourceCapabilityRepository(conn)
        self.companies = CompanyRepository(conn)
        self.securities = SecurityRepository(conn)
        self.claims = ClaimRepository(conn)
        self.claim_evidence = ClaimEvidenceRepository(conn)
        self.provenance = ProvenanceRepository(conn)
        self.review_tasks = ReviewTaskRepository(conn)
        self.graph_outbox = GraphOutboxRepository(conn)
        self.audit_events = AuditEventRepository(conn)
        self.documents = DocumentRepository(conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def ping(self) -> None:
        self._conn.execute("SELECT 1")

    def close(self) -> None:
        self._conn.close()


class UnitOfWorkFactory:
    """从 DSN 打开连接并包装为 Unit of Work。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def open(self) -> UnitOfWork:
        conn = psycopg.connect(self._dsn)
        return UnitOfWork(conn)

    @contextmanager
    def transaction(self) -> Iterator[UnitOfWork]:
        """事务作用域：正常退出提交，异常回滚后重抛；连接确保关闭。"""
        uow = self.open()
        try:
            yield uow
        except Exception:
            uow.rollback()
            raise
        else:
            uow.commit()
        finally:
            uow.close()


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
