# Semantica集成设计

## 1. 定位

Semantica在本项目中承担 **语义运行时**，主要使用：

- OWL/RDF本体生成、加载和导出；
- SHACL数据约束和质量门禁；
- 知识图谱构建与图存储统一接口；
- 双时态事实和时间查询；
- W3C PROV-O风格的来源与处理链追踪；
- 多来源冲突检测和辅助处置；
- 图路径、邻居和相似性能力。

Semantica不承担：

- TuShare原始数据仓库；
- 财务报表数值计算；
- 唯一事实持久化；
- 未经校验的LLM自动结论；
- PostgreSQL与Neo4j之间的一致性事务。

## 2. 版本与运行环境

设计基线：

```text
Python 3.11
Semantica 0.7.0
```

实施时精确锁定版本，并通过依赖锁文件保证环境一致性。

推荐按需安装，而不是使用`semantica[all]`：

```bash
pip install \
  "semantica[graph-neo4j,shacl,documents,parse-pdf,parse-docling,vectorstore-pgvector,monitoring]==0.7.0"
```

实际依赖以项目锁文件和兼容性测试结果为准。

## 3. 适配器边界

业务代码只依赖项目自定义接口：

```python
from typing import Protocol, Any


class SemanticRuntime(Protocol):
    def validate_ontology(self) -> dict[str, Any]: ...

    def validate_claim(self, claim: dict[str, Any]) -> dict[str, Any]: ...

    def validate_graph(self, graph_data: dict[str, Any]) -> dict[str, Any]: ...

    def detect_conflicts(
        self,
        claims: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...

    def register_provenance(
        self,
        subject_id: str,
        source: dict[str, Any],
        activity: dict[str, Any],
    ) -> None: ...

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
        as_of: str | None = None,
    ) -> dict[str, Any]: ...
```

实现类：

```text
SemanticaRuntimeAdapter
```

这样可确保：

- Semantica升级不直接冲击业务层；
- 未来可替换Neo4j或增加RDF/SPARQL引擎；
- 单元测试可以使用FakeSemanticRuntime；
- Semantica API变化由适配器集中吸收。

## 4. 模块映射

| 项目能力 | Semantica模块/类 | 项目封装 |
|---|---|---|
| 本体生命周期 | `semantica.ontology.OntologyEngine` | `OntologyService` |
| SHACL校验 | `OntologyValidator`、`OntologyQualityGate` | `ClaimValidationService` |
| 双时态事实 | `semantica.kg.BiTemporalFact` | `TemporalFactMapper` |
| 时间点查询 | `TemporalGraphQuery` | `TemporalQueryService` |
| 图存储 | `semantica.graph_store.GraphStore` | `GraphRepository` |
| 来源追踪 | `semantica.provenance.ProvenanceManager` | `ProvenanceService` |
| 冲突检测 | `semantica.conflicts.ConflictDetector` | `ConflictService` |
| 冲突处置 | `ConflictResolver`、`SourceTracker` | `ConflictPolicy` |
| 图版本 | `TemporalVersionManager` | 仅用于离线快照和测试 |

## 5. 初始化示例

```python
from dataclasses import dataclass

from semantica.graph_store import GraphStore
from semantica.ontology import OntologyEngine
from semantica.provenance import ProvenanceManager


@dataclass(frozen=True)
class SemanticSettings:
    base_uri: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str


class SemanticaRuntimeAdapter:
    def __init__(
        self,
        settings: SemanticSettings,
        provenance_storage,
    ) -> None:
        self.ontology = OntologyEngine(base_uri=settings.base_uri)

        self.graph = GraphStore(
            backend="neo4j",
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
        )
        self.graph.connect()

        self.provenance = ProvenanceManager(
            storage=provenance_storage,
        )

    def close(self) -> None:
        self.graph.close()
```

连接生命周期必须由应用容器管理，禁止每个请求重新建立连接。

## 6. 本体使用方式

### 6.1 本体来源

项目采用 **人工设计为主，自动生成为辅**：

```text
ontology/stock-core.ttl
ontology/product-skos.ttl
ontology/shapes.ttl
ontology/rules.yaml
```

不建议直接从数据自动生成完整股票本体并投入生产，因为：

- 原始主营名称噪声较大；
- 行业、概念、产品和主题语义不同；
- 自动生成难以确保稳定IRI；
- 金融事实需要严格的时间与证据边界。

Semantica的本体生成能力用于：

- 发现遗漏类或属性；
- 在开发环境生成候选本体；
- 评价覆盖率和粒度；
- 导出TTL、RDF/XML或JSON-LD。

正式本体变更必须通过Git评审。

### 6.2 质量门禁

CI执行：

1. TTL语法检查；
2. 类、属性、domain、range完整性；
3. SHACL Shapes可加载；
4. 测试图通过/失败符合预期；
5. 本体覆盖率达到阈值；
6. 不存在意外循环层级；
7. IRI不被无迁移方案删除或改名。

示例：

```python
from semantica.ontology import OntologyEngine

engine = OntologyEngine(
    base_uri="https://ontology.example.com/stock/",
)

report = engine.validate_graph(graph_data, ontology=ontology)
if not report.conforms:
    raise RuntimeError(report.violations)
```

## 7. Claim校验流程

```text
候选Claim
  -> Pydantic字段校验
  -> Entity/Product解析
  -> Evidence原文定位
  -> 业务规则校验
  -> SHACL校验
  -> 冲突检测
  -> 自动接受或人工审核
```

### 7.1 SHACL与业务规则分工

SHACL负责：

- 主体和对象类型；
- 必填属性；
- 基数；
- 枚举；
- 数值范围；
- 时间字段基本约束。

项目规则引擎负责：

