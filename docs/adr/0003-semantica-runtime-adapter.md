---
title: ADR-0003：Semantica 适配边界
parent: 架构决策记录
grand_parent: 设计原文
nav_order: 3
permalink: /adr/0003-semantica-runtime-adapter.html
---

# ADR-0003：Semantica作为可替换语义运行时

- 状态：Accepted
- 日期：2026-09-28
- 决策人：ontologyMVP项目组

## 背景

项目需要本体生命周期、SHACL校验、知识图谱构建、双时态、溯源、冲突检测和图数据库适配能力。Semantica提供了这些能力，并支持Neo4j等后端。

同时，Semantica仍在快速演进。业务服务若直接依赖其内部类、返回结构和默认存储，将增加升级风险，并可能把事实库、审计和查询接口绑定到单一框架。

## 决策

1. 使用Semantica作为MVP的语义运行时。
2. 当前工程基线锁定Semantica `0.7.0`；升级必须通过契约测试。
3. 项目定义自己的`SemanticRuntime`端口，业务层只能依赖该端口。
4. `SemanticaRuntimeAdapter`封装以下能力：
   - Ontology与SHACL；
   - 双时态对象与时间查询；
   - Provenance；
   - Conflict；
   - GraphStore；
   - 本体质量门禁。
5. PostgreSQL继续作为事实与Provenance权威存储；不能依赖Semantica默认内存存储承担生产审计。
6. Neo4j写入通过项目Graph Projector和适配器完成，写后必须校验。
7. OWL、SKOS、SHACL与规则文件保存在Git中，不只存在于运行时对象。
8. Semantica自动生成本体的能力只用于辅助，不直接覆盖人工治理的正式本体。

## 端口定义

建议端口：

```python
from typing import Any, Protocol

class SemanticRuntime(Protocol):
    def validate_ontology(self) -> dict[str, Any]: ...
    def validate_claim(self, claim: dict[str, Any]) -> dict[str, Any]: ...
    def detect_conflicts(
        self,
        claims: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...
    def register_provenance(self, entry: dict[str, Any]) -> None: ...
    def project_fact(self, fact: dict[str, Any]) -> None: ...
    def query_graph(
        self,
        cypher: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]: ...
    def explain_path(
        self,
        source_id: str,
        target_id: str,
        max_hops: int,
    ) -> dict[str, Any]: ...
```

业务服务不能导入Semantica内部模块。只有`src/semantic/semantica_adapter.py`及其测试可直接导入。

## 存储策略

Semantica的默认内存Provenance不作为生产方案。项目应实现：

```text
PostgresProvenanceStorage implements ProvenanceStorage
```

该实现映射到`fact.provenance_entry`，并满足：

- 事务写入；
- Hash链；
- 按实体查询；
- 上下游Lineage；
- 批量写入；
- 清理操作受管理员权限保护。

## 版本与升级策略

升级步骤：

1. 在独立分支更新精确版本；
2. 运行Semantica适配器契约测试；
3. 运行Turtle、SHACL和Ontology Quality Gate；
4. 运行Neo4j创建、读取、批量和时态字段测试；
5. 运行Provenance持久化测试；
6. 运行全量黄金查询；
7. 检查发布说明中的破坏性变化；
8. 通过后更新ADR附录和锁文件。

不得使用不固定版本的生产依赖。

## 需要特别验证的契约

- GraphStore连接和关闭；
- 批量节点/边写入是否真实持久化；
- 参数化Cypher；
- BiTemporalFact序列化字段；
- TemporalGraphQuery时间边界；
- OntologyEngine和SHACL报告结构；
- ProvenanceManager与自定义Storage；
- ConflictDetector支持的实际冲突类型；
- 异常类型和错误信息；
- Python版本兼容性。

时态和逻辑冲突不能假设框架自动完整覆盖。项目仍需要SQL、SHACL和自定义规则补充。

## 后果

### 正面

- 快速获得本体、时态、溯源和图能力；
- 业务代码不绑定框架内部API；
- 框架升级影响集中在适配器；
- 保留替换其他本体/RDF/图运行时的可能；
- 本体文件和事实数据不被框架锁定。

### 负面

- 需要维护一层适配器和契约测试；
- 部分Semantica能力无法直接暴露，需要二次封装；
- 自定义PostgreSQL Provenance存储增加实现工作；
- 版本升级需要完整回归。

## 备选方案

### 方案A：业务层直接调用Semantica

未采用。初期代码少，但升级、替换和测试边界不清晰。

### 方案B：完全自研语义运行时

未采用。MVP阶段成本高，重复实现本体、SHACL、时态和溯源能力。

### 方案C：只用Neo4j，不使用Semantica

未采用。图查询可以实现，但本体生命周期、SHACL、双时态和Provenance需要自行组合更多组件。

## 验证方式

- 业务模块静态检查不得直接导入Semantica；
- 使用FakeSemanticRuntime完成单元测试；
- Semantica升级前后业务API结果一致；
- 自定义ProvenanceStorage重启后数据仍存在；
- 清空Neo4j后适配器可完成重建；
- 框架不可用时系统能明确失败或降级，而不是静默成功。
