---
title: ADR-0005 产品 Web UI 技术栈
parent: 架构决策记录
nav_order: 5
permalink: /adr/0005-product-ui-stack.html
---

# ADR-0005：产品 Web UI 技术栈与边界

- 状态：Accepted，重要依赖在 #30 通过 PoC 后精确锁版
- 日期：2026-09-30
- 决策范围：V0.2 产品界面、图谱交互、前端测试和部署

## 背景

V0.1 已交付 FastAPI、PostgreSQL、Neo4j、受控 QueryPlan、Evidence 和 Claim 审核领域能力，但没有面向用户的 Web UI。新界面需要同时支持数据密集型表格、受控查询、证据下钻、图谱路径、审核并发、响应式和 WCAG 2.2 AA，并保持现有权威边界。

项目不需要 SEO 内容站、服务端 React 业务逻辑或新的 BFF；GitHub Pages 已承担公开文档，FastAPI 继续承担 API、权限和审计。

## 决策

在独立 `web/` 目录建立静态 SPA，经 FastAPI/OpenAPI 集成：

| 组件 | 职责边界 | 许可证 | 选择理由 |
|---|---|---|---|
| React + TypeScript | 页面/组件和类型安全；不包含业务权威状态 | MIT / Apache-2.0 | 生态完整，适配数据表格、图谱和无障碍组件 |
| Vite | 开发服务器和静态构建；不承担运行时服务端 | MIT | 与 FastAPI 解耦，构建和部署简单 |
| React Router | 路由、深链接和错误边界 | MIT | 成熟标准路由，避免自研导航状态 |
| TanStack Query | API 服务端状态、缓存、取消和失效 | MIT | 避免自研请求缓存；写操作仍由服务端保证幂等 |
| TanStack Table | 公司、Claim、Evidence 和运行记录表格 | MIT | headless、可虚拟化，不绑定视觉主题 |
| shadcn/ui + Radix Primitives | 可访问交互组件和项目可控视觉层 | MIT | 接近参考界面，减少自研焦点/弹窗/菜单基础设施 |
| Tailwind CSS | 设计 Token 和响应式样式 | MIT | 与 shadcn 兼容，产物可裁剪 |
| Cytoscape.js | 受控子图、布局、选择和路径交互 | MIT | 领域匹配的通用网络图能力，支持扩展与无框架核心 |
| Vitest + Testing Library | 组件和集成测试 | MIT | 与 Vite 集成，测试用户可见行为 |
| Playwright | 真实浏览器 E2E、移动端和视觉回归 | Apache-2.0 | 支持 Chromium/Firefox/WebKit 和故障场景 |
| axe-core | 自动无障碍检查，作为人工检查补充 | MPL-2.0 | 成熟规则集；仅测试工具，不进入领域逻辑 |

截至决策日核对的官方发布包括 React 19.3.0、Vite 8.3.1、React Router 8.4.0、TypeScript 7.0.2、Tailwind CSS 4.3.3、shadcn 4.21.0、Cytoscape.js 3.34.3、Vitest 5.0.2 和 Playwright 1.63.0。实施时以兼容性 PoC 结果精确锁版，提交 lockfile、许可证清单和 SBOM，不自动追逐最新版本。

官方来源：

