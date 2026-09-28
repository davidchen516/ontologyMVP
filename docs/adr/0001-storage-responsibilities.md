---
title: ADR-0001：存储职责
parent: 架构决策记录
grand_parent: 设计原文
nav_order: 1
permalink: /adr/0001-storage-responsibilities.html
---

# ADR-0001：PostgreSQL 作为事实主库，Neo4j 作为图查询投影

- 状态：Accepted
- 日期：2026-09-28
- 决策者：ontologyMVP 项目组

## 背景

股票本体系统同时需要：

- 保存 TuShare 原始数据和结构化财务数据；
- 保存 Claim、Evidence、审核、双时态和溯源；
- 支持事务、唯一约束、重跑和审计；
- 支持公司、产品、主题、控制关系的多跳图查询；
- 支持删除图数据库后恢复；
- 避免在 PostgreSQL 与 Neo4j 之间实施高复杂度分布式事务。

如果把 Neo4j 作为唯一事实库，财务数值计算、文档证据、采集任务、版本和事务处理会变得复杂。若只使用 PostgreSQL，又会增加多跳关系查询、路径解释和图分析的实现成本。

## 决策

1. PostgreSQL 是唯一事实主库。
2. Neo4j 是由 PostgreSQL 投影得到的图查询模型。
3. 所有 Raw 数据、主数据、Claim、Evidence、Provenance、审核、财务 Observation 和任务状态均保存在 PostgreSQL。
4. Neo4j 保存查询所需实体、关系、Claim 节点和证据引用。
5. PostgreSQL 到 Neo4j 使用 Transactional Outbox，不使用跨库分布式事务。
6. Neo4j 必须能够从 PostgreSQL 全量重建。
7. API 返回的事实详情和证据以 PostgreSQL 为准；Neo4j 负责候选筛选和路径查询。

## 写入流程

```text
BEGIN PostgreSQL transaction
  write/update Claim
  write ClaimEvidence
  write Provenance
  write graph_outbox
COMMIT

Graph Projector
  claim outbox event
  idempotent MERGE to Neo4j
  read-after-write verification
  mark outbox processed
```

## 一致性语义

- PostgreSQL 写入成功、Neo4j 暂时失败：事实已存在，图查询存在短暂延迟；Outbox 负责重试。
- Neo4j 不可用：结构化事实查询可以降级，图路径查询返回图新鲜度状态。
- 重复消费：通过 `idempotency_key`、实体稳定 ID 和 `claim_id` 保证幂等。
- Claim 被替代或拒绝：投影事件更新或移除对应物化经营边。

## 后果

### 正面

- 事实写入具备单库事务；
- 财务数据适合 SQL 计算；
- 证据与审计模型清晰；
- Neo4j 可替换、可重建；
- 图写入失败不会丢失正式事实；
- 可清楚定义权威来源。

### 代价

- 需要维护投影器和 Outbox；
- 查询需要在图和关系库之间编排；
- 存在最终一致性延迟；
- 必须建立持续对账机制。

## 被否决的方案

### 方案A：Neo4j 作为唯一数据库

否决原因：

- 不适合保存和计算大量财务观察值；
- 原始接口响应、任务和审核事务复杂；
- 证据文本与向量存储不自然；
- 图模型升级可能影响事实持久化。

### 方案B：只使用 PostgreSQL，不使用 Neo4j

否决原因：

- 多跳路径和图解释开发成本较高；
- 产品层级、产业链和控制关系查询不够直观；
- 后续图分析与可视化扩展受限。

### 方案C：PostgreSQL 与 Neo4j 双写且同步提交

否决原因：

- 需要跨数据库分布式事务或复杂补偿；
- 故障模式多；
- MVP 阶段投入与收益不匹配。

## 验收约束

- 删除 Neo4j 后可从 PostgreSQL 完整重建；
- Accepted Claim 投影完整率不低于 99.9%；
- 任何经营类图边都必须有 `claim_id`；
- 图谱对账差异必须可解释；
- Outbox 重复消费不得产生重复事实；
- PostgreSQL 与 Neo4j 的职责不得在业务代码中混淆。
