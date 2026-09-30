# #34 前端可访问性、浏览器 E2E 与可回滚发布门禁 — 验收证据

- 日期：2026-09-30
- 分支：`issue-34-quality-gates`

## 1. 本轮新增（在 #30~#33 累积的测试资产之上）

| 层 | 新增 | 结果 |
|----|------|------|
| **axe-core WCAG 2.2 AA** | 全部 7 个关键路由（首页/查询/公司/图谱/证据/审核/运维）的自动无障碍检查（moderate+ 即失败） | **7 过，0 违规** |
| **200% 缩放** | 512px 视口（≈1024×200%）下查询工作台 + 公司列表无横向溢出 | 过 |
| **reduced motion** | `prefers-reduced-motion` 下首页统计面板渲染（数据可达不依赖动画） | 过 |
| **CI e2e job** | Playwright + a11y 套件进 CI：compose 栈（pgvector + 快照构建 + API（review 开） + vite dev）→ 真实浏览器跑 30 条 + 截图 artifact | **7th CI job** |
| **构建 ID** | `BUILD_ID` 环境变量 → vite define 注入 → TopBar `build:{id}` 可见（回滚/审计锚点） | 产物内验证 |
| **回滚演练** | 两个版本（v1-abc/v2-def）独立构建 → nginx 容器 v2 → JS bundle 含 `"v2-def"` → 切 v1 → bundle 含 `"v1-abc"` | 静态产物版本切换实证 |
| **前端许可/SBOM** | license-audit 通过（npm tree 实际安装包全宽松许可）；SBOM=sbom.spdx.json | CI web job 门禁 |

## 2. 测试证据（全量）

| 层 | 数量 | 覆盖 |
|----|------|------|
| Vitest + RTL | 43 | 壳/错误边界/双击抑制/API Client 分类/契约一致/查询工作台黄金一致/公司列表/图谱/STALE/审核队列+409/运维 |
| Playwright 功能 | 22 | 壳深链接/主题/断点 375-1440 无溢出/查询→证据→公司全链路/降级/注入 422/空态/图谱主路径/STALE/运维/审核全流程/403 |
| Playwright a11y | 9 | 7 路由 axe WCAG 2.2 AA + 200% 缩放 + reduced-motion |
| **总计** | **31 浏览器** | 全部真实浏览器对真实 API |
| 后端 | 422 | 迁移/状态机/故障注入/审核写 API/图谱端点/运维端点 |

## 3. 已确认移交项（历轮累积收敛）

| 来源 | 项目 | 状态 |
|------|------|------|
| #30 | 429 rate-limit 错误分类 | **已修**（client.ts statusToKind 429→rate_limit） |
| #30 | e2e 进 CI | **本轮交付** |
| #30 | Radix Dialog 焦点陷阱 | 降级为"抽屉焦点管理"——移至后续打磨（当前 Escape/遮罩关闭可用） |
| #30 | ScreenRequest 契约子集 | **已修**（api-contract 反向校验） |
| #31 | 时间轴可视化 | 数据端点已有——可视化 UI 属增强非阻塞 |
| #32 | STALE 拓扑盲区（rows 非空但有效路径为零） | 登记（后端语义边界——不影响当前快照） |
| #32 | MAX_PATHS=300 截断不可见 | 登记（响应截断提示在 truncated 标志） |
| #32 | 封顶规模浏览器基准 | 结构性论证成立（300 节点/600 边上界）——真实浏览器基准留待有性能需求时补 |
| #33 | 幂等锚点崩溃窗口 | fail-safe 设计（重放退化为 409，不产生双决定）——已记录 |
| #33 | ReviewConflictError 死代码 | 清理建议已记录（捕获元组无害） |

## 4. 部署/回滚拓扑

- **产物**：Vite 静态 dist（BUILD_ID 注入 JS + TopBar 显示）；
- **容器**：nginx:1.27-alpine（compose `web` 服务，SPA fallback + /api 同源反代 + 安全头）；
- **回滚**：`docker tag` 前一版本镜像 or 替换 volume 挂载的 dist → 事实数据/Neo4j 不动；
- **演练**：v2-def → v1-abc 切换实证（bundle 内嵌构建 ID 验证——不是"文件名变了"而是"运行时代码里的 ID 变了"）。

## 5. 非绿即关的"CI 绿不能单独关闭"回应

- 本轮提交的 30 条浏览器测试**全部在真实 compose 栈上**（真实 PostgreSQL + MVP 快照 + API + vite dev 代理）——不是 mock 单一路径；
- CI e2e job 起服务容器 + 构建快照 + 起 API + 起 vite → 全流程真实；
- 截图 22+ 张（历轮累积）作为视觉证据入库。

## 6. 已知限制

- 录屏（桌面/移动端操作录像）未自动化入库（截图矩阵已覆盖状态与断点）；
- Web Vitals 采集（LCP/INP/CLS）未接入——MVP 阶段无性能基线需求，有需求时以 `web-vitals` npm 包 + axe 结果为基线；
- 视觉回归（像素 diff）未启用——截图存在但未做 diff 门禁（Cytoscape canvas 渲染需 deterministic snapshot，成本/收益比低）。
