# #31 查询、公司分析与 Grounded 结果研究工作台 — 验收证据

- 日期：2026-09-30
- 分支：`issue-31-research-workbench`
- 设计基线：`docs/product-ui.md`（main 维护者版）

## 1. 后端新增（受控只读端点）

| 端点 | 说明 | 测试 |
|------|------|------|
| `GET /api/v1/companies` | 公司列表/搜索/分页：参数化 LIKE（`%`/`_` 通配符转义为字面量）+ stage 过滤 + 确定性排序 + 每行证券代码与 PRODUCES Claim 计数 | 7 个 DB 测试（默认列表/搜索转义/过滤分页/只读拒绝/统计/最近运行/详情回归） |
| `GET /api/v1/overview/stats` | 首页真实统计（公司/Claim/证据/财务计数 + 数据新鲜度 + 快照说明——Epic 不变量：统计携带说明不伪装真实数据） | 全部来自事实库真实计数 |
| `GET /api/v1/overview/recent-runs` | 最近采集/标准化运行（真实记录） | |

修复（E2E 发现）：`/screen|/query` 响应体 trace_id 在无外部请求头时为
null——改为回落 TraceIdMiddleware 自动生成的 trace_id（每请求必有、
与 X-Trace-Id 响应头一致），UI 的 trace_id 展示从此始终有效。

## 2. 前端交付

- **查询工作台** `/workbench/query`：受控结构化表单（业务阶段枚举/概念/
  财务指标白名单/算子/阈值条件显示/财年窗口/证据要求）；MIN/MAX 无阈值
  **字段就地报错不发请求**；结果区分区展示 results/excluded/unknowns/
  conflicts/degradation_notes/period_rule/trace_id（Epic 不变量：不能只
  展示命中）；每条结果可展开证据包（Claim ID/页码原文/推理路径/财务
  明细/有效期/新鲜度）。
- **公司列表** `/workbench/companies`：搜索（可分享 URL 参数）/分页/
  表格（名称链接/证券/类型/量产计数）。
- **公司详情** `/workbench/companies/:id`：基本信息 + Claim 表（谓词/
  状态/阶段/置信度/双时态）+ ACCEPTED 时间线；深链接刷新可复现。
- **首页升级**：真实统计卡（5 指标 + 新鲜度 + 合成快照说明）+ 最近活动
  （真实运行）+ 快捷入口。
- 能力感知导航更新：查询工作台/公司列表进入 #31 里程碑（apiGate）。

## 3. 测试证据

| 层 | 结果 |
|----|------|
| 后端 DB 测试（新 7 + 全量） | **401 passed** |
| Vitest + RTL（新 6：黄金筛选逐字段一致/空结果/422 就地报错不发猜测请求/MIN_VALUE 前置校验/重复点击抑制/公司列表+搜索空态） | 30 passed 全绿 |
| Playwright 真实浏览器（新 5：主路径查询→证据展开→公司列表→详情→深链接刷新/首页真实统计/空结果无虚构/422 注入拒绝/搜索空态） | **15 passed**（含 #30 的 10） |
| TypeScript strict + ESLint + 生产构建 | 通过（316.4 kB gzip 100.1 kB） |

关键实证（E2E 断言，非仅单测 mock）：
- 黄金筛选在真实快照上：SUCCEEDED（有图环境）状态 + `LAST_3_FY_TOTAL_POSITIVE`
  口径 + trace_id + 证据页码原文 + 推理路径全部渲染。
- 空结果（TECHNOLOGY_RESERVE 无匹配）：显示筛选条件说明 + 排除原因，
  **无虚构推荐**（`辛示机器人` 链接计数为 0 断言）。
- 注入文本 `'; DROP TABLE fact.claim; --`：后端 422 + UI 受控错误 +
  trace_id；只发一次请求（无第二"猜测"请求）。
- 公司搜索：URL 参数（非敏感条件可分享）+ 命中/空态。
- 深链接 `/workbench/companies/{id}` 刷新可复现。

## 4. 截图（web/screenshots/）

`workbench-query-grounded.png`（黄金结果+证据展开）、`company-detail.png`、
`home-with-stats.png`（真实统计卡）、`workbench-empty.png`（空结果）、
`workbench-422.png`（注入拒绝）+ #30 的 6 张。

## 5. 已知限制（移交 #32/#34）

- 公司详情"时间线"区当前仅显示计数（可视化时间轴在 #32 证据浏览器）。
- 概念字段为文本输入（无自动补全——概念列表端点属 #32 图谱浏览器范围）。
- 性能/可访问性正式报告在 #34（本轮键盘 Tab 顺序与 375px 无溢出已由
  #30 套件覆盖）。
