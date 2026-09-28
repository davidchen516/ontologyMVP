---
title: 测试与验收
parent: 开发与治理
nav_order: 1
permalink: /testing-and-acceptance.html
---

# 测试与验收

## 当前校验覆盖

当前 GitHub Actions 已分为三条流水线：

- **Runtime CI**：按 `uv.lock` 冻结安装，运行 Ruff、pytest、设计资产校验和 Gitleaks；
- **Validate design assets**：独立运行 `scripts/validate_design.py`；
- **GitHub Pages**：严格构建文档，只有 `main` 可以部署。

设计资产校验覆盖：

- 必需设计文件存在；
- Turtle 语法；
- YAML 语法；
- README 和 `docs/` 下的本地 Markdown 链接。

当前 pytest 覆盖配置快速失败、健康/就绪状态、日志脱敏、`trace_id`、Worker 生命周期、依赖不可达故障注入和 Semantica 导入边界。这些检查保证工程基线行为，但不访问真实 TuShare，也不替代真实 PostgreSQL/Neo4j、数据库迁移、并发事务或完整业务链路验收。

## 完整测试层次

| 层次 | 主要目标 |
|---|---|
| 单元测试 | 映射、时间、Hash、状态机、QueryPlan、白名单编译 |
| 契约测试 | TuShare、Semantica、Neo4j 与外部文档源的真实接口边界 |
| 集成测试 | Raw → Standard → Claim → Outbox → Graph → Query |
| 黄金查询 | Precision、Recall、Evidence Grounding、路径和时态正确性 |
| 故障注入 | 事务中断、Worker 崩溃、重复/乱序、恢复和降级 |
| 安全测试 | Secret、依赖、权限、输入注入和外部副作用 |
| 性能测试 | 查询延迟、重建、批处理和积压阈值 |

## 核心关闭逻辑

### 状态机

Given 一条记录处于当前状态，When 请求合法或非法迁移，Then：

- 只有文档定义的边可以成功；
- 非法迁移无部分写入；
- 终态回退或重试保留审计；
- 并发决定使用版本或锁检测冲突，不发生最后写入者静默覆盖。

适用对象包括 IngestRun、Document、Claim、ReviewTask、GraphOutbox 和 Query。

### 幂等与重复提交

Given 同一 Payload、Claim、审核请求或 Outbox 事件被重复处理，When 重放一次或多次，Then 权威事实数量不增加，最终状态一致，重复原因可观察。

### 原子事务

Given 接受 Claim 的任一步发生故障，When Evidence、Provenance、审计或 Outbox 写入失败，Then 整个事务回滚；恢复后重试不会形成半接受事实。

### 崩溃与恢复

Given Worker 在持久化之后、更新游标或确认消息之前崩溃，When 进程重启并重放，Then 不丢批次、不重复事实，并能从持久化水位继续。

### 权限拒绝

Given Token 无权限、数据库角色只读或管理操作未授权，When 发起请求，Then 返回明确且不泄密的拒绝；不会通过重试绕过权限，也不会产生外部副作用。

### 回滚与重建

Given 新本体、映射、数据快照或图版本验收失败，When 切回上一版本，Then 历史 Claim/Evidence/Provenance 保留；清空 Neo4j 后可从 PostgreSQL 重建，黄金查询结果一致。

## 关闭 Issue 必须提交的证据

“CI 绿”或“测试通过”不是充分关闭条件。Issue 应同时具备适用的：

1. **自动化测试**：正常、错误、重复、并发、状态机非法迁移和边界值；
2. **真实运行**：在隔离环境运行真实数据库或真实只读依赖，记录版本和数据快照；
3. **故障注入**：在关键提交点、确认点和外部调用处验证恢复；
4. **监控与审计**：指标、日志、`trace_id`、对账报告或告警样例；
5. **外部副作用检查**：证明 CI 没有真实交易、发布、邮件、共享数据库写入或额度消耗；
6. **回滚演练**：说明切回版本、恢复数据或全量重建的结果；
7. **已知限制**：明确未覆盖范围、降级语义和风险接受人。

## MVP 指标

| 指标 | 目标 |
|---|---:|
| Company/Security 映射准确率 | ≥ 99% |
| 黄金产品映射准确率 | ≥ 95% |
| Accepted 文本 Claim 原文可定位率 | 100% |
| Accepted 经营关系含 `claim_id` | 100% |
| 图谱可重建率 | 100% |
| PostgreSQL/Neo4j 对账一致率 | ≥ 99.9% |
| 黄金查询 Precision | ≥ 90% |
| 黄金查询 Recall | ≥ 85% |

完整策略见[质量、安全与测试方案](quality-and-testing.html)与 [Issue #10](https://github.com/davidchen516/ontologyMVP/issues/10)。
