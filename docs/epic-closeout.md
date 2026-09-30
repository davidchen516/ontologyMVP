# Epic 收尾报告：V0.1 股票本体查询系统可运行 MVP（issue #12）

- **收尾日期**：2026-09-30
- **状态**：#1~#11 全部关闭（见第 2 节清单），Epic 验收标准逐条核对见第 3 节
- **详细业务验收**：`docs/mvp-acceptance-report.md`（issue #11 逐条矩阵）

## 1. Epic 交付总览

按依赖顺序交付的可运行、可审计、可恢复 MVP：

| 层 | Issue | 交付 | 合并 |
|----|-------|------|------|
| 基础 | #1 | 工程基线（uv 锁定、FastAPI/Worker、compose、结构化日志） | #13 |
| 基础 | #2 | PostgreSQL Schema + Alembic + 事务边界（9 迁移） | #18 |
| 结构化 | #3 | TuShare 探针/Connector/限流/断点续跑/Raw 幂等 | #19 |
| 结构化 | #4 | 主数据/概念/主营构成/财务标准化 + 重述选择规则 | #20 |
| 语义 | #5 | Semantica 0.7.0 适配边界 + 持久化 Provenance Hash 链 | #21 |
| 证据 | #6 | 官方披露采集、版本化解析、EvidenceFragment | #22 |
| 治理 | #7 | Claim 抽取/Grounding/冲突/审核状态机 | #23 |
| 图 | #8 | Outbox Worker、Neo4j 幂等投影、对账与全量重建 | #24 |
| 查询 | #9 | 受控 QueryPlan、图/财务联合查询、证据化 API + 审计 | #25 |
| 质量 | #10 | CI 门禁（覆盖率/契约/故障矩阵/黄金回归/依赖审计） | #26 |
| 业务 | #11 | 人形机器人核心零部件证据化筛选纵向切片 | #27 |

## 2. 子 Issue 关闭状态

`gh issue list --state open` 仅剩本 Epic（#12）。#1~#11 全部以
「实现 → 测试全绿 → PR → CI 全绿 → 独立审查（含整改轮）→ APPROVE →
squash 合并 → 关闭」闭环关闭。审查共拦截并整改 14 个 BLOCKER 级缺陷
（#4×2、#5×1、#6×5、#7×4、#8×4+#系统性ID、#9×4+#1、#10×1+#1、
#11×4+1+#2——计数按轮累计），全部有整改提交与复审实证。

## 3. Epic 验收标准逐条核对

| # | 验收标准 | 结论 | 证据 |
|---|---------|------|------|
| 1 | #1~#10 均按各自验收标准关闭 | ✅ | 各轮 goal-state 记录 + GitHub issue 状态 |
| 2 | #11 端到端业务验收通过 | ✅ | `docs/mvp-acceptance-report.md`（三轮审查后 APPROVE） |
| 3 | 最新 CI 所有必需检查成功 | ✅ | runtime-ci（build/test/db/quality/secret-scan）+ validate-design 全绿；覆盖率门禁 88%（实测 90%+）；pip-audit 零豁免（1 条登记的规则误报豁免）；gitleaks 通过 |
| 4 | 状态机/负向/并发/崩溃/恢复/副作用自动化证据 | ✅ | tests/db/test_state_machines（7 台状态机合法+非法迁移）、tests/db 故障矩阵（事务中断/并发审核/租约 fencing/乱序守卫/超时/审计幂等）、tests/mvp（30 黄金 + 重建一致性）、tests/integration（真实 Neo4j 读写路径 e2e） |
| 5 | PostgreSQL 事实 ↔ Neo4j 投影 ↔ API 响应 ↔ Evidence 端到端追溯 | ✅ | `tests/db/test_epic_traceability.py`：PG 事实（claim+fragment+页码）→ Neo4j Claim 节点+带 claim_id 经营边 → API claim_ids/evidence_quotes 回链 → 事实库可定位（四环断言） |
| 6 | 验收报告明确快照/版本/局限/边界 | ✅ | mvp-acceptance-report §2 合成快照声明、§3 版本清单、§4 已知限制（6 项）、§5 非投资建议边界 |

## 4. 跨 Issue 核心不变量达成

- **事务原子性**：accept_claim 单事务（Claim+Evidence+Provenance+审计+Outbox），任意步失败回滚（test_claim_accept_tx）。
- **幂等**：重复采集（payload_hash）、重复标准化（水位+唯一键）、重复抽取（content_hash）、重复消费（Outbox MERGE）四级不重复事实（#11 审查实测 540→540）。
- **图边溯源**：经营边 100% 带 claim_id（#11 实测 340/340），且可回 PG。
- **证据/Provenance 覆盖 100%**：ACCEPTED 文本经营 Claim（scale 断言 with_evidence == accepted）。
- **冲突/未知红线**：高严重度冲突自动接受 0%（hedge→0.5→NEEDS_REVIEW 实测）；未知误判否定 0%（DENIES/UNKNOWN 谓词分离）。
- **重建恢复**：删图 → full_rebuild → 黄金指纹（结果/推理路径/证据）一致。
- **CI 离线**：全部测试无真实 TuShare/LLM/外网；CI service 容器隔离。

## 5. 遗留与后续（非阻塞）

- 合成快照 → 真实数据快照：配置 `TUSHARE_TOKEN` 后 `--real` 重建（同一管线）。
- 人工审核 UI/运营流程（快照审核队列 83+57 条待审）。
- 产品映射 95% 准确率的数值化验证（需真实公告语料）。
- 查询异步执行/取消路径（Query 状态机 CANCELLED 预留）。
- #8/#9/#10 各轮登记的非阻塞观察项（见 goal-state.md）。

## 6. 边界声明

系统不预测股价、不提供投资建议、不自动交易；全部经营结论可回溯至
Claim（Provenance Hash 链）与可定位 Evidence（document_version +
page_number + char offset），降级与数据新鲜度在响应中显式呈现。
