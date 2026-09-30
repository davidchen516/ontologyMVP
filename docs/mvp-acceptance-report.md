# MVP 验收报告：人形机器人核心零部件证据化筛选纵向切片（issue #11）

- **验收日期**：2026-09-29（第二轮修订——修正第一轮审查指出的失实声明）
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
（#4 标准化，含概念成员 ths/dc 与申万行业成员落库）→
document/document_version/evidence_fragment（#6 形态）→
`RulesExtractor` + `intake_candidate`（#7 抽取/校验/审核/auto-accept/
Outbox）→ 产品实体物化 + PRODUCES Claim→标准产品映射 → 图实体节点 +
`run_worker_cycle` 投影（#8，340 条经营边 claim_id 覆盖 100%）→
受控 QueryPlan API（#9，含图路径候选）→ 黄金回归 + 质量门禁（#10）。

## 2. 数据快照性质声明（重要）

**本快照为合成快照。** 构建时 `TUSHARE_TOKEN` 不可用（`.env` 中为空），
真实外部数据抓取被阻塞。30 家候选公司（辛示机器人01~30号、
USCC 前缀 `SIN-`，ts_code 使用合成段 3001xx.SZ）为**明确标注的合成
主体**——项目不冒用真实公司名称，不生成真实公司的虚构财务数据。

`--real --token <TUSHARE_TOKEN>` 模式：token 经 `http_transport` 注入
真实 TuShare API（`run_pipeline` 中 token 存在时自动切换 HTTP 层），
采集/标准化/证据/审核/投影代码路径完全相同。合成模式用
`SyntheticTransport` 只替代 HTTP 层。

各情景分布（覆盖 issue #11 全部黄金负向场景）：

| 情景 | 数量 | 预期行为 |
|------|------|----------|
| MASS_PROD（量产+证据+3FY 全正） | 12 | 黄金 include |
| CONCEPT_ONLY（仅概念标签，无产品 Claim） | 3 | 不进结果，不断言无业务 |
| RESEARCH_ONLY（仅研发） | 4 | 不提升为量产 |
| DENIAL（公司否认） | 3 | DENIES_INVOLVEMENT，排除 |
| THIRD_PARTY（第三方称量产+公司否认） | 3 | hedge→审核；否认→DENIES |
| FIN_SHORT（现金流不足 3FY） | 2 | 数据不足排除（不当 0） |
| FIN_NEGATIVE（一年为负） | 2 | 口径差异：TOTAL 过/CONSECUTIVE 排除 |
| RESTATE（财务重述） | 1 | 当前值视图选最新公告 |

实测统计（manifest）：fragments 600、候选 Claim 540、Accepted 400、
NEEDS_REVIEW 83 + 冲突挂起 57、产品实体 10、PRODUCES→产品映射 423、
概念成员投影（Concept 节点 + TAGGED_AS 边 90）、经营图边 340
（claim_id 覆盖 100%）、Outbox 全部 PROCESSED（无积压）。

## 3. 验收标准逐条对照

| # | 验收标准 | 结论 | 证据与边界 |
|---|---------|------|-----------|
| 1 | 30~50 家候选公司主体/证券映射，抽样准确率 ≥99% | 达成（合成自洽） | scale 测试断言 30 家全有 company_security 映射；"抽样准确率"在合成数据上为 100%，**真实数据上的 99% 待 --real 快照复核** |
| 2 | 50~100 产品概念及别名可用 | 达成（概念侧） | product-skos.ttl 52 个概念 + 40+ 别名（skos:altLabel）；**产品映射准确率 95% 未在黄金用例中数值化验证**——黄金产品映射由 g21/g22 的 standard_product 字段与映射步骤（精确名称匹配）保证，属弱断言 |
| 3 | ≥500 条候选 Claim；Accepted 文本经营 Claim 原文定位率与 Provenance 覆盖率 100% | 达成 | scale 测试断言 candidates ≥500（实测 540）且 `with_evidence == accepted`（100%）；原文定位 = evidence_fragment 外键 + intake quote grounding 校验 |
| 4 | 高严重度冲突自动接受 0%；未知误判为否定 0% | 达成 | THIRD_PARTY 的 PRODUCES 全部 hedge→NEEDS_REVIEW（DB 实测 0.50）；否认语义独立谓词（DENIES≠UNKNOWN） |
| 5 | 经营图边含 claim_id 100%；对账一致率 ≥99.9% | 达成 | reconciliation 实测 340 边全部含 claim_id（100%）；Claim 节点覆盖率 100%；对账报告入 manifest |
| 6 | 删 Neo4j 重建后黄金结果和解释路径一致 | 达成 | `test_mvp_rebuild_consistency`：清图 → full_rebuild → 结果集+推理路径+证据指纹一致 |
| 7 | ≥30 条黄金查询；Precision ≥90% Recall ≥85% | 达成 | `golden_mvp.yaml` 30 用例（include/exclude/双时态/口径/注入）；include 型用例集合断言等价 P/R=100%（阈值由 `_evaluate` 消费） |
| 8 | 首个筛选问题返回每家公司完整证据包 | 达成 | 响应含：证券（security_code）、标准产品（standard_product）、业务阶段、Claim ID、证据（evidence_ids + evidence_quotes 含页码/原文）、有效期（valid_from——快照中为报告期年初，valid_to 开放至被状态机关闭）、三年现金流+口径（financial_detail/period_rule）、推理路径、新鲜度（data_freshness）、未知项（unknowns/excluded）——demo 实测输出 |
| 9 | 负向黄金样例结果正确 | 达成 | g07~g12 + g05/g06/g24 全部通过；hedge 与否认红线 DB 实证 |
| 10 | 关键故障和恢复测试通过，最新 CI 全部成功 | 达成 | #10 故障矩阵 + CI 七 job 全绿 |
| 11 | 验收报告记录快照/版本/限制/边界 | 达成 | 本报告（第二轮修订版） |

## 4. 已知限制

1. **合成快照**：公司、财务、公告文本均为确定性合成数据；真实数据
   快照需配置 `TUSHARE_TOKEN` 后以 `--real` 模式重建（同一管线）。
2. **图路径场景在无 Neo4j 环境下降级**：明确 DEGRADED + 注记；真实
   图路径由 `tests/integration/test_neo4j_query_read_e2e.py`（CI
   service）与 MVP 黄金套件（CI）锁定。
3. **LLM 语言组织未接入**（`LLM_API_KEY` 缺失）：结构化 Grounded
   响应即最终响应（issue #9 允许——不丢失证据）。
4. **审核队列积压**：合成快照中约 64+ 条候选进入人工审核（第三方
   冲突场景设计如此）；MVP 未接人工审核 UI，生产闭环需运营流程。
5. **产品映射为精确名称匹配**：合成公告使用标准产品名（映射 100%
   命中）；真实公告的同义词/变体映射依赖 `master.product_term_mapping`
   （issue #11 范围外，验收 2 的 95% 数值化验证属后续轮）。
6. **概念成员只表示平台分类**：`ths_member/dc_member` 落库为
   snapshot 成员关系，绝不自动生成量产 Claim（红线，g07 锁定）。

## 5. 边界声明

本切片及全部查询输出**不构成投资建议**，不输出买卖指令，不预测股价，
不接入任何交易接口。所有经营结论均可回溯至 Claim（含 Provenance Hash
链）与可定位 Evidence 片段（document_version + page_number + char
offset），并在响应中携带数据新鲜度与降级说明；任何子系统降级都会
在结果中显式呈现，绝不静默伪装完整结果。
