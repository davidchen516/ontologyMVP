"""SemanticaRuntimeAdapter（ADR-0003 / issue #5）。

本文件与 tests/semantic/ 的契约测试是全仓库仅允许导入 semantica 的位置
（tests/unit/test_semantica_boundary.py 静态强制）。

职责边界：
- 把 Semantica 0.7.0 的 Ontology/SHACL/冲突能力包装成 SemanticRuntime 端口；
- 业务层只见端口类型（ValidationReport/ConflictFinding/LineageRecord/...），
  绝不见框架内部类型；
- Provenance 权威存储是 PostgreSQL（PostgresProvenanceStorage 注入）；
  未注入存储时能力必须明确不可用，绝不回退 InMemoryProvenance；
- 图查询只接受白名单模板 + 参数，不透传自由 Cypher；后端不可达 →
  SemanticCapabilityError，绝不吞异常报成功。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# ---- 以下 semantica 导入仅存在于本适配层 ----
import semantica
from semantica.kg import BiTemporalFact, TemporalBound
from semantica.ontology import OntologyEngine
from semantica.provenance import ProvenanceEntry

from src.semantic.ontology_service import load_graphs, run_quality_gate
from src.semantic.pg_provenance import PostgresProvenanceStorage
from src.semantic.ports import (
    ConflictFinding,
    ConflictKind,
    GraphQueryRequest,
    LineageRecord,
    PortViolation,
    ProvenanceInput,
    QueryTemplate,
    SemanticCapabilityError,
    TemporalFact,
    ValidationReport,
)

SEMANTICA_LOCKED_VERSION = "0.7.0"

# 白名单图查询模板：只接受模板名 + 参数绑定
CYPHER_TEMPLATES: dict[QueryTemplate, str] = {
    QueryTemplate.COMPANIES_BY_CONCEPT:
        "MATCH (c:Company)-[:TAGGED_AS]->(k:Concept {canonical_key: $concept}) "
        "RETURN c.canonical_key AS company",
    QueryTemplate.SUPPLY_CHAIN_NEIGHBORS:
        "MATCH path = (c:Company {canonical_key: $company})"
        "-[:PRODUCES|SUPPLIES_TO*1..3]-(n) "
        "RETURN [r IN relationships(path) | type(r)] AS rels, n.canonical_key AS neighbor",
    QueryTemplate.CLAIMS_FOR_SUBJECT:
        "MATCH (c:Company {canonical_key: $company})<-[rel:CLAIM {predicate_code: $predicate}]-(k) "
        "RETURN k.claim_id AS claim",
}

_CONFLICT_KIND_MAP = {
    "VALUE_CONFLICT": ConflictKind.VALUE,
    "TYPE_CONFLICT": ConflictKind.TYPE,
    "RELATIONSHIP_CONFLICT": ConflictKind.RELATION,
    "TEMPORAL_CONFLICT": ConflictKind.TEMPORAL_OVERLAP,
    "LOGICAL_CONFLICT": ConflictKind.LOGICAL,
}


def verify_semantica_version() -> None:
    """契约锁定：Semantica 版本漂移必须立刻暴露（升级须独立提交+契约测试）。"""
    actual = str(getattr(semantica, "__version__", ""))
    if actual != SEMANTICA_LOCKED_VERSION:
        raise SemanticCapabilityError(
            f"semantica version drift: expected {SEMANTICA_LOCKED_VERSION}, got {actual}"
        )


class SemanticaRuntimeAdapter:
    """SemanticRuntime 的 Semantica 0.7.0 实现。"""

    def __init__(
        self,
        *,
        shapes_path: str,
        provenance: PostgresProvenanceStorage | None,
        graph_query_executor: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None = None,
    ) -> None:
        verify_semantica_version()
        self._shapes_path = shapes_path
        # 生产不变量：无持久化 Provenance 存储 → 能力不可用，绝不回退内存实现
        self._provenance = provenance
        self._graph_query_executor = graph_query_executor
        self._engine: OntologyEngine | None = None
        self._engine_lock = threading.Lock()

    # ---- 引擎生命周期：进程内单例，不允许每请求新建 ----

    def _ontology_engine(self) -> OntologyEngine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = OntologyEngine()
        return self._engine

    # ---- 校验 ----

    def validate_ontology(self, ttl_paths: list[str]) -> ValidationReport:
        """语法 + 项目质量门禁 + 框架 SHACL 交叉校验，问题结构化合并。"""
        try:
            graph = load_graphs(ttl_paths)
        except Exception as exc:  # noqa: BLE001 - OntologyQualityError 或 IO 失败
            return ValidationReport(
                conforms=False,
                violations=(PortViolation(focus="<load>", constraint="LOAD_FAILED",
                                          message=str(exc)[:200]),),
                engine="SEMANTICA_ADAPTER",
            )
        quality = run_quality_gate(graph)
        if not quality.conforms:
            return quality
        try:
            report = self._ontology_engine().validate_graph(
                graph, shacl=self._shapes_path, explain=False
            )
        except Exception as exc:  # noqa: BLE001 - 框架失败不得伪成功
            raise SemanticCapabilityError(
                f"ontology engine failure: {type(exc).__name__}: {str(exc)[:160]}"
            ) from exc
        return self._map_shacl_report(report, engine="SEMANTICA_ONTOLOGY")

    def validate_claim(self, claim_graph: Any) -> ValidationReport:
        return self._validate_data_graph(claim_graph)

    def validate_graph(self, graph: Any) -> ValidationReport:
        return self._validate_data_graph(graph)

    def _validate_data_graph(self, graph: Any) -> ValidationReport:
        try:
            report = self._ontology_engine().validate_graph(
                graph, shacl=self._shapes_path, explain=True
            )
        except Exception as exc:  # noqa: BLE001
            raise SemanticCapabilityError(
                f"claim validation engine failure: {type(exc).__name__}: {str(exc)[:160]}"
            ) from exc
        return self._map_shacl_report(report, engine="SEMANTICA_SHACL")

    @staticmethod
    def _map_shacl_report(report: Any, *, engine: str) -> ValidationReport:
        conforms = bool(getattr(report, "conforms", False))
        violations: list[PortViolation] = []
        for violation in getattr(report, "violations", None) or []:
            violations.append(
                PortViolation(
                    focus=str(getattr(violation, "focus_node", "") or ""),
                    path=str(getattr(violation, "result_path", "") or "") or None,
                    constraint=str(getattr(violation, "constraint", "") or "") or None,
                    severity=str(getattr(violation, "severity", "Violation")),
                    message=str(getattr(violation, "message", "") or "")[:300] or None,
                )
            )
        return ValidationReport(
            conforms=conforms, violations=tuple(violations), engine=engine
        )

    # ---- 冲突检测：Semantica 封装 + 项目时态/逻辑规则补充 ----

    def detect_conflicts(self, claims: list[dict[str, Any]]) -> list[ConflictFinding]:
        """冲突检测：Semantica 封装（值冲突）+ 项目规则（类型/关系/逻辑/时态）。

        框架喂参契约（semantica 0.7.0）：同一 entity_id 的实体按顶层属性值
        分组检测 → 我们把同 (subject, predicate) 的 Claim 组映射为共享
        entity_id 的实体，object/stage 等置于顶层，claim id 记入 source。
        所有来源 Claim 保留在 findings 中，检测层绝不自动裁决。
        """
        findings: list[ConflictFinding] = []
        if not claims:
            return findings

        # 按 (主体, 谓词) 分组——同一断言维度的不同来源
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for claim in claims:
            key = (str(claim.get("subject_entity_id")), str(claim.get("predicate_code")))
            groups.setdefault(key, []).append(claim)

        # 1) Semantica 值冲突（真实封装，喂参与框架分组契约对齐）
        try:
            from semantica.conflicts import ConflictDetector

            detector = ConflictDetector()
            for (subject, predicate), group in groups.items():
                entities = [
                    {
                        "entity_id": f"{subject}:{predicate}",
                        "entity_type": "ClaimGroup",
                        # 顶层属性：框架按顶层键比较值
                        "object": str(
                            claim.get("object_entity_id")
                            or claim.get("object_value")
                            or ""
                        ),
                        "source": str(claim.get("id")),
                    }
                    for claim in group
                ]
                if len({e["object"] for e in entities}) < 2:
                    continue
                for conflict in detector.detect_conflicts(entities):
                    findings.append(
                        ConflictFinding(
                            kind=_CONFLICT_KIND_MAP[conflict.conflict_type.name],
                            claim_ids=tuple(
                                str(s.get("document"))
                                for s in (conflict.sources or [])
                            ),
                            detail=(
                                f"{conflict.entity_id}.{conflict.property_name}: "
                                f"conflicting values {conflict.conflicting_values} "
                                f"(severity={conflict.severity})"
                            )[:300],
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - 框架失败不伪成功
            raise SemanticCapabilityError(
                f"conflict engine failure: {type(exc).__name__}: {str(exc)[:160]}"
            ) from exc

        # 2) 项目规则：类型/关系/逻辑/时态
        for (subject, predicate), group in groups.items():
            findings.extend(self._type_conflicts(group, subject, predicate))
        findings.extend(self._relation_conflicts(claims))
        findings.extend(self._logical_conflicts(claims))
        findings.extend(self._temporal_stage_conflicts(claims))
        return findings

    @staticmethod
    def _type_conflicts(
        group: list[dict[str, Any]], subject: str, predicate: str
    ) -> list[ConflictFinding]:
        """同 (subject, predicate) 下对象实体类型不一致 → TYPE 冲突。"""
        types = {
            str(c.get("object_entity_type") or "")
            for c in group
            if c.get("object_entity_id") is not None
        }
        if len(types) < 2:
            return []
        return [
            ConflictFinding(
                kind=ConflictKind.TYPE,
                claim_ids=tuple(str(c.get("id")) for c in group),
                detail=(
                    f"{subject}/{predicate}: conflicting object entity types "
                    f"{sorted(types)} — all sources retained"
                ),
            )
        ]

    @staticmethod
    def _relation_conflicts(claims: list[dict[str, Any]]) -> list[ConflictFinding]:
        """同 (subject, object) 被断言了多种不同关系 → RELATION 冲突。"""
        pairs: dict[tuple[str, str], set[str]] = {}
        claim_by_pair: dict[tuple[str, str], list[dict]] = {}
        for claim in claims:
            if claim.get("object_entity_id") is None:
                continue
            key = (str(claim.get("subject_entity_id")), str(claim["object_entity_id"]))
            pairs.setdefault(key, set()).add(str(claim.get("predicate_code")))
            claim_by_pair.setdefault(key, []).append(claim)
        findings: list[ConflictFinding] = []
        for key, predicates in pairs.items():
            if len(predicates) < 2:
                continue
            findings.append(
                ConflictFinding(
                    kind=ConflictKind.RELATION,
                    claim_ids=tuple(str(c.get("id")) for c in claim_by_pair[key]),
                    detail=(
                        f"{key[0]} -> {key[1]}: multiple relations {sorted(predicates)} "
                        "asserted — needs review, no auto resolution"
                    ),
                )
            )
        return findings

    @staticmethod
    def _logical_conflicts(claims: list[dict[str, Any]]) -> list[ConflictFinding]:
        """同 (subject, predicate) 同时存在明确否认与肯定断言 → LOGICAL 冲突。"""
        groups: dict[tuple[str, str], list[dict]] = {}
        for claim in claims:
            if str(claim.get("predicate_code")) == "DENIES_INVOLVEMENT":
                key = (str(claim.get("subject_entity_id")), "")
                groups.setdefault(key, []).append(claim)
            elif claim.get("business_stage"):
                key = (str(claim.get("subject_entity_id")), str(claim.get("predicate_code")))
                groups.setdefault(key, []).append(claim)
        findings: list[ConflictFinding] = []
        denials_by_subject: dict[str, list[dict]] = {}
        for claim in claims:
            if str(claim.get("predicate_code")) == "DENIES_INVOLVEMENT":
                denials_by_subject.setdefault(str(claim.get("subject_entity_id")), []).append(claim)
        affirmations = [
            c for c in claims
            if str(c.get("predicate_code")) != "DENIES_INVOLVEMENT" and c.get("business_stage")
        ]
        for subject, denial_claims in denials_by_subject.items():
            related = [c for c in affirmations if str(c.get("subject_entity_id")) == subject]
            if not related:
                continue
            findings.append(
                ConflictFinding(
                    kind=ConflictKind.LOGICAL,
                    claim_ids=tuple(
                        str(c.get("id")) for c in denial_claims + related
                    ),
                    detail=(
                        f"{subject}: explicit denial coexists with affirmative stage claims "
                        "— logical conflict, all sources retained"
                    ),
                )
            )
        return findings

    @staticmethod
    def _temporal_stage_conflicts(claims: list[dict[str, Any]]) -> list[ConflictFinding]:
        from src.domain.enums import ClaimStatus

        contradictory = {"MASS_PRODUCTION", "COMPANY_DENIAL"}
        by_subject: dict[tuple[str, str], list[dict]] = {}
        for claim in claims:
            if claim.get("claim_status") not in (
                ClaimStatus.ACCEPTED.value, ClaimStatus.NEEDS_REVIEW.value,
            ):
                continue
            stage = str(claim.get("business_stage") or claim.get("evidence_state") or "")
            if stage not in contradictory:
                continue
            key = (str(claim.get("subject_entity_id")), str(claim.get("predicate_code")))
            by_subject.setdefault(key, []).append(claim)

        findings: list[ConflictFinding] = []
        for (subject, predicate), group in by_subject.items():
            stages = {str(c.get("business_stage") or c.get("evidence_state")) for c in group}
            if len(stages) < 2:
                continue
            overlapping = SemanticaRuntimeAdapter._has_temporal_overlap(group)
            if overlapping:
                findings.append(
                    ConflictFinding(
                        kind=ConflictKind.TEMPORAL_OVERLAP,
                        claim_ids=tuple(str(c.get("id")) for c in group),
                        detail=(
                            f"contradictory stages {sorted(stages)} on {subject}/{predicate} "
                            "with overlapping validity — all sources retained, no auto resolution"
                        ),
                    )
                )
        return findings

    # ---- Provenance：PostgreSQL 权威存储 ----

    @staticmethod
    def _overlap_span(value: Any) -> Any:
        """把日期值归一为可比较 datetime（缺省为开区间端点）。"""
        import datetime as _dt

        if value is None:
            return None
        if isinstance(value, _dt.datetime):
            return value
        try:
            parsed = _dt.datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed

    @classmethod
    def _has_temporal_overlap(cls, claims: list[dict[str, Any]]) -> bool:
        """[valid_from, valid_to) 半开区间两两相交判定（None 端点=开放）。"""
        import datetime as _dt

        low = _dt.datetime.min.replace(tzinfo=_dt.UTC)
        high = _dt.datetime.max.replace(tzinfo=_dt.UTC)
        spans: list[tuple[_dt.datetime, _dt.datetime]] = []
        for claim in claims:
            start = cls._overlap_span(claim.get("valid_from"))
            end = cls._overlap_span(claim.get("valid_to"))
            # 字符串日期缺时区时按 UTC 处理
            if start is not None and start.tzinfo is None:
                start = start.replace(tzinfo=_dt.UTC)
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=_dt.UTC)
            spans.append((start or low, end or high))
        spans.sort()
        for i in range(1, len(spans)):
            if spans[i][0] < spans[i - 1][1]:  # 半开区间相交（边界相接不算重叠）
                return True
        return False

    def register_provenance(self, entry: ProvenanceInput) -> LineageRecord:
        if self._provenance is None:
            raise SemanticCapabilityError(
                "provenance storage not configured; production must never fall "
                "back to InMemoryProvenance"
            )
        semantica_entry = ProvenanceEntry(
            entity_id=entry.entity_id,
            entity_type=entry.entity_type,
            activity_id=entry.activity_id,
            agent_id=entry.agent_id,
            source_document=entry.source_document or "ontology-mvp",
            source_location=entry.source_location,
            source_quote=entry.source_quote,
            confidence=entry.confidence if entry.confidence is not None else 1.0,
            parent_entity_id=entry.parent_entity_id,
            used_entities=list(entry.used_entities),
            checksum=entry.chain_key(),
            metadata=dict(entry.metadata),
        )
        stored = self._provenance.store(semantica_entry)
        if stored is None:
            raise SemanticCapabilityError(f"provenance write failed for {entry.entity_id}")
        return self._as_lineage(stored)

    def trace_lineage(
        self, entity_id: str, *, max_depth: int | None = None
    ) -> list[LineageRecord]:
        if self._provenance is None:
            raise SemanticCapabilityError("provenance storage not configured")
        return [
            self._as_lineage(record)
            for record in self._provenance.trace_lineage(entity_id, max_depth)
        ]

    @staticmethod
    def _as_lineage(record: dict[str, Any]) -> LineageRecord:
        used = record.get("used_entities") or []
        return LineageRecord(
            entity_id=str(record.get("entity_id") or ""),
            entity_type=record.get("entity_type"),
            activity_id=record.get("activity_id"),
            checksum=record.get("checksum"),
            sequence_id=record.get("sequence_id"),
            previous_checksum=record.get("previous_checksum"),
            parent_entity_id=record.get("parent_entity_id"),
            used_entities=tuple(used) if isinstance(used, list) else (),
            metadata=dict(record.get("metadata") or {}),
        )

    # ---- 受控图查询：白名单模板 + 参数 ----

    def query_graph(self, request: GraphQueryRequest) -> dict[str, Any]:
        return self._run_graph_query(request, explain=False)

    def explain_path(self, request: GraphQueryRequest) -> dict[str, Any]:
        return self._run_graph_query(request, explain=True)

    def _run_graph_query(self, request: GraphQueryRequest, *, explain: bool) -> dict[str, Any]:
        if self._graph_query_executor is None:
            raise SemanticCapabilityError(
                "graph backend not configured — controlled query interface unavailable"
            )
        template = CYPHER_TEMPLATES.get(request.template)
        if template is None:
            raise SemanticCapabilityError(
                f"query template {request.template!r} is not whitelisted; "
                "free-form Cypher is rejected"
            )
        try:
            rows = self._graph_query_executor(template, dict(request.params))
        except SemanticCapabilityError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后端不可达/失败 → 分类错误
            raise SemanticCapabilityError(
                f"graph backend failure: {type(exc).__name__}: {str(exc)[:160]}"
            ) from exc
        return {
            "template": request.template.value,
            "explain": explain,
            "rows": list(rows),
            "count": len(rows),
        }

    # ---- 双时态映射（TemporalFactMapper）：项目字段 ↔ BiTemporalFact ----
    # BiTemporalFact 仅携带时间维度（无 subject/predicate）；主体/谓词留在项目类型上。
    # 语义映射：valid_to=None（开放结束）↔ None；superseded_at=None（未替代）
    # ↔ TemporalBound.OPEN（框架不接受 None）。

    @staticmethod
    def fact_to_bitemporal(fact: TemporalFact) -> BiTemporalFact:
        """项目双时态字段 → Semantica BiTemporalFact。"""
        return BiTemporalFact(
            valid_from=fact.valid_from,
            valid_until=fact.valid_to,
            recorded_at=fact.recorded_at,
            superseded_at=fact.superseded_at if fact.superseded_at is not None
            else TemporalBound.OPEN,
        )

    @staticmethod
    def bitemporal_to_fact(bifact: BiTemporalFact, *, subject: str, predicate: str) -> TemporalFact:
        """BiTemporalFact → 项目双时态字段（往返后四字段语义不得变化）。"""
        valid_to = bifact.valid_until
        if valid_to is TemporalBound.OPEN:
            valid_to = None
        superseded = bifact.superseded_at
        if superseded is TemporalBound.OPEN:
            superseded = None
        return TemporalFact(
            subject=subject,
            predicate=predicate,
            valid_from=bifact.valid_from,
            valid_to=valid_to,
            recorded_at=bifact.recorded_at,
            superseded_at=superseded,
        )
