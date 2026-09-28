---
title: 实施路线与验收标准
parent: 设计原文
nav_order: 7
permalink: /delivery-plan.html
---

# 实施路线与验收标准

## 1. 实施策略

采用“纵向切片优先”，而不是先建设一套庞大的全市场图谱。

第一个切片固定为：

> 从人形机器人概念候选公司中，识别已经具备核心零部件量产证据的公司，并筛选最近三个完整财年经营活动现金流合计为正的公司。

该切片可以同时验证：

- TuShare 权限和数据质量；
- 公司、证券、概念和财务标准化；
- 产品本体和别名映射；
- 文档证据抽取；
- Claim/Evidence 模型；
- Semantica、SHACL、双时态与溯源；
- PostgreSQL 与 Neo4j 投影；
- QueryPlan、SQL/Cypher 编排；
- 可解释答案。

## 2. V0.1 范围

### 2.1 数据范围

- A 股；
- 人形机器人主题；
- 30～50 家候选上市公司；
- 最近 3～5 年结构化与披露数据；
- 50～100 个标准产品概念；
- 500～1000 条证据化 Claim；
- 至少 30 个黄金查询。

### 2.2 功能范围

- 公司和证券主数据；
- 申万行业；
- 东方财富/同花顺概念候选；
- 主营构成；
- 利润表、现金流、财务指标；
- 文档和证据片段；
- 产品与业务阶段 Claim；
- Claim 审核；
- 图谱查询；
- 本体与财务混合筛选；
- 时间点查询；
- 解释路径和证据返回。

### 2.3 V0.1 非目标

- 全 A 股所有产业链；
- 完整客户和供应商网络；
- 实时行情交易系统；
- 股价预测；
- 自动买卖；
- 复杂概率推理；
- 多租户企业级权限体系；
- Kubernetes 和大规模流处理平台。

## 3. 阶段规划

## P0：工程与基础设施

### 工作包

- 初始化 Python 3.11 工程；
- FastAPI、Worker、SQLAlchemy、Alembic；
- PostgreSQL + pgvector；
- Neo4j；
- Docker Compose；
- 配置与 Secret 管理；
- 结构化日志和 Trace ID；
- CI；
- Semantica 精确版本锁定；
- `SemanticRuntime` 接口与空实现；
- TuShare Capability Probe。

### 交付物

```text
pyproject.toml
compose.yaml
.env.example
src/core
src/semantic/port.py
src/connectors/tushare/capability_probe.py
migrations/
apps/api
apps/worker
```

### 退出标准

- Docker Compose 可启动 PostgreSQL、Neo4j、API 和 Worker；
- `/health` 返回依赖状态；
- 能执行一个 TuShare 最小查询并保存 Raw 数据；
- 能创建一个 Company，并通过 Outbox 投影到 Neo4j；
- 能从 API 查询该 Company；
- CI 可以运行单元测试和本体语法检查。

## P1：结构化股票数据

### 工作包

实现以下数据集：

- `stock_basic`；
- `stock_company`；
- `namechange`；
- 申万行业分类及成分；
- 东方财富概念及成员；
- 同花顺概念及成员；
- `fina_mainbz_vip`；
- `income_vip`；
- `cashflow_vip`；
- `fina_indicator_vip`；
- 股东与机构调研的可用部分。

### 退出标准

- 30～50 家候选公司主体和证券映射完整；
- 原始数据均有请求、Hash 和采集运行记录；
- 概念成员按来源和快照日期存储；
- 主营构成保留原始产品名；
- 最近三个完整财年的经营现金流可计算；
- 标准层结果与 TuShare 抽样核对一致。

## P2：本体与语义层

### 工作包

- `stock-core.ttl`；
- `product-skos.ttl`；
- `shapes.ttl`；
- 产品别名和映射；
- Semantica Adapter；
- Claim 校验；
- 双时态；
- Provenance；
- 冲突检测；
- Ontology Quality Gate；
- 本体版本发布流程。

### 退出标准

- 本体和 SHACL 可由 CI 加载；
- 关键类和属性通过质量门禁；
- 20 个以上产品别名正确归一；
- Claim 可以完成 SHACL 合格/失败验证；
- 能查询某一历史时点的有效经营关系；
- 每条 Accepted Claim 有 Provenance；
- Semantica 升级有独立契约测试。

## P3：文档与证据化 Claim

### 工作包

- 巨潮、上交所、深交所文档元数据和下载；
- PDF Hash 和对象存储；
- 带页码的文本解析；
- 章节和段落切分；
- EvidenceFragment；
- Schema-guided Claim Extraction；
- 原文引用定位；
- 产品映射；
- 业务阶段识别；
- 人工审核队列与 API；
- 冲突处理。

### 退出标准

- 每家公司至少处理最近一份年报和关键公告；
- 累计 500 条以上候选 Claim；
- Accepted Claim 原文定位率 100%；
- 高严重度冲突不会自动接受；
- 审核操作有完整审计；
- “研发、送样、小批量、量产、收入、否认”能够结构化表达。

## P4：查询与解释

### 工作包

- QueryPlan；
- 实体解析；
- 时间表达；
- 语义查询编译；
- 财务查询编译；
- 查询编排；
- Evidence Loader；
- Grounded Answer Builder；
- 查询和筛选 API；
- 公司、产品、主题、Claim API；
- 黄金查询。

### 退出标准

五类查询可用：

