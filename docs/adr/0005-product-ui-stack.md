# ADR-0005：产品前端技术栈选型

- 状态：Accepted
- 日期：2026-09-30
- 关联：Epic #29、任务 #30；被 `docs/product-ui.md` 引用

## 背景

V0.1（#1~#12）交付了纯后端闭环（FastAPI + PostgreSQL + Neo4j + 受控
QueryPlan API）。V0.2 需要一个可交互的研究工作台，让用户无需直接调用
API 即可完成查询、公司分析、图谱探索、证据核验与系统状态检查。
需要确定前端技术栈与边界。

## 决策

| 维度 | 选择 | 理由 |
|------|------|------|
| 框架 | React 18 + TypeScript（strict） | 生态最全，类型化 API Client 与受控 Schema 天然对齐 |
| 构建 | Vite | 静态 SPA 产物（无 SSR 运行时），开发代理/预览/构建一体 |
| 样式 | Tailwind CSS v4 + 语义化设计 Token（CSS 变量） | Token 驱动亮/暗主题；不做自定义样式系统 |
| 组件 | shadcn/ui 约定 + Radix UI 无头原语（按需引入） | 可访问性内建（焦点/键盘/ARIA），不自研组件基础设施 |
| 路由 | React Router（library 模式） | 深链接 + SPA fallback 由 nginx `try_files` 支持 |
| 服务端状态 | TanStack Query v5 | 超时/取消/重试/缓存标准化；重复提交抑制用 `isPending` |
| 表格 | TanStack Table（#31 引入时） | 同生态 |
| 图标 | lucide-react（统一 SVG） | 树摇、风格统一 |
| 测试 | Vitest + React Testing Library；浏览器 E2E（Playwright）在 #34 | 单测快速；E2E 独立成质量任务 |
| 包管理 | npm（package-lock.json 固定） | 锁文件固定 + `npm ci` 可复现构建 |

## 部署形态

- 静态产物由 nginx 服务（`try_files` SPA fallback），`/api`、`/admin`、
  `/healthz`、`/readyz` 同源反代到 API 服务——浏览器**永不**直连
  PostgreSQL/Neo4j/TuShare/LLM。
- Compose 独立 `web` 服务：可单独停止/回退，不影响 API/Worker/数据库。
- API Client 类型从后端 OpenAPI 契约生成并做一致性检查；后端契约漂移
  会在 CI 失败，而不是运行时爆炸。

## 安全与不变量（承接 Epic #29）

- UI 只消费 API；不复制后端业务规则、不重新判定 Claim 真伪。
- 认证/授权未交付前，审核**写操作**一律不可用（导航置灰并说明原因）。
- 错误展示分类明确且携带 trace_id；不泄露内部 SQL/Cypher/堆栈/Secret。
- 所有统计来自真实响应并携带快照/新鲜度；无数据显示空态而非示例数字。

## 后果与退出条件

- 新增 Node/npm 工具链与 CI `web` job；锁文件漂移即 CI 失败。
- 退出条件：若 React 生态重大变更（如 React 编译器语义破坏性变更）
  导致维护成本超过重写，需新 ADR 评审；静态产物可整体下线回退到
  API/CLI 路径，不影响事实数据。
