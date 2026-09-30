---
title: 设计原文
nav_order: 5
has_children: true
permalink: /reference.html
---

# 设计原文

本组页面是项目的工程设计基线。对外指南用于降低阅读门槛；当摘要与设计原文存在差异时，应先检查最新 ADR、Issue 和提交，再修正文档漂移。

## 架构与领域

- [总体架构](architecture.html)
- [领域与数据模型](domain-model.html)
- [TuShare 与数据接入](data-sources-and-ingestion.html)
- [Semantica 集成设计](semantica-integration.html)
- [查询、推理与 API 设计](query-and-api.html)
- [产品界面设计](product-ui.html)
- [质量、安全与测试方案](quality-and-testing.html)
- [实施路线与验收标准](delivery-plan.html)

## 数据库与图结构

- [PostgreSQL 核心表结构](database-schema.sql)
- [Neo4j 约束与索引](neo4j-schema.cypher)

## 本体与语义资产

- [本体目录说明](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/README.md)
- [股票核心本体](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/stock-core.ttl)
- [经营与产业链关系扩展](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/stock-relations.ttl)
- [人形机器人产品 SKOS 词表](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/product-skos.ttl)
- [SHACL 约束](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/shapes.ttl)
- [确定性推理规则](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/rules.yaml)
- [TuShare 字段映射](https://github.com/davidchen516/ontologyMVP/blob/main/ontology/mappings/tushare.yaml)

## 架构决策

- [ADR-0001：PostgreSQL 事实主库与 Neo4j 投影](adr/0001-storage-responsibilities.html)
- [ADR-0002：Claim 中心事实模型](adr/0002-claim-centered-model.html)
- [ADR-0003：Semantica 可替换语义运行时](adr/0003-semantica-runtime-adapter.html)
- [ADR-0004：GitHub Pages 文档站选型](adr/0004-documentation-site.html)
- [ADR-0005：产品 Web UI 技术栈与边界](adr/0005-product-ui-stack.html)
