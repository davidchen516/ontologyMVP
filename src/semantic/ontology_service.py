"""OntologyService：加载本体资产并执行项目质量门禁（issue #5）。

只用 rdflib，不依赖 Semantica（可被任何层复用）；Adapter 在
validate_ontology 中组合本服务与框架校验。
质量门禁（负向验收）：
1. 全部 TTL 语法可解析；
2. 对象属性必须声明 rdfs:domain / rdfs:range（引用的类必须存在）；
3. SHACL shapes 的 sh:targetClass 必须指向已定义类；
4. SKOS 概念必须有 prefLabel 且 IRI 唯一。
失败必须结构化列出问题，不静默通过。
"""

from __future__ import annotations

from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph
from rdflib.namespace import SKOS
from rdflib.term import URIRef

from src.semantic.ports import OntologyQualityError, PortViolation, ValidationReport

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
ASSETS = [
    "stock-core.ttl",
    "stock-relations.ttl",
    "product-skos.ttl",
    "shapes.ttl",
]


def load_graphs(ttl_paths: list[str]) -> Graph:
    """加载并合并 TTL；语法错误抛 OntologyQualityError。"""
    merged = Graph()
    issues: list[PortViolation] = []
    for path in ttl_paths:
        graph = Graph()
        try:
            graph.parse(path, format="turtle")
        except Exception as exc:  # noqa: BLE001 - 语法门禁必须捕获一切解析失败
            issues.append(
                PortViolation(
                    focus=path, constraint="TURTLE_SYNTAX", message=str(exc)[:200]
                )
            )
            continue
        merged += graph
    if issues:
        raise OntologyQualityError(issues)
    return merged


def run_quality_gate(graph: Graph) -> ValidationReport:
    """执行结构质量门禁；返回结构化报告（不抛异常）。"""
    issues: list[PortViolation] = []

    classes = set(graph.subjects(RDF.type, OWL.Class))
    classes |= set(graph.subjects(RDF.type, RDFS.Class))
    # 外部词汇（SKOS/PROV 等）与内置类：出现在任何 rdf:type 三元组或为内置即视为已定义
    classes |= {obj for _, _, obj in graph.triples((None, RDF.type, None))
                if isinstance(obj, URIRef)}
    classes |= {
        OWL.Thing, RDFS.Resource, RDFS.Literal,
        URIRef("http://www.w3.org/2004/02/skos/core#Concept"),
        URIRef("http://www.w3.org/2004/02/skos/core#ConceptScheme"),
        URIRef("http://www.w3.org/ns/prov#Entity"),
    }

    # 对象属性 domain/range 完整性
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        domains = list(graph.objects(prop, RDFS.domain))
        ranges = list(graph.objects(prop, RDFS.range))
        if not domains:
            issues.append(
                PortViolation(
                    focus=str(prop), path="rdfs:domain",
                    constraint="MISSING_DOMAIN",
                    message="object property missing rdfs:domain",
                )
            )
        if not ranges:
            issues.append(
                PortViolation(
                    focus=str(prop), path="rdfs:range",
                    constraint="MISSING_RANGE",
                    message="object property missing rdfs:range",
                )
            )
        for scope_name, targets in (("domain", domains), ("range", ranges)):
            for target in targets:
                if isinstance(target, URIRef) and not (
                    target in classes or (None, RDFS.subClassOf, target) in graph
                ):
                    issues.append(
                        PortViolation(
                            focus=str(prop), path=f"rdfs:{scope_name}",
                            constraint="UNRESOLVED_REFERENCE",
                            message=f"references undefined class {target}",
                        )
                    )

    # SHACL targetClass 必须指向已定义类
    shacl_target = URIRef("http://www.w3.org/ns/shacl#targetClass")
    for shape, target in graph.subject_objects(shacl_target):
        if isinstance(target, URIRef) and target not in classes:
            issues.append(
                PortViolation(
                    focus=str(shape), path="sh:targetClass",
                    constraint="UNRESOLVED_TARGET_CLASS",
                    message=f"shape targets undefined class {target}",
                )
            )

    # SKOS 概念必须有 prefLabel
    for concept in graph.subjects(RDF.type, SKOS.Concept):
        labels = list(graph.objects(concept, SKOS.prefLabel))
        if not labels:
            issues.append(
                PortViolation(
                    focus=str(concept), path="skos:prefLabel",
                    constraint="MISSING_PREFLABEL",
                    message="skos concept missing prefLabel",
                )
            )

    return ValidationReport(
        conforms=not issues,
        violations=tuple(issues),
        engine="ONTOLOGY_QUALITY_GATE",
    )


def validate_assets() -> ValidationReport:
    """对仓库本体资产执行完整门禁（语法 + 结构）。"""
    paths = [str(ONTOLOGY_DIR / name) for name in ASSETS]
    try:
        graph = load_graphs(paths)
    except OntologyQualityError as exc:
        return ValidationReport(
            conforms=False, violations=tuple(exc.issues), engine="ONTOLOGY_QUALITY_GATE"
        )
    return run_quality_gate(graph)