- [React](https://github.com/facebook/react)、[Vite](https://github.com/vitejs/vite)、[TypeScript](https://github.com/microsoft/TypeScript)
- [React Router](https://github.com/remix-run/react-router)、[TanStack Query](https://github.com/TanStack/query)、[TanStack Table](https://github.com/TanStack/table)
- [shadcn/ui](https://github.com/shadcn-ui/ui)、[Radix Primitives](https://github.com/radix-ui/primitives)、[Tailwind CSS](https://github.com/tailwindlabs/tailwindcss)
- [Cytoscape.js](https://github.com/cytoscape/cytoscape.js)
- [Vitest](https://github.com/vitest-dev/vitest)、[Playwright](https://github.com/microsoft/playwright)、[axe-core](https://github.com/dequelabs/axe-core)

## 候选比较

### 应用框架

| 候选 | 结论 | 原因 |
|---|---|---|
| React + Vite | 选择 | 静态 SPA 足够，FastAPI 保留服务端权威；开发/部署边界最薄 |
| Next.js 16 | 拒绝进入主路径 | MIT 且维护活跃，但 SSR/RSC/Node 服务会引入第二个服务端和 BFF 诱因；当前没有 SEO 或服务端渲染需求 |
| Vue 3 + Vite | 可替代 | MIT、成熟且能满足需求；当前 React 的图谱、headless 数据和组件组合集成成本更低，无必要同时维护双栈 |
| Streamlit | 拒绝作为正式产品 UI | 适合内部原型，但复杂路由、证据/图谱交互、权限、可访问性和长期前端质量控制受限 |

若未来出现必须 SSR、边缘渲染或统一 React 服务端数据加载的硬需求，重新评估 Next.js；不能仅为“流行”迁移。

### 组件系统

选择 shadcn/ui + Radix，而不选择整套 MUI 作为主视觉层。MUI 9（MIT）成熟、文档完整，是可行回退方案；但参考界面需要项目化的信息密度和视觉 Token，headless/开放代码组件更容易保持领域布局。限制是 shadcn 组件更新需要项目自己审查；必须保留上游许可、避免无审查复制，并通过项目组件 API 隔离页面。

### 图谱

选择 Cytoscape.js，不自研 Canvas/SVG 图引擎。Sigma.js 对超大 WebGL 网络性能有优势，但当前核心需求是受控子图、路径、布局和丰富交互；React Flow 更适合节点编辑器/流程图，不适合本体关系网络。若 2,000 节点 PoC 达不到预算，再用相同 `GraphViewModel` 接口评估 Sigma.js，而不是把图组件细节泄露到页面。

## 可选与实验性组件

- [Apache ECharts](https://github.com/apache/echarts) 6.1.0（Apache-2.0）仅在财务趋势图确有价值时加入；V0.2 首个切片可以使用表格和轻量 SVG，不因参考图增加无用图表依赖。
- 自然语言查询 UI 只有在后端 Planner 真实可用、能显示/确认 QueryPlan、具备注入与审计测试后启用。
- 登录入口只有在标准 OIDC/会话方案被接受并由服务端声明能力后启用；不自研认证协议。

## 保留为项目自有的能力

- 页面信息架构、设计 Token、领域 ViewModel 和深链接约定；
- QueryPlan 表单与后端枚举映射；
- Claim/Evidence/双时态/降级的展示规则；
- Reviewer/Operator 权限策略和事实写入授权；
- API Client 的项目适配层。

这些是产品特定逻辑，不能隐藏在通用 UI 框架中；但状态机和权威判断仍由后端执行。

## PoC 与采用门槛

#30 在形成中央依赖前必须提交：

1. Vite 构建、深链接刷新、FastAPI OpenAPI 类型和 Compose 同源代理 PoC；
2. QueryResponse 的命中/排除/未知/冲突/降级完整渲染；
3. Cytoscape.js 500/2,000 节点两档的布局、交互、内存和表格替代视图；
4. Radix 对话框/菜单/抽屉的键盘、焦点、200% 缩放和 reduced-motion 验证；
5. Playwright 覆盖 API 中断/恢复与至少一个真实 Compose 流程；
6. 锁文件、依赖审计、许可证清单/SBOM 和静态产物回滚。

未通过的组件不得以“后续优化”名义进入主路径。

## 后果与限制

- 增加 Node/前端依赖链和独立 CI，需要持续处理供应链与浏览器兼容性；
- 静态 SPA 的 SEO 不适合作为公开内容站，公开文档继续由 GitHub Pages 承担；
- shadcn 源码归项目维护，必须控制定制范围并保留上游通知；
- Canvas 图谱对读屏不友好，因此同步表格/路径列表是强制能力；
- 前端缓存永远不是事实权威，写操作完成后必须重新验证服务端状态。

## 退出或替换条件

- Vite/React 栈无法满足受支持浏览器、安全或可访问性要求；
- Cytoscape.js 在受控规模 PoC 中持续超预算，且 Sigma.js 能在 `GraphViewModel` 接口下显著改善；
- 上游停止维护、许可证变化、出现无法缓解的高危漏洞；
- 产品出现不可替代的 SSR/边缘渲染要求。

替换时保持 OpenAPI、URL 深链接、领域 ViewModel、设计 Token 和后端权威边界稳定，避免重写业务域。
