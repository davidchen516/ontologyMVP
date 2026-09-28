---
title: TuShare 与数据接入
parent: 设计原文
nav_order: 3
permalink: /data-sources-and-ingestion.html
---

# TuShare与数据接入设计

## 1. 数据源策略

系统采用明确的优先级：

```text
TuShare结构化数据
  ↓ 字段缺失、接口无权限或内容不够
巨潮资讯/上交所/深交所正式披露
  ↓ 仍不足
上市公司官网
  ↓ 仅作候选或辅助
AKShare及其他第三方平台
```

原则：

- TuShare是结构化主数据源；
- 官方披露是经营事实的最高等级证据；
- 第三方平台用于候选发现，不自动覆盖正式事实；
- 每个来源都保留原始数据、抓取时间、请求参数和Hash；
- 任何数据冲突都显式记录，不静默覆盖。

当前账户积分为6420，可覆盖大量2000、5000和6000积分档接口。但部分接口可能需要独立权限，因此系统必须以运行时能力探针为准。

## 2. TuShare接口规划

> 接口可用性和字段以能力探针及TuShare官方文档为准，不能仅根据积分静态判断。

### 2.1 主数据

| 数据域 | 推荐接口 | 目标实体/关系 | 更新方式 |
|---|---|---|---|
| 股票列表 | `stock_basic` | Security | 每周全量+启动校验 |
| 上市公司资料 | `stock_company` | Company | 每周增量比对 |
| 历史名称 | `namechange` | CompanyAlias/SecurityAlias | 每周增量 |
| 交易日历 | `trade_cal` | Calendar | 年度全量、每日检查 |

### 2.2 行业分类

| 数据域 | 推荐接口 | 用途 |
|---|---|---|
| 申万行业分类 | `index_classify` | 建立L1/L2/L3分类树 |
| 申万行业成员 | `index_member_all` | 建立带纳入/剔除日期的行业关系 |

`index_member_all`返回的`in_date`、`out_date`和`is_new`必须完整保留，不能只保存当前行业字符串。

### 2.3 概念板块

| 平台 | 推荐接口 | 用途 |
|---|---|---|
| 同花顺 | `ths_index`、`ths_member` | 当前概念候选池与平台交叉验证 |
| 东方财富 | `dc_index`、`dc_member` | 概念候选池与可用历史快照 |

模型：

```text
Company --TAGGED_AS {source_platform, snapshot_date}--> Concept
```

禁止从概念成员直接生成`PRODUCES`或`DEVELOPS`经营Claim。

### 2.4 主营业务构成

| 接口 | 用途 |
|---|---|
| `fina_mainbz` | 单只股票历史主营构成回补 |
| `fina_mainbz_vip` | 按报告期批量拉取全市场产品/行业/地区构成 |

核心字段：

```text
ts_code
end_date
bz_item
bz_code   P产品 / I行业 / D地区
bz_sales
bz_profit
bz_cost
curr_type
update_flag
```

`bz_item`是原始披露口径，必须原样保存，再映射到标准Product；不得直接将`bz_item`当作全市场统一产品ID。

### 2.5 财务报表

| 接口 | 数据 |
|---|---|
| `income_vip` | 利润表 |
| `balancesheet_vip` | 资产负债表 |
| `cashflow_vip` | 现金流量表 |
| `fina_indicator_vip` | 财务指标 |

要求：

- 保存`ann_date`、`f_ann_date`、`end_date`、`report_type`、`comp_type`、`update_flag`；
- 同一报告期可能有调整前、调整后、母公司和合并口径，不能直接覆盖；
- 查询默认使用“最新有效合并口径”，但必须能说明选择规则；
- 数值单位、币种和空值语义统一管理。

### 2.6 股东与公司治理

| 接口 | 用途 |
|---|---|
| `top10_holders` | 十大股东持股事实 |
| `top10_floatholders` | 十大流通股东 |
| `stk_holdernumber` | 股东户数 |
| 其他可用治理接口 | 辅助建立治理和控制候选 |

规则：

- 股东数据只自动产生`HOLDS`；
- 不得根据第一大股东自动产生`CONTROLS`；
- 实际控制人需由年报、招股书或正式控制权披露确认。

### 2.7 机构调研、公告和互动

| 接口 | 作用 | 说明 |
|---|---|---|
| `stk_surv` | 产品进展、客户验证、产能等辅助证据 | 证据等级低于法定公告 |
| `anns_d` | 公告目录与原文入口 | 可能需要独立权限 |
| 上证/深证互动接口 | 公司回复与否认 | 可能需要独立权限 |

