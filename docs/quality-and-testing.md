# 质量、安全与测试方案

## 1. 目标

系统质量的核心不是“能返回答案”，而是：

- 数据来源清晰；
- 事实与推理分层；
- 时间语义正确；
- 证据能够回溯；
- 冲突不会被静默覆盖；
- 图谱可以从事实主库重建；
- 查询口径可复现；
- 错误数据不会自动进入正式事实层。

## 2. 数据质量门禁

### 2.1 Raw 层

每次采集必须记录：

- 数据源和接口名称；
- 请求参数；
- 拉取时间；
- 业务日期或报告期；
- 原始响应；
- Payload SHA-256；
- 采集运行 ID；
- 解析器和 Schema 版本。

门禁：

- 同一来源、接口和 Payload Hash 不重复入库；
- 返回行数异常下降超过配置阈值时停止标准化；
- 字段新增、删除或类型变化时标记 `SCHEMA_CHANGED`；
- 空响应不能被自动解释为“无数据”；
- 权限错误、限流和网络错误必须分开记录。

### 2.2 标准层

- `ts_code` 在有效时间内唯一；
- Company 与 Security 不能混为同一实体；
- 股票代码必须符合交易所格式；
- 财务指标必须保存单位、币种、报告期和报表范围；
- 财务重述不能覆盖历史版本；
- 主营业务原始名称必须保留；
- 产品标准化映射必须记录本体版本和映射方法。

### 2.3 Claim 层

正式经营 Claim 必须满足：

- 有主体、谓词和对象；
- 有业务有效时间或明确的未知状态；
- 有来源；
- 有抽取或映射方法；
- `confidence` 在 0～1；
- `ACCEPTED` 的文本经营事实至少有一个可定位证据；
- `MASS_PRODUCTION` 不能只由概念标签支持；
- `REVENUE_DISCLOSED` 必须关联报告期或主营构成记录；
- `DENIED` 必须保存公司或交易所回复原文；
- `UNKNOWN` 不得转换为 `FALSE`。

## 3. 来源与证据分级

初始来源权重作为配置管理，不硬编码在业务逻辑：

| 来源 | 初始权重 |
|---|---:|
| 年报、半年报、招股书 | 1.00 |
| 交易所正式公告 | 0.98 |
| TuShare 标准化财务或公司数据 | 0.90 |
| 交易所互动回复 | 0.90 |
| 机构调研记录 | 0.82 |
| 上市公司官网 | 0.78 |
| 东方财富/同花顺概念标签 | 0.60 |
| 其他第三方资料 | 0.50 |

来源权重只影响自动审核和冲突排序，不代表低权重来源自动无效。所有冲突源都应保留。

## 4. 冲突治理

### 4.1 冲突类型

- 数值冲突：同一报告期指标值不同；
- 类型冲突：同一实体被识别为不同类别；
- 关系冲突：同一主体、谓词和对象属性不一致；
- 时态冲突：互斥状态在相同有效时间重叠；
- 逻辑冲突：业务状态与证据或属性不一致；
- 主体冲突：同一证券在同一时间映射多个发行主体。

### 4.2 自动处理边界

可以自动处理：

- 完全重复的事实；
- 同一官方来源的新披露明确替代旧披露；
- 统一单位后数值一致；
- 低风险别名映射。

必须人工审核：

- 量产与否认同时有效；
- 实际控制人冲突；
- 核心产品映射置信度不足；
- 第三方量产说法与年报未披露收入冲突；
- 关键财务值在不同来源不一致且无法由口径解释。

## 5. SHACL 和规则门禁

CI 和运行时分别执行：

### 5.1 CI 本体门禁

- Turtle/RDF 语法正确；
- 类、属性和命名空间可解析；
- Domain/Range 指向已定义类；
- SKOS 首选标签唯一；
- 产品层级无循环；
- SHACL Shapes 可加载；
- 本体覆盖率达到配置阈值；
- 本体版本符合语义化版本规则。

### 5.2 运行时事实门禁

- Claim 形状校验；
- 对象类型校验；
- 时间区间校验；
- 证据最小数量校验；
- 业务阶段与来源等级规则；
- 互斥关系和状态冲突检查。

## 6. 测试金字塔

### 6.1 单元测试

覆盖：

- TuShare 字段映射；
- 股票代码标准化；
- 报告期和单位转换；
- Product Alias 到 SKOS Concept 的映射；
- Claim ID 与 Content Hash；
- 双时态有效性；
- QueryPlan Schema；
- SQL/Cypher 编译白名单；
- Outbox 幂等键；
- 原文引文定位。

### 6.2 契约测试

#### TuShare 契约

- 每个接口最小调用；
- 权限、限流和字段结构；
- DataFrame 列与项目 Schema 的兼容性；
- 空结果与异常结果区分。

#### Semantica 契约

固定 Semantica 版本后测试：

