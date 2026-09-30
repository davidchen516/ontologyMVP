# #30 前端工程、设计系统与应用壳 — 验收证据

- 日期：2026-09-30
- 分支：`issue-30-web-foundation`（提交见 PR）
- 设计基线：`docs/product-ui.md` + `docs/adr/0005-product-ui-stack.md`（本轮补齐——
  issue 引用但仓库缺失，已按 issue 范围第 7 项交付）

## 1. 工程与设计系统

- `web/`：React 18 + TypeScript strict + Vite 静态 SPA；
  Tailwind CSS v4 + 语义化设计 Token（`src/styles/tokens.css`，亮/暗主题、
  8px 基数、375/768/1024/1440 断点、tabular-nums）；
  React Router（SPA fallback）+ TanStack Query + lucide-react。
- 依赖锁定：`package-lock.json`（npm ci 可复现）；许可审计
  `npm run license:audit`（314 个安装包全部宽松许可证：MIT×259/
  Apache-2.0×22/ISC×16/BSD×11/MPL-2.0×2/MIT-0/BlueOak/Python-2.0/CC-BY-4.0
  各 1；零豁免）。

## 2. 应用壳

- 顶部栏（品牌 + 系统状态点 + 主题切换）、可折叠侧栏（localStorage 持久化）、
  移动端抽屉导航、面包屑、404、路由级 ErrorBoundary。
- **能力感知导航**（`src/lib/capabilities.ts`）：里程碑门禁（#31~#33 未交付）
  + `/readyz` 组件/能力门禁；未启用项显示"未启用"+原因（title），不可点击。
- **类型化 API Client**（`src/lib/api/`）：七类错误分类（network/timeout/
  permission/validation/not_found/server/unknown）、X-Trace-Id 透传、
  超时与外部取消合并、统一错误文案（不泄露内部细节）。
- 主题：首帧防闪烁脚本 + `prefers-color-scheme` 跟随 + localStorage 持久化。

## 3. 测试证据

| 层 | 结果 |
|----|------|
| Vitest + RTL（组件/路由/错误边界/双击抑制/API Client） | **24 passed** |
| API 契约一致性（web/openapi.json 锚点，CI contract job 漂移检测） | 通过 |
| TypeScript strict（tsc -b --noEmit） | 通过 |
| ESLint（含 react-hooks） | 0 error 0 warning |
| Playwright 真实浏览器（dev 代理 + compose 生产栈） | **7 passed** |
| 许可审计 | 665 包通过，零豁免 |

双击抑制：首次点击后立即 disabled + `aria-busy` + "提交中…"，完成前重复
点击不派发（服务端幂等仍由 #9 审计 UNIQUE 保证——双层防护）。

## 4. 真实 Compose 验收（API + Web 同源）

```
docker compose up -d          # postgres/neo4j/api/worker/web 五服务
alembic upgrade head          # （迁移步）
curl :5173/                   # 200（SPA）
curl :5173/workbench/query    # 200（深链接刷新，nginx try_files fallback）
curl :5173/readyz             # 200 真实 readyz（同源反代）
curl -X POST :5173/api/v1/screen  # 200 真实查询响应
# 中断演示：docker compose stop api → readyz 000（UI 显示网络错误态）
#           docker compose start api → readyz 200（恢复）
```

浏览器验收（Playwright 对 compose 生产栈 + dev 代理）：`web/screenshots/`
9 张真实截图（375/768/1024/1440px 亮色、1280px 暗色、深链接、错误态、
键盘焦点）。
- 375/768/1024/1440px 全部无横向溢出（`scrollWidth - clientWidth <= 0`
  硬断言；issue 要求的四个断点全部真实视口截图）。
- API 中断时 `[role=alert]` 分类错误 + 重试按钮 + trace_id，无 psycopg/
  Traceback 泄露（断言）。
- 键盘 Tab 焦点可见（`:focus-visible` outline）。

## 5. CI

`runtime-ci.yml` 新增 `web` job（npm ci → tsc → eslint → vitest → build →
bundle 报告 → 许可审计）与 `contract` job（OpenAPI 快照漂移检测）。

## 6. 回滚

- web 为独立静态服务：`docker compose stop web` 即下线，不影响
  API/Worker/数据库；回退 = 回退镜像/静态产物 tag。
- 前端依赖由 lockfile 固定；升级失败回退 lockfile + 构建产物。

## 6.5 审查遗留项（已修 / 移交）

- 已修（本轮提交）：evidence 许可数字失实（665→实测 314）、截图断点补齐
  （768/1024/1440 真实视口）、抽屉图标不切换、键盘断言恒真（改为断言
  真实焦点目标：品牌链接 → 主题开关）。
- 移交（#31/#34）：types.ts ScreenRequest 补 known_at/period_rule 反向
  契约守护；429 rate-limit 分类；Radix Dialog 焦点陷阱；e2e 进 CI（#34
  浏览器矩阵）；tsbuildinfo 不入库。

## 7. 已知限制（移交后续轮）

- compose 数据卷保留旧密码时需 `down -v` 重建（本轮验证时发现的环境事实，
  非代码缺陷）。
- 浏览器 E2E 的完整矩阵（并发审核、权限拒绝、性能）按 Epic 规划在 #34。
