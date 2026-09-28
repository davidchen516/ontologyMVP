"""SemanticRuntime 端口层（ADR-0003 / issue #5）。

业务代码只依赖本模块定义的端口与数据类型，绝不接触 Semantica 内部类型。
唯一豁免：`semantica_adapter.py` 及其契约测试（tests/semantic/）可导入 semantica。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class SemanticCapabilityError(Exception):
    """语义能力不可用（如存储/图后端未配置或不可达）。

    框架调用失败不得返回伪成功；调用方必须把该错误当作能力不可用处理。
    """


class OntologyQualityError(Exception):
    """本体质量门禁失败：结构化问题随异常携带。"""

    def __init__(self, issues: list[PortViolation]) -> None:
        super().__init__("ontology quality gate failed")
        self.issues = issues


@dataclass(frozen=True)
class PortViolation:
    """标准化校验违规：不暴露框架内部结构。"""

    focus: str
    path: str | None = None
    constraint: str | None = None
    severity: str = "Violation"
    message: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    conforms: bool
    violations: tuple[PortViolation, ...] = ()
    engine: str = "SHACL"

    def first_message(self) -> str | None:
        return self.violations[0].message if self.violations else None


class ConflictKind(StrEnum):
    VALUE = "VALUE"
    TYPE = "TYPE"
    RELATION = "RELATION"
    TEMPORAL_OVERLAP = "TEMPORAL_OVERLAP"
    LOGICAL = "LOGICAL"


@dataclass(frozen=True)
class ConflictFinding:
    """冲突保留所有来源 Claim，不做唯一真值裁决。"""

    kind: ConflictKind
    claim_ids: tuple[str, ...]
    detail: str
    # 冲突只能报告，绝不在检测层自动选择一方
    auto_resolved: bool = False


@dataclass(frozen=True)
class TemporalFact:
    """项目双时态事实（与 fact.claim 的字段语义一一对应）。

    开放结束时间用 valid_to=None 表示；superseded_at=None 表示未替代。
    """

    subject: str
    predicate: str
    valid_from: dt.datetime | None
    valid_to: dt.datetime | None
    recorded_at: dt.datetime
    superseded_at: dt.datetime | None = None


@dataclass(frozen=True)
class LineageRecord:
    entity_id: str
    entity_type: str | None
    activity_id: str | None
    checksum: str | None
    sequence_id: int | None
    previous_checksum: str | None
    parent_entity_id: str | None = None
    used_entities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProvenanceInput:
    """注册 Provenance 的端口入参（与存储实现解耦）。"""

    entity_id: str
    entity_type: str
    activity_id: str
    agent_id: str = "ontology-mvp"
    source_document: str | None = None
    source_quote: str | None = None
    source_location: str | None = None
    parent_entity_id: str | None = None
    used_entities: tuple[str, ...] = ()
    confidence: float | None = None
    valid_from: dt.datetime | None = None
    valid_until: dt.datetime | None = None
    supersedes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def chain_key(self) -> str:
        """Hash 链覆盖的字段（canonical 序列化）。"""
        import hashlib
        import json

        payload = {
            "entity_id": self.entity_id,
            "activity_id": self.activity_id,
            "agent_id": self.agent_id,
            "used": sorted(self.used_entities),
            "parent": self.parent_entity_id,
            "meta": self.metadata,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode()
        ).hexdigest()


class QueryTemplate(StrEnum):
    """受控图查询白名单：只接受模板名 + 参数，不接受自由 Cypher。"""

    COMPANIES_BY_CONCEPT = "companies_by_concept"
    SUPPLY_CHAIN_NEIGHBORS = "supply_chain_neighbors"
    CLAIMS_FOR_SUBJECT = "claims_for_subject"


@dataclass(frozen=True)
class GraphQueryRequest:
    template: QueryTemplate
    params: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SemanticRuntime(Protocol):
    """业务层唯一的语义运行时端口（ADR-0003）。

    实现方：SemanticaRuntimeAdapter（生产）、FakeSemanticRuntime（业务单测）。
    破坏性操作（clear 等）刻意不在端口上暴露。
    """

    def validate_ontology(self, ttl_paths: list[str]) -> ValidationReport: ...

    def validate_claim(self, claim_graph: Any) -> ValidationReport: ...

    def validate_graph(self, graph: Any) -> ValidationReport: ...

    def detect_conflicts(self, claims: list[dict[str, Any]]) -> list[ConflictFinding]: ...

    def register_provenance(self, entry: ProvenanceInput) -> LineageRecord: ...

    def trace_lineage(
        self, entity_id: str, *, max_depth: int | None = None
    ) -> list[LineageRecord]: ...

    def query_graph(self, request: GraphQueryRequest) -> dict[str, Any]: ...

    def explain_path(self, request: GraphQueryRequest) -> dict[str, Any]: ...


__all__ = [
    "ConflictFinding",
    "ConflictKind",
    "GraphQueryRequest",
    "LineageRecord",
    "OntologyQualityError",
    "PortViolation",
    "ProvenanceInput",
    "QueryTemplate",
    "SemanticCapabilityError",
    "SemanticRuntime",
    "TemporalFact",
    "ValidationReport",
]
