"""PostgresProvenanceStorage：持久化 Provenance（issue #5）。

实现 semantica.provenance.ProvenanceStorage 的结构化协议（无需导入 semantica，
鸭子类型即可），权威存储为 fact.provenance_entry（#2 迁移）。

关键不变量：
- Hash 链：sequence_id 单调、previous_checksum 指向链头，写入用事务级
  advisory lock 串行化（并发无分叉、无重复序号）；
- 崩溃安全：链头锁 + 插入同事务 → 中途崩溃不留"声称完整但断链"的记录；
- 幂等：同 (entity_id, activity_id, checksum) 重复注册返回既有记录，
  不产生无法解释的重复链；
- clear() 破坏性操作默认拒绝（管理权限显式开启），正常业务路径不可调用；
- 进程重启后 lineage 仍可读取（持久化，不依赖内存缓存）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from psycopg.types.json import Json

# 与 semantica 管理器对齐的链全局 advisory lock 键
CHAIN_LOCK_KEY = 0x50524F56  # "PROV"

_COLUMNS = (
    "id, entity_id, entity_type, activity_id, agent_id, source_location, "
    "source_quote, parent_entity_id, used_entities, confidence, checksum, "
    "sequence_id, previous_checksum, metadata, created_at"
)


class ProvenancePermissionError(Exception):
    """破坏性操作被拒绝：需要显式管理授权。"""


class PostgresProvenanceStorage:
    """fact.provenance_entry 的协议实现（结构化满足 ProvenanceStorage）。"""

    def __init__(self, dsn: str, *, admin_mode: bool = False) -> None:
        self._dsn = dsn
        self._admin_mode = admin_mode

    # ---- 连接管理 ----

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn)

    # ---- 写入 ----

    def store(self, entry: Any) -> Any:
        """存储 Provenance 条目；幂等 + 链原子推进。

        entry：具有 ProvenanceEntry 结构特征的对象（entity_id/entity_type/
        activity_id/agent_id/source_document/source_quote/...）。为避免在本层
        导入框架类型，通过 getattr 读取字段。
        """
        entity_id = str(entry.entity_id)
        entity_type = str(getattr(entry, "entity_type", "") or "")
        activity_id = str(getattr(entry, "activity_id", "") or "")
        agent_id = str(getattr(entry, "agent_id", "") or "")
        source_document = getattr(entry, "source_document", None)
        source_quote = getattr(entry, "source_quote", None)
        source_location = getattr(entry, "source_location", None)
        parent_entity_id = getattr(entry, "parent_entity_id", None)
        used = list(getattr(entry, "used_entities", None) or [])
        confidence = getattr(entry, "confidence", None)
        checksum = getattr(entry, "checksum", None)
        previous_checksum = getattr(entry, "previous_checksum", None)
        metadata = dict(getattr(entry, "metadata", None) or {})
        if source_document:
            metadata.setdefault("source_document", str(source_document))
        valid_from = getattr(entry, "valid_from", None)
        valid_until = getattr(entry, "valid_until", None)
        if valid_from:
            metadata.setdefault("valid_from", str(valid_from))
        if valid_until:
            metadata.setdefault("valid_until", str(valid_until))
        supersedes = getattr(entry, "supersedes", None)
        if supersedes:
            metadata.setdefault("supersedes", str(supersedes))

        if not checksum:
            checksum = self._entry_checksum(
                entity_id, activity_id, agent_id, sorted(used), metadata
            )

        with self._connect() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_LOCK_KEY,))
                # 幂等：同 (entity, activity, checksum) 已存在 → 返回既有
                existing = conn.execute(
                    "SELECT id, entity_id, entity_type, activity_id, agent_id, source_quote, "
                    "parent_entity_id, used_entities, confidence, checksum, sequence_id, "
                    "previous_checksum, metadata, created_at "
                    "FROM fact.provenance_entry "
                    "WHERE entity_id = %s AND activity_id = %s AND checksum = %s",
                    (entity_id, activity_id, checksum),
                ).fetchone()
                if existing is not None:
                    return self._row_to_record(existing)
                head = conn.execute(
                    "SELECT sequence_id, checksum FROM fact.provenance_entry "
                    "ORDER BY sequence_id DESC NULLS LAST LIMIT 1 FOR UPDATE"
                ).fetchone()
                next_seq = (head[0] + 1) if head and head[0] is not None else 1
                if previous_checksum is None and head:
                    previous_checksum = head[1]
                row = conn.execute(
                    """
                    INSERT INTO fact.provenance_entry
                        (entity_id, entity_type, activity_id, agent_id,
                         source_location, source_quote, parent_entity_id,
                         used_entities, confidence, checksum, sequence_id,
                         previous_checksum, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, entity_id, entity_type, activity_id, agent_id,
                              source_quote, parent_entity_id, used_entities,
                              confidence, checksum, sequence_id,
                              previous_checksum, metadata, created_at
                    """,
                    (
                        entity_id, entity_type, activity_id, agent_id,
                        source_location, source_quote, parent_entity_id,
                        Json(used), confidence, checksum, next_seq,
                        previous_checksum, Json(metadata),
                    ),
                ).fetchone()
                # 显式序号写入后同步 BIGSERIAL 计数器：保证 #2 仓储默认路径
                # （ProvenanceRepository.insert）与本存储不会产生重复序号
                conn.execute(
                    "SELECT setval(pg_get_serial_sequence('fact.provenance_entry', "
                    "'sequence_id'), COALESCE((SELECT max(sequence_id) "
                    "FROM fact.provenance_entry), 1))"
                )
        return self._row_to_record(row)

    # ---- 读取 ----

    def _row_to_record(self, row: tuple) -> dict[str, Any]:
        return {
            "id": row[0], "entity_id": row[1], "entity_type": row[2],
            "activity_id": row[3], "agent_id": row[4],
            "source_quote": row[5], "parent_entity_id": row[6],
            "used_entities": row[7], "confidence": row[8],
            "checksum": row[9], "sequence_id": row[10],
            "previous_checksum": row[11], "metadata": row[12],
            "created_at": row[13],
        }

    def retrieve(self, entity_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, entity_id, entity_type, activity_id, agent_id, source_quote, "
                "parent_entity_id, used_entities, confidence, checksum, sequence_id, "
                "previous_checksum, metadata, created_at "
                "FROM fact.provenance_entry WHERE entity_id = %s "
                "ORDER BY sequence_id DESC NULLS LAST LIMIT 1",
                (entity_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def retrieve_all(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT id, entity_id, entity_type, activity_id, agent_id, source_quote, "
            "parent_entity_id, used_entities, confidence, checksum, sequence_id, "
            "previous_checksum, metadata, created_at FROM fact.provenance_entry"
        )
        params: tuple[Any, ...] = ()
        if entity_type:
            query += " WHERE entity_type = %s"
            params = (entity_type,)
        query += " ORDER BY sequence_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def trace_lineage(self, entity_id: str, max_depth: int | None = None) -> list[dict]:
        """沿 parent_entity_id 向上游回溯（持久化 lineage，重启可读）。"""
        chain: list[dict[str, Any]] = []
        current_id: str | None = entity_id
        depth = 0
        while current_id and (max_depth is None or depth < max_depth):
            record = self.retrieve(current_id)
            if record is None:
                break
            chain.append(record)
            current_id = record["parent_entity_id"]
            depth += 1
        return chain

    def trace_descendants(self, entity_id: str, max_depth: int | None = None) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, entity_id, entity_type, activity_id, agent_id, source_quote, "
                "parent_entity_id, used_entities, confidence, checksum, sequence_id, "
                "previous_checksum, metadata, created_at "
                "FROM fact.provenance_entry WHERE parent_entity_id = %s "
                "ORDER BY sequence_id",
                (entity_id,),
            ).fetchall()
        if max_depth is not None:
            rows = rows[:max_depth]
        return [self._row_to_record(row) for row in rows]

    def get_chain_head(self, conn: Any = None) -> tuple[int, str] | None:
        own = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT sequence_id, checksum FROM fact.provenance_entry "
                "ORDER BY sequence_id DESC NULLS LAST LIMIT 1"
            ).fetchone()
        finally:
            if own:
                conn.close()
        return (row[0], row[1]) if row else None

    # ---- 校验 ----

    def verify_chain(self) -> list[int]:
        """校验 Hash 链完整性；返回断链的 sequence_id 列表（空=通过）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sequence_id, checksum, previous_checksum "
                "FROM fact.provenance_entry ORDER BY sequence_id"
            ).fetchall()
        broken: list[int] = []
        expected_prev: str | None = None
        for seq, checksum, previous in rows:
            if previous != expected_prev:
                broken.append(seq)
            expected_prev = checksum
        return broken

    # ---- 协议占位与破坏性操作 ----

    def transaction(self):  # pragma: no cover - 协议占位：项目路径使用显式事务
        raise NotImplementedError("use explicit connections managed by UnitOfWork")

    def savepoint(self, conn: Any = None):  # pragma: no cover
        raise NotImplementedError("savepoints are managed by UnitOfWork")

    def clear(self) -> int:
        """破坏性清空：默认拒绝；仅 admin_mode 显式开启时可用（不可逆人工操作）。"""
        if not self._admin_mode:
            raise ProvenancePermissionError(
                "provenance clear() requires explicit admin_mode; "
                "normal business paths must never clear provenance"
            )
        with self._connect() as conn:
            count = conn.execute("SELECT count(*) FROM fact.provenance_entry").fetchone()[0]
            conn.execute("TRUNCATE fact.provenance_entry RESTART IDENTITY")
        return count

    @staticmethod
    def _entry_checksum(
        entity_id: str,
        activity_id: str,
        agent_id: str,
        used_entities: list[str],
        metadata: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "entity_id": entity_id,
                "activity_id": activity_id,
                "agent_id": agent_id,
                "used_entities": used_entities,
                "metadata": metadata,
            },
            # 纯内容校验和：不含时间戳——幂等注册依赖确定性
            sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()
