# 领域与数据模型

## 1. 设计目标

股票本体系统不能将“公司属于某概念”直接等同于“公司实际从事该业务”。数据模型必须同时表达：

- 法人主体与证券的差异；
- 行业分类与平台概念标签；
- 公司披露的产品、研发与业务状态；
- 事实来源、原文证据和置信度；
- 事实在现实世界的有效期；
- 系统在什么时间获知、修正或废止该事实；
- 多来源冲突与人工审核；
- 事实与推理结果的边界。

## 2. 分层模型

```text
Source Layer
  原始API响应、PDF、公告元数据

Master Data Layer
  Company、Security、Exchange、Industry、Concept、Product等标准实体

Fact Layer
  Claim、Evidence、Provenance、Review、Conflict

Observation Layer
  FinancialObservation、BusinessSegmentObservation、MarketObservation

Projection Layer
  Neo4j节点、边和可解释路径
```

## 3. 核心实体

### 3.1 Company

表示法律或经营主体，不等同于股票。

关键字段：

| 字段 | 说明 |
|---|---|
| `id` | 系统UUID |
| `canonical_name` | 标准公司名称 |
| `legal_name` | 法定名称 |
| `unified_social_credit_code` | 统一社会信用代码，存在时作为强匹配键 |
| `company_type` | LISTED_COMPANY、SUBSIDIARY、SUPPLIER等 |
| `country_code` | 国家/地区 |
| `status` | ACTIVE、DISSOLVED、UNKNOWN |
| `source_system` | 首次建立实体的数据源 |

稳定业务键优先级：

```text
统一社会信用代码
  > 官方披露主体ID
  > 上市公司+交易所证券关系
  > 名称+地域+成立日期组合
```

### 3.2 Security

表示股票或其他证券。

| 字段 | 说明 |
|---|---|
| `id` | 系统UUID |
| `ts_code` | TuShare代码，如`600000.SH` |
| `symbol` | 证券代码 |
| `name` | 当前简称 |
| `security_type` | STOCK、ADR等 |
| `market` | 主板、科创板、创业板等 |
| `list_date` | 上市日期 |
| `delist_date` | 退市日期 |
| `status` | LISTED、DELISTED、SUSPENDED等 |

关系：

```text
Company --ISSUES--> Security
Security --LISTED_ON--> Exchange
```

### 3.3 Industry

行业必须携带分类体系和版本，不能只存行业名称字符串。

| 字段 | 说明 |
|---|---|
| `taxonomy` | SW、CSRC等 |
| `taxonomy_version` | 分类版本 |
| `level` | L1/L2/L3 |
| `code` | 行业代码 |
| `name` | 行业名称 |
| `parent_id` | 上级行业 |

公司行业关系是时间关系：

```text
CompanyIndustryMembership
- company_id
- industry_id
- valid_from
- valid_to
- source_record_id
```

### 3.4 Concept

表示东方财富、同花顺等平台的概念板块，不表示经过验证的经营事实。

| 字段 | 说明 |
|---|---|
| `source_platform` | EASTMONEY、THS等 |
| `external_code` | 平台概念代码 |
| `name` | 概念名称 |
| `snapshot_date` | 成员快照日期 |

必须使用关系：

```text
Company --TAGGED_AS--> Concept
```

不得因概念成员身份自动产生：

```text
Company --PRODUCES--> Product
```

### 3.5 Product与ProductCategory

`Product`是标准语义实体，TuShare主营构成中的原始名称通过映射关系连接到标准产品。

```text
RawBusinessItem
  --MAPS_TO--> Product
Product
  --SUBCLASS_OF--> ProductCategory
ProductCategory
  --PART_OF--> Theme
```

例如：

```text
“机器人减速机”
“谐波传动产品”
“精密传动装置”
      ↓ 人工或模型辅助映射
谐波减速器
      ↓
精密减速器
      ↓
机器人核心零部件
      ↓
人形机器人产业链
```

映射本身也要记录：

| 字段 | 说明 |
|---|---|
| `raw_term` | 原始披露名称 |
| `product_id` | 标准产品 |
| `mapping_method` | MANUAL、RULE、LLM_ASSISTED |
| `confidence` | 映射置信度 |
| `ontology_version` | 使用的本体版本 |
| `review_status` | 审核状态 |

### 3.6 Theme

表示系统用于研究和查询的主题，如“人形机器人”。Theme与平台Concept可以有关联，但不能合并为同一实体。