1. 主体查询；
2. 真假概念验证；
3. 产品与业务阶段查询；
4. 历史时间线；
5. 本体 + 财务混合筛选。

并满足：

- 所有经营结果有 Claim；
- 所有 Accepted 文本 Claim 有 Evidence；
- 查询返回口径、时间点、数据新鲜度和推理路径；
- LLM 不直接执行 SQL/Cypher；
- 30 个黄金查询达到质量门槛。

## P5：生产加固

### 工作包

- 数据新鲜度；
- 定时调度；
- 告警；
- Outbox 重试和死信；
- PostgreSQL/Neo4j 对账；
- 图谱全量重建；
- 备份恢复；
- 权限与审计；
- 限流；
- 性能优化；
- 依赖和安全扫描。

### 退出标准

- 图谱删除后可完整重建；
- 对账差异有自动报告；
- 数据源失败有降级和告警；
- PostgreSQL 恢复演练通过；
- P95 达到查询性能目标；
- 管理操作均有审计记录。

## 4. 首个纵向切片拆解

### 4.1 数据获取

1. 获取人形机器人相关东方财富和同花顺概念；
2. 合并并保留来源，形成候选公司；
3. 拉取股票、公司、行业、历史名称；
4. 拉取近五年主营构成；
5. 拉取最近三个完整财年现金流；
6. 获取候选公司的年报和关键公告。

### 4.2 语义建模

产品树首批包括：

```text
人形机器人
  ├─ 整机
  ├─ 关节与执行器
  │   ├─ 谐波减速器
  │   ├─ RV减速器
  │   ├─ 无框力矩电机
  │   ├─ 伺服系统
  │   └─ 滚柱丝杠
  ├─ 感知
  │   ├─ 六维力传感器
  │   ├─ 视觉传感器
  │   └─ 编码器
  └─ 材料与其他核心部件
```

### 4.3 Claim 抽取

从文档识别：

- 是否生产；
- 是否研发；
- 是否送样；
- 是否小批量；
- 是否量产；
- 是否形成收入；
- 是否否认；
- 生效时间；
- 证据引文。

### 4.4 查询执行

```text
Theme = 人形机器人
Product descendant_of 核心零部件
BusinessStage in [MASS_PRODUCTION, REVENUE_DISCLOSED]
ClaimStatus = ACCEPTED
Evidence exists
FinancialMetric = NET_CASH_FLOWS_OPER_ACT
PeriodRule = sum_of_latest_3_fiscal_years
Value > 0
```

### 4.5 结果验收

每个结果必须包含：

- 公司与证券；
- 产品标准名称；
- 业务阶段；
- Claim ID；
- 原始产品或业务描述；
- 来源、页码和原文；
- 业务有效时间；
- 三年现金流值和口径；
- 图谱推理路径；
- 数据新鲜度；
- 未知项。

## 5. Backlog 优先级

### Must Have

- TuShare 结构化数据；
- Raw/Standard/Fact 分层；
- Company/Security；
- Product SKOS；
- Claim/Evidence；
- SHACL；
- 双时态；
- Outbox；
- Neo4j；
- QueryPlan；
- 财务混合筛选；
- 证据返回；
- 人工审核。

### Should Have

- 产品映射辅助界面；
- 冲突处理界面；
- 图谱可视化；
- 机构调研辅助证据；
- 公司业务时间线；
- 查询结果缓存；
- 数据新鲜度 Dashboard。

### Could Have

- 公司相似度；
- 图结构嵌入；
- Link Prediction；
- 自动生成本体候选；
- 全市场批量扩展；
- 更多产业主题。

### Won't Have in V0.1

- 自动交易；
- 股价预测；
- 无审核自动生成高风险事实；
- 完整工商股权穿透；
- 全市场客户供应商图谱。

## 6. Definition of Done

一个功能只有同时满足以下条件才算完成：

- 业务行为和边界有文档；
- 有数据库迁移；
- 有接口或内部契约；
- 有单元测试；
- 有集成测试；
- 有审计和错误处理；
- 有监控指标；
- 不泄露凭证；
- 数据可追溯；
- 能在固定 Fixture 上重放；
- README 或相关设计文档已更新。

## 7. 建议开发顺序

```text
1. 工程脚手架和数据库
2. stock_basic + stock_company
3. Concept + Candidate Pool
4. fina_mainbz_vip + cashflow_vip
5. Product SKOS + 映射
6. Claim/Evidence + SHACL
7. 年报解析和量产 Claim
8. Outbox + Neo4j
9. QueryPlan + 首个查询 API
10. 黄金查询与质量门禁
11. 审核 UI
12. 生产加固
```

## 8. 决策检查点

### Checkpoint A：结构化数据是否足够

通过标准：候选池、主营构成和财务值可以稳定取得。若 TuShare 个别接口不可用，按数据源设计启用官方或其他备用源。

### Checkpoint B：产品映射是否可用

通过标准：黄金产品词表映射准确率达到 95%。若自动映射不足，先使用人工审核而不是扩展复杂模型。

### Checkpoint C：Claim 是否可信

通过标准：Accepted Claim 原文定位率 100%，抽样准确率达到目标，高风险事实无自动误接收。

### Checkpoint D：本体是否真正产生价值

通过标准：黄金查询相对于简单关键词或平台标签，在准确率、解释性和时间查询上有明确提升。

只有通过 Checkpoint D，才进入更多产业主题和全市场扩展。