1. 本体加载与导出；
2. SHACL 合格和失败样例；
3. 双时态序列化与查询；
4. Provenance 写入和读取；
5. 冲突检测结果结构；
6. Neo4j 节点、关系和参数化查询；
7. 版本升级前后的行为差异。

#### Neo4j 契约

- 创建和读取节点；
- 创建带 `claim_id` 的关系；
- 有效时间过滤；
- 2～4 跳路径查询；
- 删除并全量重建；
- PostgreSQL 与图谱对账。

### 6.3 集成测试

覆盖端到端链路：

```text
TuShare Fixture
  -> Raw
  -> Standard
  -> Claim
  -> SHACL
  -> Outbox
  -> Neo4j
  -> QueryPlan
  -> SQL/Cypher
  -> Evidence
  -> API Response
```

测试必须使用固定 Fixture，不依赖实时外部网络。

### 6.4 黄金查询测试

至少维护 30 个黄金问题，每个问题包含：

```yaml
id: GQ-001
question: 哪些公司量产谐波减速器？
as_of: 2025-12-31
expected_companies:
  include: [company:a, company:b]
  exclude: [company:c]
expected_paths:
  - PRODUCES
  - SUBCLASS_OF
required_evidence_types:
  - ANNUAL_REPORT
allowed_unknowns: []
```

黄金集指标：

- 公司集合 Precision/Recall；
- Claim 准确率；
- Product 映射准确率；
- Evidence Grounding Rate；
- Path Explanation Correctness；
- 时间点查询准确率；
- 未知状态误判率。

## 7. MVP 验收指标

| 指标 | MVP 目标 |
|---|---:|
| Company/Security 主体映射准确率 | ≥ 99% |
| 黄金产品映射准确率 | ≥ 95% |
| 已接受 Claim 原文可定位率 | 100% |
| 已接受经营关系含 claim_id 比例 | 100% |
| 图谱可重建率 | 100% |
| PostgreSQL/Neo4j 对账一致率 | ≥ 99.9% |
| 黄金查询 Precision | ≥ 90% |
| 黄金查询 Recall | ≥ 85% |
| 未知误判为否定的比例 | 0% |
| 高严重度冲突自动接受比例 | 0% |

## 8. 安全设计

### 8.1 凭证

- `TUSHARE_TOKEN`、数据库密码和 LLM Key 只通过 Secret 注入；
- 不提交 `.env`；
- 日志不得输出 Token、Authorization Header 或完整数据库连接串；
- 文档下载临时地址不得进入永久日志。

### 8.2 数据库

- API 使用只读查询账号；
- Worker 使用受限写账号；
- Schema Migration 使用单独账号；
- Neo4j 查询与管理账号分离；
- 所有 SQL/Cypher 参数化；
- 限制最大跳数、结果数和执行时长。

### 8.3 LLM

- 文档中的指令视为数据，不执行文档内 Prompt；
- 只允许结构化输出；
- 输出通过 Pydantic 和 SHACL 校验；
- 候选 Claim 不能绕过审核状态机；
- QueryPlan 不允许任意表名、字段名和关系名；
- 外部模型调用时遵守文档与数据分级策略。

### 8.4 审计

记录：

- 数据采集运行；
- 文档解析版本；
- Claim 抽取模型和 Prompt 版本；
- 人工审核；
- 本体版本；
- 查询计划和最终口径；
- 图谱重建与对账；
- 管理接口调用。

## 9. 可观测性

### 9.1 指标

- `ingestion_rows_total`
- `ingestion_failures_total`
- `source_capability_status`
- `claim_extracted_total`
- `claim_acceptance_rate`
- `claim_review_backlog`
- `evidence_grounding_failures_total`
- `outbox_pending_count`
- `outbox_oldest_age_seconds`
- `graph_reconciliation_diff`
- `query_latency_seconds`
- `query_plan_failures_total`
- `ontology_validation_failures_total`

### 9.2 告警

- 关键数据源连续失败；
- 接口 Schema 变化；
- 财务或概念数据超过新鲜度阈值；
- Outbox 积压；
- PostgreSQL 与 Neo4j 对账异常；
- 高严重度冲突未处理；
- Evidence Grounding 失败率异常；
- 查询错误率或延迟异常。

## 10. 数据备份与恢复

- PostgreSQL 每日备份并定期恢复演练；
- 原始 PDF 和解析产物保留 Hash；
- Neo4j 可备份，但必须支持从 PostgreSQL 全量重建；
- 本体、Shapes、规则和映射全部 Git 版本化；
- 每个可发布数据快照记录 `ontology_version`、`data_snapshot_version` 和 `build_id`。

## 11. 发布门禁

发布前必须全部通过：

- 单元测试；
- 契约测试；
- 集成测试；
- 本体和 SHACL 校验；
- 黄金查询回归；
- 数据库迁移前向和回滚测试；
- 图谱全量重建测试；
- 漏洞与依赖扫描；
- 数据新鲜度检查；
- 高严重度冲突清零或有明确豁免记录。
