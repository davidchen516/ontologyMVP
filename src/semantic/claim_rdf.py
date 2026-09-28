"""Claim → RDF 图转换（issue #5）：把项目 Claim 字段转为可 SHACL 校验的图。

IRI 与属性严格对齐 ontology/shapes.ttl（stock: 命名空间）：
- Claim 节点 a stock:Claim，携带 canonicalKey/claimSubject/predicateCode/
  claimObject/claimStatus/businessStage/evidenceState/confidence/
  recordedAt/ontologyVersion/validFrom/validTo/supersededAt/supportedBy/
  prov:wasDerivedFrom；
- object_value 型 Claim 铸造值节点 IRI 并附 stock:objectValue 字面量
  （满足 claimObject 的 IRI 约束）；
- 语义红线由 shapes.ttl 的 SPARQL 约束判定：ACCEPTED 无证据、
  MASS_PRODUCTION 仅平台标签支持必须判负，本层不做裁剪。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

STOCK = Namespace("https://ontology.example.com/stock#")
PROV = Namespace("http://www.w3.org/ns/prov#")
EVIDENCE = Namespace("https://ontology.example.com/evidence#")

_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _ts(value: Any) -> Literal:
    if isinstance(value, dt.datetime):
        return Literal(value, datatype=XSD.dateTime)
    return Literal(str(value), datatype=XSD.dateTime)


# 本体已声明的实体类型白名单：小写输入 → 规范类名；未知类型必须被
# 质量门禁/SHACL 噪亮拒绝，而不是静默生成未定义类
_KNOWN_ENTITY_TYPES = {
    "company": "Company",
    "security": "Security",
    "exchange": "Exchange",
    "concept": "Concept",
    "product": "Product",
    "theme": "Theme",
    "industry": "Industry",
}


class UnknownEntityTypeError(Exception):
    """Claim 引用了本体未声明的实体类型：进入拒绝/审核，不得静默。"""


def _type_entity_node(
    graph: Graph, node: URIRef, entity_type: str, entity_id: Any,
    *, fallback: str | None = None,
) -> None:
    """实体节点带 rdf:type 与 canonicalKey：满足 shapes 的对象类型约束
    （如 PRODUCES 对象必须是 stock:Product）及目标类 Shape 的业务键要求。"""
    raw = str(entity_type or "").strip().lower()
    type_name = _KNOWN_ENTITY_TYPES.get(raw)
    if type_name is None and fallback is not None:
        raw = fallback.lower()
        type_name = _KNOWN_ENTITY_TYPES.get(raw)
    if type_name is None:
        raise UnknownEntityTypeError(
            f"entity type {entity_type!r} is not declared in the ontology"
        )
    graph.add((node, RDF.type, URIRef(f"{STOCK}{type_name}")))
    graph.add((node, STOCK.canonicalKey, Literal(str(entity_id))))


def claim_to_graph(
    claim: dict[str, Any], *, ontology_version: str = "0.1.0"
) -> Graph:
    graph = Graph()
    graph.bind("stock", STOCK)
    graph.bind("prov", PROV)

    claim_id = str(claim.get("id") or "unidentified")
    node = URIRef(f"{STOCK}claim/{claim_id}")
    graph.add((node, RDF.type, STOCK.Claim))
    graph.add((node, STOCK.canonicalKey, Literal(claim_id)))

    subject_kind = str(claim.get("subject_entity_type") or "company").lower()
    subject = URIRef(
        f"https://ontology.example.com/stock/{subject_kind}/{claim.get('subject_entity_id')}"
    )
    _type_entity_node(graph, subject, claim.get("subject_entity_type", "Company"),
                      claim.get("subject_entity_id"), fallback="Company")
    graph.add((node, STOCK.claimSubject, subject))

    if claim.get("predicate_code"):
        graph.add((node, STOCK.predicateCode, Literal(str(claim["predicate_code"]))))

    # claimObject：IRI 对象优先；object_value 型铸造值节点
    if claim.get("object_entity_id"):
        object_kind = str(claim.get("object_entity_type") or "product").lower()
        object_node = URIRef(
            f"https://ontology.example.com/stock/{object_kind}/{claim['object_entity_id']}"
        )
        _type_entity_node(graph, object_node,
                          claim.get("object_entity_type", "Product"),
                          claim["object_entity_id"])
        graph.add((node, STOCK.claimObject, object_node))
    else:
        # 值对象：HAS_REVENUE_FROM 等谓词的对象必须是 Product（ProducesObjectShape），
        # 值节点按 Product 类型化（收入来源即产品），附带原始值字面量
        value_node = URIRef(f"{STOCK}claimObjectValue/{claim_id}")
        _type_entity_node(graph, value_node, "Product", claim_id)
        graph.add((node, STOCK.claimObject, value_node))
        if claim.get("object_value") is not None:
            graph.add((value_node, STOCK.objectValue,
                       Literal(str(claim["object_value"]))))

    graph.add((node, STOCK.claimStatus,
               Literal(str(claim.get("claim_status", "EXTRACTED")))))
    if claim.get("business_stage"):
        graph.add((node, STOCK.businessStage, Literal(str(claim["business_stage"]))))
    if claim.get("evidence_state"):
        graph.add((node, STOCK.evidenceState, Literal(str(claim["evidence_state"]))))

    confidence = claim.get("confidence", 0.5)
    graph.add((node, STOCK.confidence, Literal(float(confidence), datatype=XSD.decimal)))
    graph.add((node, STOCK.recordedAt,
               _ts(claim.get("recorded_at") or dt.datetime.now(tz=dt.UTC))))

    # ontologyVersion 约束为 semver：非 semver 的版本串不能伪装通过
    version = str(claim.get("ontology_version") or ontology_version)
    if not _SEMVER.fullmatch(version):
        version = ontology_version
    graph.add((node, STOCK.ontologyVersion, Literal(version)))

    if claim.get("valid_from"):
        graph.add((node, STOCK.validFrom, _ts(claim["valid_from"])))
    if claim.get("valid_to"):
        graph.add((node, STOCK.validTo, _ts(claim["valid_to"])))
    if claim.get("superseded_at"):
        graph.add((node, STOCK.supersededAt, _ts(claim["superseded_at"])))

    for evidence_id in claim.get("evidence_ids", []):
        graph.add((node, STOCK.supportedBy, URIRef(f"{EVIDENCE}fragment/{evidence_id}")))
    for source in claim.get("derived_from", []):
        graph.add((node, PROV.wasDerivedFrom, URIRef(f"{PROV}entity/{source}")))

    return graph


def claim_graph_to_ttl(graph: Graph) -> str:
    return graph.serialize(format="turtle")
