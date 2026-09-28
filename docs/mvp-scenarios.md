---
title: MVP 场景与示例查询
parent: 系统设计
nav_order: 1
permalink: /mvp-scenarios.html
---

# MVP 场景与示例查询

## 范围

V0.1 聚焦 A 股人形机器人产业链，计划覆盖：

- 30～50 家候选上市公司；
- 50～100 个标准产品概念；
- 最近 3～5 年结构化数据与官方披露；
- 500～1000 条候选 Claim；
- 至少 30 个黄金查询。

不在 V0.1 范围内：全 A 股全产业链、实时行情和交易、股价预测、自动投资建议、完整工商穿透、Kubernetes 或大规模流处理。

## 首个纵向查询

问题：

> 哪些人形机器人核心零部件公司已经量产，并且最近三个完整财年经营活动现金流合计为正？

受控 QueryPlan 的核心条件：

```json
{
  "intent": "SEMANTIC_SCREEN",
  "entities": [
    {
      "entity_type": "theme",
      "text": "人形机器人",
      "resolved_id": "theme:humanoid_robot"
    }
  ],
  "semantic_filters": [
    {
      "path": "product_descendant_of",
      "operator": "descendant_of",
      "value": "product_category:humanoid_robot_core_component"
    },
    {
      "path": "business_stage",
      "operator": "in",
      "value": ["MASS_PRODUCTION"]
    }
  ],
  "numeric_filters": [
    {
      "metric_code": "NET_CASH_FLOWS_OPER_ACT",
      "operator": "gt",
      "value": 0,
      "period_rule": "sum_of_latest_3_fiscal_years"
    }
  ],
  "evidence_required": true,
  "max_hops": 3,
  "max_results": 20
}
```

{: .note }
**财务口径：**“三年合计为正”不代表每年均为正。若问题要求连续三年均为正，应使用 `each_of_latest_3_fiscal_years`。少于三个完整财年或存在未处理空值时，结果必须标记数据不足，而不是把缺失值当作 0。

## 结果必须解释什么

每家公司至少返回：

| 字段 | 目的 |
|---|---|
| 公司与证券 | 区分发行主体和交易证券 |
| 标准产品及原始披露名 | 说明产品词如何归一 |
| 业务阶段与证据状态 | 区分研发、送样、量产、收入和否认 |
| Claim ID 与状态 | 定位正式事实及审核结果 |
| 来源、页码和原文 | 让使用者回到官方材料 |
| `as_of` / `known_at` | 明确业务时间与当时已知信息 |
| 三年现金流值、单位、口径 | 让财务筛选可复算 |
| 推理路径 | 解释公司、产品、类别与主题的关系 |
| 数据新鲜度和未知项 | 不隐藏滞后、冲突和缺口 |

## 其他查询类型

### 主体查询

> 证券代码 `688xxx.SH` 对应哪个上市主体？历史名称、行业和实际控制人是什么？

关键点：Company 与 Security 是不同实体；历史名称和控制关系都必须带有效时间与来源。

### 概念真假验证

> 哪些公司只是被平台列入“人形机器人概念”，哪些已有正式经营证据？

平台概念只形成 `TAGGED_AS`。只有被接受的经营 Claim 才能形成 `PRODUCES`、`DEVELOPS` 等关系。没有证据时应回答“当前证据不足”，而不是断言公司没有相关业务。

### 历史时间线

> 截至 2024 年底，该公司与谐波减速器相关的业务阶段是什么？在 2025 年 1 月 1 日系统当时知道什么？

前一个问题使用 `as_of`，后一个问题同时受 `known_at` 限制。

### 关系解释

> 这家公司为什么被认为与人形机器人主题存在直接或二阶经营暴露？

结果必须返回输入 Claim、规则版本和关系路径；推理结论不能被写成公司原话。

## 必测负向样例

- 只有概念标签、没有产品证据；
- 仅披露研发或计划，未达到量产；
- 公司明确否认相关业务；
- 第三方说法与年报冲突；
- 产品词相似但实际类别不同；
- 财务数据少于三年、含空值、被重述或口径不同；
- Claim 已失效，或在指定 `known_at` 时尚未被系统获知；
- Neo4j 滞后、Evidence 缺失或 LLM 不可用；
- 输入试图注入 SQL/Cypher 或要求买卖建议。

查询模型、编译边界和 API 草案见[查询、推理与 API 设计](query-and-api.html)。
