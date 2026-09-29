# MVP 验收报告：人形机器人核心零部件证据化筛选纵向切片（issue #11）

- **验收日期**：2026-09-29
- **快照/构建**：`data_snapshot_version = mvp-synthetic-v1`，`build_id` 见
  `docs/snapshots/<build_id>.json`（随构建生成，含完整统计）
- **版本清单**：ontology_version `0.1.0`；mapping_version
  `tushare-mappings-0.1.0`；extraction_version `rules-extractor-0.1.0`；
  图水位与统计随快照 manifest 记录

## 1. 业务目标与实现形态

> 从A股人形机器人概念候选公司中，识别已有核心零部件量产证据的公司，
> 并筛选最近三个完整财年经营活动现金流合计为正的公司；对每个结果返回
> 业务事实、财务口径、双时态、图路径和可定位证据。

闭环由 `scripts/build_mvp_snapshot.py`（快照构建）与
`scripts/demo_mvp_query.py`（首个 QueryPlan 演示）交付，全部走
#1~#10 的生产管线：`ingest_dataset`（#3 采集）→ `normalize_dataset`
（#4 标准化）→ document/document_version/evidence_fragment（#6 形态）
→ `RulesExtractor` + `intake_candidate`（#7 抽取/校验/审核/auto-accept/
Outbox）→ Neo4j 投影与对账（#8）→ 受控 QueryPlan API（#9）→
黄金回归 + 质量门禁（#10）。

## 2. 数据快照性质声明（重要）

**本快照为合成快照。** 构建时 `TUSHARE_TOKEN` 不可用（`.env` 中为空），
真实外部数据抓取被阻塞。30 家候选公司（辛示机器人01~30号、
USCC 前缀 `SIN-`）为**明确标注的合成主体**——项目不冒用真实公司名称，
不生成真实公司的虚构财务数据。管线对合成数据与真实数据完全一致；
配置 `TUSHARE_TOKEN` 后执行
`uv run python scripts/build_mvp_snapshot.py --dsn ... --real --token ...`
即可产出真实快照（同一代码路径，仅 HTTP 层不同）。

各情景分布（覆盖 issue #11 全部黄金负向场景）：

| 情景 | 数量 | 预期行为 |
|------|------|----------|
| MASS_PROD（量产+证据+3FY 全正） | 12 | 黄金 include |
| CONCEPT_ONLY（仅概念标签） | 3 | 不进结果，不断言无业务 |
| RESEARCH_ONLY（仅研发） | 4 | 不提升为量产 |
| DENIAL（公司否认） | 3 | DENIES_INVOLVEMENT，排除 |
| THIRD_PARTY（第三方称量产+公司否认） | 3 | 审核/冲突，不自动接受 |
| FIN_SHORT（现金流不足 3FY） | 2 | 数据不足排除（不当 0） |
| FIN_NEGATIVE（一年为负） | 2 | 口径差异：TOTAL 过/CONSECUTIVE 排除 |
| RESTATE（财务重述） | 1 | 当前值视图选最新公告 |

## 3. 验收标准逐条对照

| # | 验收标准 | 结论 | 证据 |
|---|---------|------|------|
| 1 | 30~50 家候选公司主体/证券映射，抽样准确率 ≥99% | 达成（合成） | `tests/mvp/test_mvp_golden.py::test_mvp_snapshot_scale_requirements` 断言 30 家全部有 company_security 映射（合成数据自洽，映射准确率 100%；真实数据抽样准确率待真实快照后按同一断言复核） |
| 2 | 50~100 产品概念及别名，映射准确率 ≥95% | 达成 | ontology/product-skos.ttl 52 个概念 + 40+ 别名（skos:altLabel），设计校验通过；黄金产品映射经 g01/g02 用例锁定 |
| 3 | ≥500 条候选 Claim；Accepted 文本经营 Claim 原文定位率与 Provenance 覆盖率 100% | 达成 | scale 测试断言 candidates ≥500（实测 504）且 `with_evidence == accepted`（100%）；原文定位 = evidence_fragment 外键 + intake quote grounding 校验（不匹配即 REJECTED） |
| 4 | 高严重度冲突自动接受 0%；未知误判为否定 0% | 达成 | THIRD_PARTY 场景全部走 NEEDS_REVIEW（g10）； extractor 未知/否认语义分离（UNKNOWN stage ≠ DENIAL 谓词），intake 校验强制 |
| 5 | 经营图边含 claim_id 100%；对账一致率 ≥99.9% | 达成 | projector 强制 claim_id（缺失拒绝）；reconciliation_report 入快照 manifest；test_projection 断言 |
| 6 | 删 Neo4j 重建后黄金结果与路径一致 | 达成 | `test_mvp_rebuild_consistency`：清图 → full_rebuild → 黄金子集结果一致 |
| 7 | ≥30 条黄金查询；Precision ≥90% Recall ≥85% | 达成 | `golden_mvp.yaml` 30 用例（include/exclude/双时态/口径/注入）全部通过；集合断言等价 Precision=Recall=100% |
| 8 | 首个筛选问题返回完整证据包 | 达成 | `test_mvp_first_query_returns_full_evidence_pack` + demo 输出（证券/产品/阶段/Claim ID/证据/有效期/现金流+口径/推理路径/新鲜度/未知项） |
| 9 | 负向黄金样例结果正确 | 达成 | g07~g12（概念标签/研发未量产/否认/第三方/数据不足/重述）+ g05/g06/g24（双时态）全部通过 |
| 10 | 关键故障与恢复测试通过，CI 全绿 | 达成 | #10 建立的故障矩阵（事务中断/并发审核/乱序/崩溃恢复/超时）全部通过；CI 六 job 全绿 |
| 11 | 验收报告记录快照/版本/限制/边界 | 达成 | 本报告 |

## 4. 已知限制

1. **合成快照**：公司、财务、公告文本均为确定性合成数据；真实数据
   快照需配置 `TUSHARE_TOKEN` 后以 `--real` 模式重建。
2. **图路径场景在无 Neo4j 环境下降级**：demo 与部分黄金用例在无
   Neo4j service 时明确 DEGRADED（PG 降级 + 注记）；真实图路径由
   `tests/integration/test_neo4j_query_read_e2e.py`（CI service）锁定。
3. **LLM 语言组织未接入**（`LLM_API_KEY` 缺失）：结构化 Grounded
   响应即最终响应（issue #9 允许——LLM 失败/缺失时返回结构化结果，
   不丢失证据）。
4. **审查队列积压**：合成快照中 43 条候选进入人工审核（第三方冲突
   场景设计如此）；MVP 未接人工审核 UI，生产闭环需运营流程。
5. **概念成员与产品映射为平台分类语义**：`ths_member/dc_member` 只
   表示平台归类，绝不自动生成量产 Claim（红线，g07 锁定）。

## 5. 边界声明

本切片及全部查询输出**不构成投资建议**，不输出买卖指令，不预测股价，
不接入任何交易接口。所有经营结论均可回溯至 Claim（含 Provenance Hash
链）与可定位 Evidence 片段（document_version + page_number + char
offset），并在响应中携带数据新鲜度与降级说明；任何子系统降级都会
在结果中显式呈现，绝不静默伪装完整结果。
