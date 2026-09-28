---
title: OWL、SKOS 与 SHACL
parent: 系统设计
nav_order: 3
permalink: /ontology-guide.html
---

# OWL、SKOS 与 SHACL 本体指南

## 资产分工

| 资产 | 文件 | 责任 |
|---|---|---|
| OWL/RDF 核心模型 | [`stock-core.ttl`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/stock-core.ttl) | 公司、证券、Claim、Evidence、时间和核心关系 |
| OWL 关系扩展 | [`stock-relations.ttl`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/stock-relations.ttl) | 组成、收入、否认和主题相关性关系 |
| SKOS 产品词表 | [`product-skos.ttl`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/product-skos.ttl) | 产品标准名、别名、层级和相关关系 |
| SHACL Shapes | [`shapes.ttl`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/shapes.ttl) | Claim、类型、时间、证据和业务约束 |
| 确定性规则 | [`rules.yaml`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/rules.yaml) | 主题相关、经营暴露和证据策略 |
| TuShare 映射 | [`tushare.yaml`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/mappings/tushare.yaml) | 源字段到领域模型的映射 |

## 为什么同时使用三种标准

### OWL：定义领域语义

OWL 描述类、属性、Domain、Range 和可共享语义。它适合回答“什么是 Company、Product、Claim，它们允许怎样关联”，但不应该承载所有产品判断或工作流规则。

### SKOS：治理产品词汇

SKOS 适合维护标准名、别名、上位/下位关系和相关概念。例如多个披露词可以经过可审计映射连接到“谐波减速器”，而不是覆盖原始文本。

### SHACL：在边界处校验数据

SHACL 为进入事实库和图投影的数据提供确定性门禁，例如：

- Claim 是否有主体、谓词和对象；
- `confidence` 是否在 0～1；
- 时间区间是否合法；
- Accepted 文本 Claim 是否有可定位证据；
- 对象类型是否符合属性约束。

复杂经营判断仍应放在版本化规则或应用模块中，而不是把所有逻辑塞进 OWL 推理。

## 命名空间与版本

当前资产使用占位命名空间：

```text
https://ontology.example.com/stock#
https://ontology.example.com/product#
```

{: .warning }
**部署前决策：**正式部署前必须替换为项目控制的稳定域名。一旦发布，IRI 不应随中文名称、环境或部署地址变化。

版本采用 `MAJOR.MINOR.PATCH`：

- MAJOR：不兼容的类、属性或语义变化；
- MINOR：兼容地新增类、属性、产品或规则；
- PATCH：标签、说明或约束缺陷修复。

每条 Claim 保存 `ontology_version`；规则、映射和数据快照也必须可对应到具体版本。

## 修改流程

1. 先解释业务概念和现有建模缺口，避免仅凭数据字段新增类。
2. 保持 IRI 稳定，保留原始披露词，不在原数据上直接改名。
3. 同步更新 OWL/SKOS、SHACL、规则和必要映射。
4. 增加合格和失败样例，运行 Turtle/YAML 与本地链接校验。
5. 评估对历史 Claim、产品映射、QueryPlan 和图重建的影响。
6. 经领域审核后发布新版本，记录迁移和回滚方式。

## 质量门禁

- 类和属性有标签与说明；
- ObjectProperty 有合理 Domain/Range；
- 每个产品概念在相同语言下有唯一 `skos:prefLabel`；
- 产品层级无循环；
- 规则引用的类、关系、阶段和证据状态均可解析；
- SHACL Shapes 可加载，错误数为 0；
- 重要变更通过黄金映射和黄金查询回归；
- Semantica 升级前后契约行为一致。

本体目录的原始说明见 [`ontology/README.md`](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/README.md)。
