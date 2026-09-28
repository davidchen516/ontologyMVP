"""PostgresProvenanceStorage DB 测试（issue #5 验收）。

覆盖：重启可读（新实例读取）、Hash 链校验、并发写入无分叉/无重复序号、
幂等注册、崩溃不留断链、破坏性 clear 权限保护、lineage/descendants。
"""

from __future__ import annotations

import threading

import psycopg
import pytest
from src.semantic.pg_provenance import (
    PostgresProvenanceStorage,
    ProvenancePermissionError,
)
from src.semantic.ports import (
    ConflictKind,
    ProvenanceInput,
    SemanticCapabilityError,
)
from src.semantic.semantica_adapter import SemanticaRuntimeAdapter

SHAPES = "ontology/shapes.ttl"


class SimpleEntry:
    """duck-typed ProvenanceEntry（存储层通过 getattr 读取字段）。"""

    def __init__(self, entity_id: str, activity_id: str, **kwargs):
        self.entity_id = entity_id
        self.entity_type = kwargs.get("entity_type", "Claim")
        self.activity_id = activity_id
        self.agent_id = kwargs.get("agent_id", "adapter")
        self.source_document = kwargs.get("source_document")
        self.source_quote = kwargs.get("source_quote")
        self.source_location = kwargs.get("source_location")
        self.parent_entity_id = kwargs.get("parent_entity_id")
        self.used_entities = kwargs.get("used_entities", [])
        self.confidence = kwargs.get("confidence")
        self.checksum = kwargs.get("checksum")
        self.previous_checksum = None
        self.metadata = kwargs.get("metadata", {})
        self.valid_from = None
        self.valid_until = None
        self.supersedes = None


