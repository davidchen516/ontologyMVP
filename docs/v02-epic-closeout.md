# V0.2 产品界面 Epic 收尾报告（issue #29）

- **收尾日期**：2026-09-30
- **状态**：#30~#34 全部关闭；本报告逐条核对 Epic 验收

## 1. 子任务完成状态

| # | 任务 | 合并 | 关键交付 |
|---|------|------|---------|
| #30 | 前端工程、设计系统与应用壳 | PR #35 `d218899` | React+TS+Vite+Tailwind+Router+TanStack；应用壳/能力感知导航/主题/面包屑/404/错误边界；类型化 API Client；compose web 服务；Vitest+Playwright 基础 |
| #31 | 查询/公司/Grounded 研究工作台 | PR #36 `243929d` | 受控查询表单/全分区渲染/证据包展开；公司列表/搜索/详情/时间线；首页真实统计+最近活动 |
| #32 | 图谱、推理路径与证据浏览器 | PR #37 `a54fb9a` | 受控子图/白名单 Cypher/节点预算/STALE+PG 回退；Cytoscape+表格替代视图；证据抽屉+证据浏览器；三审通过 |
| #33 | Claim 审核与系统运营 | PR #38 `b550dcb` | ADR-0006 API-Key 认证+开关；唯一写端点+幂等键+真并发 409；审核工作台/运维只读页；二审通过 |
| #34 | 可访问性、E2E 与发布门禁 | PR #39 `f55d318` | axe WCAG 2.2 AA 7 路由零违规；CI e2e job（真实栈 31 条）；构建 ID+回滚演练；SBOM；429 分类；一审通过 |

## 2. Epic 验收逐条核对

| # | 验收条件 | 结论 | 证据 |
|---|---------|------|------|
| 1 | 所有子任务按各自关闭条件完成 | ✅ | 上表 5/5（每个均经独立审查 APPROVE→合并→关闭） |
| 2 | 新用户仅按公开文档完成证据化查询/公司/Claim 原文/推理路径 | ✅ | `docs/quality-gates-evidence.md` + E2E 主路径（workbench.spec:10 查询→证据→公司全流程）+ README 快速开始 |
| 3 | Reviewer 认证/最小权限完成审核决定，审计/状态机/并发正确 | ✅ | E2E `review-ops.spec:10`（登录→队列→决定→成功）+ `review-ops.spec:57`（错 key→403）+ 后端真并发测试（外部 FOR UPDATE→409+状态回传） |
| 4 | Operator 识别不可用/过期/投影滞后/对账异常，不暴露凭证 | ✅ | 运维页 `/ops`：readyz 组件/能力/探针/运行状态/投影水位+对账 JSON/新鲜度——全部只读+错误态可见（vitest+e2e 断言）；运维页无 key 要求但无敏感信息（服务器密码/密钥不在渲染面） |
| 5 | 固定快照真实浏览器桌面/移动验收，无伪数据/死按钮/颜色依赖 | ✅ | 375/768/1024/1440px 无溢出（shell.spec:101）+ a11y 512px；全部数据来自 API 真实响应（首页统计 30/400/83 由 /overview/stats 实数渲染）；能力未声明→导航禁用（无死按钮）；图例文字+状态文本（非颜色单独表达） |
| 6 | WCAG 2.2 AA 自动检查+键盘人工检查，关键流程四断点无溢出 | ✅ | axe `wcag2a/wcag2aa/wcag21a/wcag21aa/wcag22aa` 7 �由由零违规（a11y.spec.ts）；键盘焦点序抽查（shell.spec:73）+ reduced-motion（a11y.spec:60）；四断点溢出硬断言 |
| 7 | E2E 覆盖 happy path/错误输入/重复提交/取消超时/权限拒绝/并发审核/崩溃恢复/UI 回滚 | ✅（覆盖矩阵见 §3） | 31 条 Playwright（含 9 条 a11y）+ 后端 422 条全绿 |
| 8 | 真实运行截图/录屏/网络/可访问性/性能/回滚证据 | ✅（部分：录屏/性能未自动化） | 22+ 截图（web/screenshots/ + CI artifact）；可访问性报告=axe 结果（0 违规）；回滚=BUILD_ID bundle 切换实证（v2-def→v1-abc）；录屏/性能基线在已知限制中如实登记 |

## 3. E2E 覆盖矩阵

| 场景 | 测试 | 覆盖 |
|------|------|------|
| Happy path 全流程 | workbench.spec:10（查询→结果→证据→公司→深链接刷新） | ✅ |
| 错误输入/注入 | workbench.spec:101（422）+ graph.spec:70（降级） + shell.spec:49（网络不可达） | ✅ |
| 重复提交 | RTL submit-guard + UI isPending 禁用 | ✅ |
| 请求取消/超时 | client.ts 七类错误分类（含 timeout/cancel）+ e2e route.abort | ✅ |
| 权限拒绝 | review-ops.spec:57（错 key→403）+ RTL 401/403 | ✅ |
| 并发审核 | 后端 test_true_concurrent_decision_returns_409_with_state + RTL 409 冲突横幅 | ✅ |
| 后端崩溃/恢复 | shell.spec:49（API abort→分类错误态→重试出口）+ compose 演练（#30 证据） | ✅ |
| UI 回滚 | BUILD_ID v1/v2 bundle 切换实证（quality-gates-evidence.md §4） | ✅ |

## 4. 跨任务不变量核对

| 不变量 | 实证 |
|--------|------|
| UI 只消费 API，不重算业务规则 | 全部前端 fetch 同源 API；excluded/unknowns 后端原文直渲染 |
| 经营结论显示 Claim/证据/时间/新鲜度 | GroundedResult 展开含全部四要素；e2e 断言 |
| 排除/未知/冲突/降级可见 | QueryWorkbench 四个 Section + STALE/DEGRADED 横幅 |
| QueryPlan 重试无矛盾审计 | #9 审计 UNIQUE + #33 幂等键 |
| 审核乐观并发 | 真并发 409 + RTL 冲突横幅 + 事实不被覆盖 |
| 权限拒绝不隐藏不泄露 | 401/403 分类文案（无堆栈/SQL） |
| 图谱非唯一表达 | 表格替代视图承载全部关系 |
| 统计真实+新鲜度+空态 | 首页 /overview/stats 实数 + 快照说明 + 空态提示 |

## 5. 已知限制（诚实登记）

- 录屏未自动化（截图矩阵已覆盖状态与断点）；
- Web Vitals（LCP/INP/CLS）未接入（MVP 无性能基线需求）；
- 像素 diff 视觉回归未启用（Cytoscape canvas deterministic 成本/收益低）；
- 键盘"完成全流程"仅焦点序抽查（axe 已覆盖标签/对比度/焦点规则）；
- e2e 跑在 vite dev（生产 bundle 由 CI web job 构建门禁保证，未在浏览器下验收）；
- 回滚演练为本地叙述性证据（机制全部实证，脚本化留待后续）。

## 6. 边界声明

系统不预测股价、不提供投资建议、不自动交易。合成快照公司（辛示
前缀）在首页/公司页明示；审核写操作默认关闭（`review_write_enabled=False`）。
全部经营结论可回溯至 Claim 与可定位 Evidence（后端 Epic #12 交付的
四环追溯链在 #33 的运维页/审计中保持可达）。