```text
Concept --RELATED_TO--> Theme
ProductCategory --PART_OF--> Theme
```

### 3.7 FinancialMetric与FinancialObservation

财务指标定义和值分离。

```text
FinancialMetric
- metric_code
- name
- statement_type
- unit_type
- aggregation_rule

FinancialObservation
- company_id/security_id
- metric_code
- period_start
- period_end
- report_type
- value
- currency
- source_record_id
- announced_at
- update_flag
```

所有数值计算由PostgreSQL完成，Neo4j只存必要摘要或Observation引用。

## 4. Claim中心事实模型

### 4.1 Claim定义

Claim表示一个带时间、来源和状态的事实主张：

```text
(subject, predicate, object/value, temporal scope, evidence, provenance)
```

示例：

```json
{
  "subject": "company:example",
  "predicate": "PRODUCES",
  "object": "product:harmonic_reducer",
  "business_stage": "MASS_PRODUCTION",
  "valid_from": "2024-07-01",
  "valid_to": null,
  "recorded_at": "2025-04-18T09:30:00Z",
  "status": "ACCEPTED",
  "confidence": 0.96
}
```

### 4.2 Claim类型

首版允许的经营谓词：

```text
PRODUCES
DEVELOPS
HAS_TECHNOLOGY_RESERVE
SAMPLE_VALIDATION
SUPPLIES_TO
USES_TECHNOLOGY
HAS_REVENUE_FROM
DENIES_INVOLVEMENT
CONTROLS
HOLDS
```

分类关系与经营关系分离：

```text
TAGGED_AS
CLASSIFIED_AS
ISSUES
LISTED_ON
```

推理关系不作为原始Claim：

```text
VERIFIED_RELEVANT_TO
DIRECT_EXPOSURE_TO
SECOND_ORDER_EXPOSURE_TO
```

### 4.3 Claim状态

```text
EXTRACTED       机器抽取得到，尚未完成校验
VALIDATED       结构、原文和规则校验通过
NEEDS_REVIEW    需要人工复核
ACCEPTED        可进入正式查询和图谱投影
CONTRADICTED    与其他Claim存在未解决冲突
SUPERSEDED      已被更新事实替代
REJECTED        被人工或规则否决
```

只有`ACCEPTED`默认进入正式经营关系投影。

### 4.4 业务阶段

业务阶段与证据状态不能混为一个枚举。

业务阶段：

```text
TECHNOLOGY_RESERVE
RESEARCH
PROTOTYPE
SAMPLE_VALIDATION
SMALL_BATCH
MASS_PRODUCTION
UNKNOWN
```

证据状态：

```text
PLATFORM_CLASSIFICATION_ONLY
PRODUCT_DISCLOSED
REVENUE_DISCLOSED
COMPANY_DENIAL
EVIDENCE_INSUFFICIENT
```

例如公司可以同时具有：

```text
business_stage = MASS_PRODUCTION
evidence_state = REVENUE_DISCLOSED
```

也可以是：

```text
business_stage = UNKNOWN
evidence_state = PLATFORM_CLASSIFICATION_ONLY
```

## 5. Evidence模型

### 5.1 Document

表示完整来源文件或结构化接口结果。

| 字段 | 说明 |
|---|---|
| `document_type` | ANNUAL_REPORT、ANNOUNCEMENT、IPO_PROSPECTUS、SURVEY、QA等 |
| `source_system` | CNINFO、SSE、SZSE、TUSHARE等 |
| `external_id` | 源系统文档ID |
| `title` | 标题 |
| `published_at` | 发布时间 |
| `source_url` | 来源地址 |
| `content_hash` | 文件或内容Hash |
| `local_object_key` | 对象存储地址 |

### 5.2 EvidenceFragment

表示Claim所引用的最小证据片段。

| 字段 | 说明 |
|---|---|
| `document_id` | 所属文档 |
| `page_number` | PDF页码 |
| `section_title` | 章节 |
| `paragraph_index` | 段落号 |
| `char_start/char_end` | 文本偏移 |
| `quote_text` | 原始引文 |
| `normalized_text` | 规范化文本 |
| `checksum` | 片段Hash |
| `embedding` | 向量 |

### 5.3 ClaimEvidence

```text
SUPPORTS
CONTRADICTS
QUALIFIES
SUPERSEDES
```

一条Claim可由多个证据支持，一个Evidence也可支持多个Claim。

## 6. 双时态模型

每个事实包含两套时间。

### 6.1 有效时间

```text
valid_from
valid_to
```

