# #32 产品图谱、推理路径与文档证据浏览器 — 验收证据

- 日期：2026-09-30
- 分支：`issue-32-graph-evidence`

## 1. 后端新增（受控只读端点）

| 端点 | 说明 |
|------|------|
| `GET /api/v1/graph/subgraph` | 公司中心子图：白名单 Cypher 模板（`__HOPS__` 1..2 渲染变体入 executor 白名单），参数校验先于图可用性（缺 company_id/越界 hops → REJECTED 不猜测）；节点预算 1-300 强制，超限 truncated=True + 说明；executor None/异常 → DEGRADED + 原因 |
| `GET /api/v1/claims/{id}/lineage` | Claim → Evidence（页码/原文/字符区间）→ Document（来源/类型/版本/解析状态）追溯链；版本缺失如实返回 None 不伪造 |
| `GET /api/v1/documents/{id}/evidence` | 文档证据片段列表 + 版本状态 |

后端测试 8 个（tests/db/test_graph_endpoints.py：SUCCEEDED 白名单执行/无执行器降级/图异常降级/参数拒绝/裁剪/lineage 全链/文档证据/只读拒绝）。

**修复（真图验证发现）**：
- 子图模板 `{{}}` format 转义残留 → Cypher 语法错误（真 Neo4j CypherSyntaxError 实证）→ 改 `__HOPS__` 替换；
- 图节点属性名对齐（投影写 `name`，模板 coalesce(name, canonical_name)）；
- executor 白名单扩展子图 hops 变体；
- 快照构建公司投影 props `canonical_name`→`name`（与图读取一致）。

实测（快照辛示机器人01号）：SUCCEEDED，**14 节点 21 边，center=辛示机器人01号**。

## 2. 前端交付

- **图谱浏览器** `/graph`：公司选择器（复用 #31 列表端点）+ 跳数 1/2 + Cytoscape 按需挂载（concentric 布局、节点色=实体类型但**并附图例文字**——不只靠颜色）；**表格替代视图**（radio 切换，role=table + caption，边/claim_id/节点清单全量承载——Epic 不变量：图谱不是唯一信息表达）；点击带 claim_id 的边/表格"查看证据链"→ 证据抽屉。
- Cytoscape 无 canvas 环境（jsdom/旧浏览器）优雅降级：提示使用表格视图（不让错误边界吃掉整页——测试实证）。
- **证据抽屉**：Claim（谓词/状态/阶段/置信度/双时态）→ 证据片段（页码/字符区间/原文引用）→ 来源文档（来源/类型/版本/解析状态）；缺失状态如实呈现（"无关联证据片段"/"文档记录缺失"），不回退到不受信任文本。
- **证据浏览器** `/evidence`：按文档 ID 浏览全部片段。
- 能力导航 #32 里程碑解锁（图谱=Neo4j 组件门禁、证据=apiGate）。

## 3. 测试证据

| 层 | 结果 |
|----|------|
| 后端（新 8 + 全量） | **409 passed** |
| Vitest + RTL（新 5：降级显示+原因+PG 提示/裁剪说明/表格视图+证据链抽屉/初始空态/文档证据页码原文版本；更新 shell 测试至 #32 里程碑语义） | **36 passed** |
| Playwright（新 4：图谱主路径→表格→证据抽屉/降级模拟/证据浏览器/深链接刷新） | **18 passed** |
| tsc/eslint/build | 绿 |

关键实证（E2E 真实快照）：辛示机器人01号子图 SUCCEEDED 14 节点；表格视图承载 PRODUCES 边 + claim_id；证据抽屉显示第 N 页原文 + CNINFO/SIN 来源；降级（mock ServiceUnavailable）→ DEGRADED 横幅 + PG 事实提示。

## 4. 500/2,000 节点 PoC（issue 要求）

| 档 | 后端展开+去重 | 返回载荷 | 结论 |
|----|--------------|---------|------|
| 500 节点 fixture | 0.2ms | 26.3 KB（300 节点预算内截断） | 预算封顶——超限部分不进浏览器 |
| 2,000 节点 fixture | 0.5ms | 57.1 KB（同上） | 同上；浏览器渲染规模恒定 ≤300 节点 |

结论：节点预算（≤300）使浏览器载荷与全图规模解耦；超限返回 truncated 说明 + 引导下钻（缩小跳数/从具体公司），不把全图一次传到浏览器。

**PoC 边界（诚实声明）**：以上数字来自后端展开/裁剪的 stub fixture——
issue 要求的"交互、内存、布局时间"（真实浏览器上渲染封顶后的 300 节点
Cytoscape）**未在本轮实测**；载荷被预算硬封顶（26-57KB）使浏览器冻结
在结构上不可发生，但真实浏览器基准移交 #34（"封顶规模真实浏览器基准"
已登记）。同样未测：真实 Neo4j 在 2000 节点图上执行可变长路径的
服务端成本（当前 LIMIT 前路径展开成本依赖图规模）。

## 5. 截图（web/screenshots/）

`graph-table-view.png`（表格替代视图+节点清单）、`evidence-drawer.png`（证据追溯抽屉）、`graph-degraded.png`（图后端降级）、`evidence-from-query.png`（查询结果展开证据）。

## 6. Cytoscape.js 供应链（issue 要求）

- 版本：3.34.3（package-lock 固定）；许可：MIT（license-audit 全过）；
- 扩展：零扩展（核心渲染 + 内置 concentric 布局即可满足 MVP）；
- 替换条件：若需要力导向布局/webgl 大规模渲染，引入对应扩展或换 Sigma.js——组件隔离在 `CytoscapeView` 单组件内，替换面小。

## 7. 已知限制（移交 #34）

- 图谱缩放/平移控件与全屏模式（#34 可访问性与性能轮统一打磨）；
- **边端点数据模型**：后端边当前不含 start/end 端点，Cytoscape 边渲染为
  中心扇形示意（自认装饰性）——真实拓扑连接由表格视图承载（claim_id
  可回溯）。边端点入 edge_path 后可渲染真实连线（#34 图谱打磨项）；
- 移动端图谱默认切表格视图（canvas 触摸交互在 #34 覆盖）；
- **封顶规模真实浏览器基准**（交互/内存/布局时间——见 §4 PoC 边界）；
- 抽屉焦点管理（Escape/焦点陷阱——与 #30 移交"焦点陷阱"合并到 #34）；
- 空图/过期投影截图（本轮已补 STALE 机制测试；截图矩阵补齐在 #34）。

### 移交登记（历轮累积，#34 需显式纳入）

- #31 → 时间轴可视化（/companies/{id}/timeline 数据端点未可视化）；
- #30 → 429 rate-limit 错误分类、e2e 进 CI（Playwright job）、
  Radix Dialog 焦点陷阱；
- #32 → 边端点数据模型、封顶规模浏览器基准、e2e 截图矩阵补齐。