def test_store_and_read_back_after_restart(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    storage.store(SimpleEntry("entity-r1", "act-1", parent_entity_id=None))
    storage.store(SimpleEntry("entity-r2", "act-2", parent_entity_id="entity-r1"))

    # 新实例读取 == 进程重启后 lineage 仍可读（不依赖内存缓存）
    fresh = PostgresProvenanceStorage(main_dsn)
    lineage = fresh.trace_lineage("entity-r2")
    assert [r["entity_id"] for r in lineage] == ["entity-r2", "entity-r1"]
    assert fresh.verify_chain() == []


def test_chain_sequence_and_links(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    ids = [f"chain-{i}" for i in range(5)]
    for i, entity in enumerate(ids):
        storage.store(SimpleEntry(entity, f"act-{i}"))

    with psycopg.connect(main_dsn) as conn:
        rows = conn.execute(
            "SELECT sequence_id, previous_checksum, checksum "
            "FROM fact.provenance_entry ORDER BY sequence_id"
        ).fetchall()
    sequences = [r[0] for r in rows]
    assert sequences == sorted(set(sequences))  # 无重复序号
    # 链完整性：previous_checksum 逐条咬合
    for i in range(1, len(rows)):
        assert rows[i][1] == rows[i - 1][2]
    assert storage.verify_chain() == []


def test_idempotent_registration_returns_existing(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    entry = SimpleEntry("dup-entity", "act-dup", checksum="fixed-checksum")
    first = storage.store(entry)
    second = storage.store(entry)
    assert first == second
    with psycopg.connect(main_dsn) as conn:
        count = conn.execute(
            "SELECT count(*) FROM fact.provenance_entry WHERE entity_id = %s",
            ("dup-entity",),
        ).fetchone()[0]
    assert count == 1  # 重复注册不产生无法解释的重复链


def test_concurrent_writes_no_fork_no_gap(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            local = PostgresProvenanceStorage(main_dsn)
            for i in range(5):
                local.store(SimpleEntry(f"conc-{worker_id}-{i}", f"act-{worker_id}-{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors

    with psycopg.connect(main_dsn) as conn:
        rows = conn.execute(
            "SELECT sequence_id, previous_checksum, checksum "
            "FROM fact.provenance_entry ORDER BY sequence_id"
        ).fetchall()
    sequences = [r[0] for r in rows]
    assert len(sequences) == 20
    assert len(set(sequences)) == 20  # 无重复序号
    for i in range(1, len(rows)):
        assert rows[i][1] == rows[i - 1][2]  # 无断链/分叉
    assert storage.verify_chain() == []


def test_crashed_transaction_leaves_chain_intact(main_dsn, monkeypatch) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    storage.store(SimpleEntry("before-crash", "act-0"))

    original_execute = psycopg.Cursor.execute

    def exploding_execute(self, query, *args, **kwargs):
        if isinstance(query, str) and "INSERT INTO fact.provenance_entry" in query:
            raise RuntimeError("simulated crash mid-insert")
        return original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", exploding_execute)
    with pytest.raises(RuntimeError):
        storage.store(SimpleEntry("crashed", "act-crash"))
    monkeypatch.setattr(psycopg.Cursor, "execute", original_execute)

    # 崩溃后：无残留记录、链未断
    assert storage.retrieve("crashed") is None
    assert storage.verify_chain() == []


def test_clear_requires_explicit_admin_mode(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    storage.store(SimpleEntry("protected", "act-1"))
    with pytest.raises(ProvenancePermissionError):
        storage.clear()  # 正常业务路径不可清空
    # 管理模式显式开启后才可用（显式人工操作）
    admin = PostgresProvenanceStorage(main_dsn, admin_mode=True)
    assert admin.clear() == 1


def test_descendants_traced(main_dsn) -> None:
    storage = PostgresProvenanceStorage(main_dsn)
    storage.store(SimpleEntry("parent-1", "act-1"))
    storage.store(SimpleEntry("child-1", "act-2", parent_entity_id="parent-1"))
    storage.store(SimpleEntry("child-2", "act-3", parent_entity_id="parent-1"))
    descendants = storage.trace_descendants("parent-1")
    assert {r["entity_id"] for r in descendants} == {"child-1", "child-2"}


def test_adapter_full_provenance_path_against_postgres(main_dsn) -> None:
    """端口全链路：Adapter.register_provenance → PG 存储 → trace_lineage。"""
    storage = PostgresProvenanceStorage(main_dsn)
    adapter = SemanticaRuntimeAdapter(shapes_path=SHAPES, provenance=storage)

    record = adapter.register_provenance(ProvenanceInput(
        entity_id="claim-42", entity_type="Claim", activity_id="accept-42",
        agent_id="review-worker", source_document="cninfo:ann-1",
        used_entities=("evidence-1", "claim-42"),
        metadata={"run": "r1"},
    ))
    assert record.entity_id == "claim-42"
    assert record.checksum and record.sequence_id >= 1

    adapter.register_provenance(ProvenanceInput(
        entity_id="claim-43", entity_type="Claim", activity_id="accept-43",
        parent_entity_id="claim-42",
    ))
    lineage = adapter.trace_lineage("claim-43")
    assert [r.entity_id for r in lineage] == ["claim-43", "claim-42"]
    assert lineage[0].previous_checksum is not None
    assert storage.verify_chain() == []


def test_adapter_conflicts_available_without_storage(main_dsn) -> None:
    """冲突/校验能力不依赖 Provenance 存储（能力解耦）。"""
    adapter = SemanticaRuntimeAdapter(shapes_path=SHAPES, provenance=None)
    findings = adapter.detect_conflicts([
        {"id": "c1", "subject_entity_id": "co", "predicate_code": "PRODUCES",
         "claim_status": "ACCEPTED", "business_stage": "MASS_PRODUCTION",
         "valid_from": "2024-01-01", "valid_to": "2026-01-01"},
        {"id": "c2", "subject_entity_id": "co", "predicate_code": "PRODUCES",
         "claim_status": "NEEDS_REVIEW", "evidence_state": "COMPANY_DENIAL",
         "valid_from": "2025-01-01", "valid_to": "2026-01-01"},
    ])
    assert any(f.kind == ConflictKind.TEMPORAL_OVERLAP for f in findings)
    with pytest.raises(SemanticCapabilityError):
        adapter.register_provenance(ProvenanceInput(
            entity_id="e", entity_type="Claim", activity_id="a"
        ))