表示事实在业务世界中何时成立。

### 6.2 记录时间

```text
recorded_at
superseded_at
```

表示系统何时知道该事实、何时被新记录替换。

例子：公司在2024年7月开始量产，但系统直到2025年4月年报披露后才知道：

```text
valid_from = 2024-07-01
recorded_at = 2025-04-18
```

### 6.3 查询语义

- “截至2024年12月31日公司在做什么？”按有效时间查询；
- “系统在2025年1月1日已知什么？”按记录时间查询；
- “何时发现之前的事实有误？”比较记录时间版本。

## 7. Provenance模型

每条Claim需要记录：

```text
source_system
source_record_id/document_id
evidence_fragment_id
extraction_activity_id
extractor_version
ontology_version
mapping_version
agent_id/user_id
confidence
created_at
checksum
```

关键原则：

- Provenance不是一个可选备注字段，而是事实的一部分；
- 自动抽取、规则映射、人工修改分别记录活动；
- 人工接受Claim时保留机器原始输出；
- 更改标准产品映射时不覆盖旧版本，而是生成新版本关系。

## 8. 冲突模型

### 8.1 冲突类型

```text
VALUE_CONFLICT
TYPE_CONFLICT
RELATIONSHIP_CONFLICT
TEMPORAL_CONFLICT
LOGICAL_CONFLICT
SOURCE_CONFLICT
```

### 8.2 示例

```text
平台A：公司属于人形机器人概念
公司回复：产品理论上可用于机器人
年报：相关业务尚未形成收入
媒体：公司已经大规模量产
```

四条信息分别保存，不互相覆盖。系统可以将“尚未形成收入”和“已大规模量产并形成收入”的Claim标记冲突，并根据证据等级、发布时间和人工审核进行处置。

### 8.3 冲突处置

```text
AUTO_RESOLVED
MANUAL_REVIEW
EXPERT_REVIEW
UNRESOLVED
```

高影响经营事实默认不自动删除或覆盖，仅允许：

- 接受其中一条并将另一条标记`SUPERSEDED`；
- 保留不同时间段事实；
- 保留来源分歧并在回答中展示。

## 9. 实体消歧

### 9.1 Company消歧

强特征：

- 统一社会信用代码；
- 官方公司全称；
- 证券发行关系；
- 注册地址和成立日期。

弱特征：

- 公司简称；
- 曾用名；
- 英文名称；
- 子公司描述。

低置信度合并必须人工确认。

### 9.2 Product消歧

采用SKOS：

```text
prefLabel      标准名称
altLabel       别名、缩写、披露写法
broader        上位概念
narrower       下位概念
related        相关但非上下位关系
exactMatch     等价映射
closeMatch     近似映射
```

## 10. Neo4j投影模型

### 10.1 节点

```text
(:Company)
(:Security)
(:Exchange)
(:Industry)
(:Concept)
(:Theme)
(:Product)
(:ProductCategory)
(:Technology)
(:Commodity)
(:Organization)
(:Person)
(:Claim)
(:Evidence)
(:Document)
```

### 10.2 Claim节点路径

```text
(c:Company)-[:HAS_CLAIM]->(cl:Claim)
(cl)-[:OBJECT]->(p:Product)
(cl)-[:SUPPORTED_BY]->(e:Evidence)
(e)-[:PART_OF_DOCUMENT]->(d:Document)
```

同时为高频查询物化：

```text
(c)-[:PRODUCES {
  claim_id,
  business_stage,
  valid_from,
  valid_to,
  confidence
}]->(p)
```

物化边必须带`claim_id`，并可回查Claim和Evidence。

## 11. 领域不变量

1. 一个有效`Security`必须由一个`Company`发行。
2. 一条`ACCEPTED`经营Claim必须至少有一个Evidence或结构化官方来源。
3. `MASS_PRODUCTION`不能仅由概念板块标签支持。
4. `REVENUE_DISCLOSED`必须关联报告期或主营构成观察值。
5. `DENIES_INVOLVEMENT`必须保留公司原文。
6. `valid_from`不能晚于`valid_to`。
7. `confidence`范围为0到1。
8. 十大股东数据只能产生`HOLDS`，不能直接推导`CONTROLS`。
9. 图谱经营边必须包含`claim_id`。
10. 删除Neo4j数据不得影响PostgreSQL事实完整性。
11. 没有找到证据时不得生成否定Claim。
12. 推理关系必须记录使用的规则版本和输入Claim集合。