若TuShare无权限，则转向交易所和巨潮官方渠道。

## 3. 能力探针

### 3.1 目的

启动时和每日执行最小请求，记录真实能力：

```text
AVAILABLE
NO_PERMISSION
SEPARATE_PERMISSION_REQUIRED
RATE_LIMITED
SCHEMA_CHANGED
NETWORK_ERROR
UNKNOWN
```

### 3.2 capability_registry

```sql
CREATE TABLE source_capability (
    source_system       VARCHAR(50) NOT NULL,
    capability_name     VARCHAR(100) NOT NULL,
    status              VARCHAR(50) NOT NULL,
    checked_at          TIMESTAMPTZ NOT NULL,
    response_signature  VARCHAR(64),
    error_code          VARCHAR(100),
    error_message       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_system, capability_name)
);
```

### 3.3 降级规则

```text
anns_d = AVAILABLE
  -> 使用TuShare获取公告列表

anns_d != AVAILABLE
  -> 使用巨潮/交易所官方公告检索

概念接口不可用
  -> 仅启用已缓存的快照并标记新鲜度
  -> 必要时使用官方或第三方候选源
```

降级必须在查询结果中体现数据新鲜度和来源，不可静默切换。

## 4. 采集器接口

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Any


@dataclass(frozen=True)
class SourceBatch:
    source_system: str
    dataset_name: str
    request_params: dict[str, Any]
    retrieved_at: datetime
    rows: list[dict[str, Any]]
    schema_signature: str


class SourceConnector(Protocol):
    def probe(self) -> dict[str, Any]: ...
    def fetch(self, cursor: dict[str, Any] | None = None) -> SourceBatch: ...
    def next_cursor(self, batch: SourceBatch) -> dict[str, Any] | None: ...
```

每个接口一个独立Connector，避免一个巨大客户端承载所有业务逻辑。

## 5. Raw层

### 5.1 source_record

```sql
CREATE TABLE source_record (
    id                  UUID PRIMARY KEY,
    source_system       VARCHAR(50) NOT NULL,
    dataset_name        VARCHAR(100) NOT NULL,
    external_key        VARCHAR(300),
    request_params      JSONB NOT NULL,
    retrieved_at        TIMESTAMPTZ NOT NULL,
    source_effective_at TIMESTAMPTZ,
    payload_hash        VARCHAR(64) NOT NULL,
    raw_payload         JSONB NOT NULL,
    ingest_run_id       UUID NOT NULL,
    schema_signature    VARCHAR(64),
    schema_version      VARCHAR(30),
    UNIQUE (source_system, dataset_name, payload_hash)
);
```

### 5.2 原始层原则

- 不修改字段名；
- 不丢弃未知字段；
- 不把空字符串、`None`和0混为一谈；
- 保存接口返回顺序无业务意义，但保留原始JSON；
- 写入后再做标准化；
- 支持从Raw层重建全部标准数据。

## 6. Ingest Run与断点续跑

```sql
CREATE TABLE ingest_run (
    id                UUID PRIMARY KEY,
    source_system     VARCHAR(50) NOT NULL,
    dataset_name      VARCHAR(100) NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ,
    status            VARCHAR(30) NOT NULL,
    cursor_state      JSONB,
    request_count     INTEGER NOT NULL DEFAULT 0,
    rows_received     INTEGER NOT NULL DEFAULT 0,
    rows_inserted     INTEGER NOT NULL DEFAULT 0,
    rows_updated      INTEGER NOT NULL DEFAULT 0,
    rows_rejected     INTEGER NOT NULL DEFAULT 0,
    error_detail      JSONB,
    code_version      VARCHAR(64),
    mapping_version   VARCHAR(30)
);
```

状态：

```text
CREATED
RUNNING
PARTIAL_SUCCESS
SUCCEEDED
FAILED_RETRYABLE
FAILED_FINAL
CANCELLED
```

## 7. 幂等策略

### 7.1 原始记录

```text
source_system + dataset_name + payload_hash
```

### 7.2 主数据

```text
Security: ts_code
Company: unified_social_credit_code，缺失时使用受控复合键
Industry: taxonomy + taxonomy_version + code
Concept: source_platform + external_code
```

### 7.3 财务观察值

推荐幂等键：

```text
security_id
+ metric_code
+ end_date
+ report_type
+ comp_type
+ update_flag
+ ann_date
```

保留调整前数据；选择“当前有效值”通过视图或查询规则完成。

### 7.4 Claim

对规范化后的Claim内容生成Hash：

```text
subject_id
predicate
object_id/object_value
valid_from
valid_to
source_id
source_location
```

相同Hash不重复写入；同一语义不同来源仍保留为不同Claim或不同Evidence绑定。

## 8. 标准化流程

```text
Raw Record
  -> 字段类型校验
  -> 股票代码和日期规范化
  -> 主体解析
  -> 单位和币种统一
  -> 时间口径处理
  -> 源记录关联
  -> 数据质量规则
  -> Standard实体/Observation