- `MASS_PRODUCTION`不能只由概念标签支持；
- `REVENUE_DISCLOSED`必须绑定报告期或主营Observation；
- 十大股东不能自动推导控制关系；
- 推理结论必须引用输入Claim和规则版本；
- 未发现证据不能生成否定事实。

## 8. 双时态映射

Semantica的`BiTemporalFact`字段映射：

| 项目字段 | Semantica字段 | 说明 |
|---|---|---|
| `valid_from` | `valid_from` | 业务开始有效时间 |
| `valid_to` | `valid_until` | 业务结束有效时间 |
| `recorded_at` | `recorded_at` | 系统首次记录时间 |
| `superseded_at` | `superseded_at` | 系统记录被替换时间 |

示例：

```python
from semantica.kg import BiTemporalFact

relationship = {
    "source": "company:example",
    "target": "product:harmonic_reducer",
    "type": "PRODUCES",
    "valid_from": "2024-07-01",
    "valid_until": None,
    "recorded_at": "2025-04-18T09:30:00Z",
    "superseded_at": None,
}

fact = BiTemporalFact.from_relationship(relationship)
fields = fact.to_relationship_fields()
```

PostgreSQL仍保存权威双时态字段；Semantica对象用于验证、查询和图投影转换。

## 9. Provenance持久化

### 9.1 不使用默认内存存储承载生产审计

生产环境必须实现PostgreSQL版`ProvenanceStorage`：

```python
from semantica.provenance.storage import ProvenanceStorage


class PostgresProvenanceStorage(ProvenanceStorage):
    def store(self, entry) -> None: ...
    def retrieve(self, entity_id: str): ...
    def retrieve_all(self, entity_type: str | None = None): ...
    def trace_lineage(self, entity_id: str, max_depth: int | None = None): ...
    def clear(self) -> int: ...
```

原因：

- 默认内存存储进程退出后丢失；
- SQLite适合本地开发和小规模验证；
- 正式系统需要与Claim、Evidence和审核事务一致；
- 需要统一备份、权限和审计。

### 9.2 Provenance对象

追踪：

```text
原始source_record/document
  -> 解析activity
  -> evidence_fragment
  -> extraction_activity
  -> candidate_claim
  -> normalization_activity
  -> accepted_claim
  -> graph_projection_activity
  -> inferred_fact/query_result
```

## 10. 冲突检测

Semantica内置能力用于：

- 值冲突；
- 类型冲突；
- 关系属性冲突；
- 来源可信度加权处置。

项目补充规则用于：

- 双时态重叠冲突；
- 业务逻辑冲突；
- 控制权唯一性冲突；
- `DENIED`与`MASS_PRODUCTION`时间重叠；
- `REVENUE_DISCLOSED`缺乏财务Observation。

冲突检测必须在实体合并和正式投影前执行，保留原始来源差异。

## 11. Neo4j GraphStore使用

```python
results = runtime.graph.query(
    """
    MATCH (c:Company)-[r:PRODUCES]->(p:Product)
    WHERE c.id = $company_id
      AND r.claim_id IS NOT NULL
    RETURN c, r, p
    """,
    parameters={"company_id": company_id},
)
```

约束：

- 只执行项目Query Compiler生成的模板；
- 用户文本不能拼接进Cypher；
- 使用参数化查询；
- 批量写入而不是逐条网络调用；
- 每批写入后做读后校验；
- Neo4j内部ID不得作为外部API ID。

## 12. GraphBuilder策略

Semantica `GraphBuilder`适合：

- 在内存中验证实体关系集合；
- 离线生成小型图；
- 执行图分析和测试。

生产主写入路径采用：

```text
PostgreSQL Accepted Fact
  -> graph_outbox
  -> Graph Projector
  -> Semantica GraphStore
  -> Neo4j
```

不将`GraphBuilderWithProvenance`直接作为事实主写入器，原因是生产需要：

- 自定义PostgreSQL Provenance存储；
- 与Claim和Outbox同事务；
- 写入重试和幂等控制；
- 读后校验；
- 明确的失败状态。

## 13. Semantica失败隔离

### 13.1 校验失败

- Claim保留为`NEEDS_REVIEW`或`REJECTED`；
- 不写正式图谱；
- 保存完整错误报告。

### 13.2 图存储失败

- PostgreSQL事实事务不回滚；
- Outbox任务保留并重试；
- 查询可临时从PostgreSQL降级返回结构化事实；
- 运维告警显示图新鲜度。

### 13.3 Provenance写入失败

经营Claim不可进入`ACCEPTED`。Provenance是正式事实的强制组成部分。

## 14. 契约测试

每次Semantica升级必须执行：

1. OntologyEngine加载TTL；
2. SHACL正例通过、反例失败；
3. BiTemporalFact序列化往返一致；
4. Provenance自定义存储可写、可追踪；
5. Neo4j节点和关系创建、查询、删除测试；
6. 参数化Cypher可用；
7. 冲突检测输出结构不变；
8. 时间点查询结果符合黄金样例；
9. 清空Neo4j后可完整重建；
10. 关键API签名与项目适配器兼容。

## 15. 依赖升级规则

- 生产使用精确版本；
- 升级在独立分支进行；
- 先运行契约测试和黄金查询；
- 检查Semantica Release Notes和Breaking Changes；
- 升级失败时业务层无需修改，仅回退适配器依赖；
- 禁止自动升级到未验证的主版本或次版本。

## 16. 官方参考

- Repository: https://github.com/semantica-agi/semantica
- Documentation: https://docs.getsemantica.ai
- Ontology模块：`semantica.ontology`
- KG与Temporal模块：`semantica.kg`
- Graph Store模块：`semantica.graph_store`
- Provenance模块：`semantica.provenance`
- Conflicts模块：`semantica.conflicts`
