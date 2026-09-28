"""FakeSemanticRuntime：业务单元测试用的端口实现（不依赖 Semantica/DB）。

行为契约：与 SemanticaRuntimeAdapter 同满足 SemanticRuntime 端口；
可脚本化校验/冲突/溯源结果。业务测试绝不应导入 semantica。
"""

from __future__ import annotations

from typing import Any

from src.semantic.ports import (
    ConflictFinding,
    GraphQueryRequest,
    LineageRecord,
    ProvenanceInput,
    SemanticCapabilityError,
    TemporalFact,
    ValidationReport,
)


class FakeSemanticRuntime:
    """端口一致性替身：tests/unit/test_semantic_port.py 校验其满足协议。"""

    def __init__(self) -> None:
        self._reports: list[ValidationReport] = []
        self._conflicts: list[ConflictFinding] = []
        self._lineage: dict[str, list[LineageRecord]] = {}
        self._query_results: list[dict[str, Any]] = []
        self.graph_backend_configured = True
        self.provenance_configured = True

    # ---- 脚本化 ----

    def queue_report(self, report: ValidationReport) -> None:
        self._reports.append(report)

    def queue_conflicts(self, findings: list[ConflictFinding]) -> None:
        self._conflicts.extend(findings)

    def queue_lineage(self, entity_id: str, records: list[LineageRecord]) -> None:
        self._lineage.setdefault(entity_id, []).extend(records)

    def queue_query_result(self, rows: list[dict[str, Any]]) -> None:
        self._query_results.append(rows)

    # ---- 端口实现 ----

    def validate_ontology(self, ttl_paths: list[str]) -> ValidationReport:
        return self._reports.pop(0) if self._reports else ValidationReport(conforms=True)

    def validate_claim(self, claim_graph: Any) -> ValidationReport:
        return self._reports.pop(0) if self._reports else ValidationReport(conforms=True)

    def validate_graph(self, graph: Any) -> ValidationReport:
        return self._reports.pop(0) if self._reports else ValidationReport(conforms=True)

    def detect_conflicts(self, claims: list[dict[str, Any]]) -> list[ConflictFinding]:
        return list(self._conflicts)

    def register_provenance(self, entry: ProvenanceInput) -> LineageRecord:
        if not self.provenance_configured:
            raise SemanticCapabilityError("provenance storage not configured")
        record = LineageRecord(
            entity_id=entry.entity_id,
            entity_type=entry.entity_type,
            activity_id=entry.activity_id,
            checksum=entry.chain_key(),
            sequence_id=len(self._lineage) + 1,
            previous_checksum=None,
        )
        self._lineage.setdefault(entry.entity_id, []).append(record)
        return record

    def trace_lineage(
        self, entity_id: str, *, max_depth: int | None = None
    ) -> list[LineageRecord]:
        if not self.provenance_configured:
            raise SemanticCapabilityError("provenance storage not configured")
        records = self._lineage.get(entity_id, [])
        return records[:max_depth] if max_depth is not None else records

    def query_graph(self, request: GraphQueryRequest) -> dict[str, Any]:
        rows = self._query_results.pop(0) if self._query_results else []
        return {"template": request.template.value, "explain": False,
                "rows": rows, "count": len(rows)}

    def explain_path(self, request: GraphQueryRequest) -> dict[str, Any]:
        rows = self._query_results.pop(0) if self._query_results else []
        return {"template": request.template.value, "explain": True,
                "rows": rows, "count": len(rows)}

    def fact_to_bitemporal(self, fact: TemporalFact) -> Any:
        return fact  # Fake 不做框架映射，原样返回

    def bitemporal_to_fact(self, bifact: Any, *, subject: str, predicate: str) -> TemporalFact:
        return bifact  # type: ignore[return-value]