```

禁止在标准化时静默纠错。所有修正写入`normalization_event`：

```text
original_value
normalized_value
rule_id
rule_version
confidence
review_status
```

## 9. 文档采集

### 9.1 Document Fetch

保存：

- 来源系统；
- 官方文档ID；
- 标题；
- 发布日期；
- 公司/股票代码；
- URL；
- MIME类型；
- 文件SHA-256；
- 下载时间；
- 抓取状态。

同一URL内容Hash发生变化时，不覆盖旧文件，创建新DocumentVersion。

### 9.2 解析质量

解析后至少验证：

- 页数；
- 文本字符数；
- 空白页比例；
- 页码映射完整度；
- 表格解析是否启用；
- 文档标题与元数据是否一致。

解析失败不得进入自动Claim抽取。

### 9.3 证据片段

推荐按标题和段落切分，保留：

```text
document_id
page_number
section_title
paragraph_index
char_start
char_end
quote_text
checksum
```

## 10. 调度计划

| 数据集 | 频率 | 策略 |
|---|---|---|
| stock_basic | 每周、启动校验 | 全量小表 |
| stock_company | 每周 | 全量比对或分交易所 |
| namechange | 每周 | 增量 |
| 申万行业 | 每周 | 保存历史有效期 |
| 概念成员 | 每交易日 | 快照，不覆盖历史 |
| 财务报表 | 披露季每日 | 按报告期批量拉取 |
| fina_mainbz_vip | 披露季每日 | 产品、行业分别拉取 |
| 机构调研 | 每日 | 增量 |
| 公告 | 每日 | MVP候选公司优先 |
| PDF解析 | 文档到达后 | 异步Worker |
| 图同步 | 持续 | Outbox消费 |
| 图对账 | 每日 | 全量计数+抽样Hash |

## 11. 限流与重试

- 每个Connector独立配置调用速率；
- 429、超时和短暂网络错误使用指数退避；
- 权限错误不盲目重试；
- Schema变化立即熔断该数据集；
- 重试请求保持相同`ingest_run_id`和幂等键；
- 超过最大重试进入`FAILED_FINAL`并告警。

## 12. 数据质量规则

### 12.1 通用规则

- 股票代码必须符合交易所后缀规范；
- 日期必须可解析且不晚于合理未来时间；
- 数值字段不得将缺失转换为0；
- 币种未知时不得默认CNY；
- 行数较历史基线突降或突增时阻断自动发布；
- 新字段和字段消失都触发Schema告警。

### 12.2 财务规则

- 报告期、公告日期和更新标志完整；
- 调整前后数据可并存；
- 合并口径和母公司口径不可混算；
- 年度计算只使用完整财年；
- 查询返回使用的报告期和选择规则。

### 12.3 概念规则

- 每次成员采集保存`snapshot_date`；
- 东方财富和同花顺为不同ConceptSource；
- 某平台移除成员不等同于公司业务终止；
- 概念数据只能作为候选发现和平台分类事实。

## 13. 来源可信度初始配置

| 来源 | 建议权重 | 说明 |
|---|---:|---|
| 法定年报、半年报、招股书 | 1.00 | 最高等级经营证据 |
| 交易所正式公告 | 0.98 | 正式披露 |
| TuShare财务和公司结构化数据 | 0.90 | 主数据源，仍保留源接口 |
| 交易所互动回复 | 0.90 | 公司正式回复，但法律属性不同于公告 |
| 机构调研 | 0.82 | 辅助业务进展证据 |
| 公司官网 | 0.78 | 官方但可能偏营销表达 |
| 概念板块标签 | 0.60 | 只证明平台分类 |
| 其他第三方 | 0.50 | 默认仅生成候选Claim |

权重是冲突治理的初始配置，不作为真实性的绝对概率。

## 14. 参考接口文档

- TuShare股票基础与公司信息：`stock_basic`、`stock_company`
- 申万行业成员：`index_member_all`
- 主营业务构成：`fina_mainbz`、`fina_mainbz_vip`
- 财务报表：`income_vip`、`balancesheet_vip`、`cashflow_vip`、`fina_indicator_vip`

实施时应将每个接口的官方文档地址记录在Connector配置中，并建立字段契约测试。
