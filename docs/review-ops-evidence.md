# #33 Claim 审核与系统运营工作台 — 验收证据

- 日期：2026-09-30
- 分支：`issue-33-review-ops`
- 设计基线：`docs/product-ui.md` + ADR-0006（本轮新增）

## 1. 认证/授权（ADR-0006 `docs/adr/0006-review-auth.md`）

| 维度 | 实现 |
|------|------|
| 认证 | `X-Reviewer-Key` 请求头 → SHA-256 哈希 + `hmac.compare_digest` 常量时间比对（`src/review/auth.py`）；服务端只存哈希（`.env`） |
| 开关 | `Settings.review_write_enabled`（默认 False）+ `review_api_key_hashes`；`/readyz.capabilities.review_write` 真实反映（开+哈希齐→OK） |
| 角色 | 有效 key ⇒ Reviewer（写审核）；无 key ⇒ 401；错 key ⇒ 403；开关关 ⇒ 503——全部零副作用 |
| 升级路径 | OIDC（触发条件+退出条件在 ADR）；路由守卫/幂等/审计层不变 |

威胁模型速记（ADR）：key 泄露→单 Reviewer 权限+审计可追溯；暴力→常量比对+网关限速；重放→Idempotency-Key 查重+乐观并发。

## 2. 最小审核写 API（`src/review/api.py`）

| 端点 | 说明 |
|------|------|
| `POST /api/v1/review/tasks/{id}/decision` | **唯一写端点**。decision 白名单 ACCEPTED/REJECTED；reason 必填（≤2000）；`Idempotency-Key` 头（客户端 UUID）幂等；409=并发冲突（回传当前任务/Claim 状态）；404/422=任务不存在/非 UUID；经 #7 `review_decide`→`accept_claim` 原子事务（Claim/Evidence/Provenance/审计/Outbox 同事务） |
| `GET /api/v1/review/queue` | Reviewer 队列（OPEN 任务+Claim 摘要+证据计数+理由码） |

幂等实现：决定成功后写入 `ops.audit_event`（REVIEW_DECISION_IDEMPOTENCY + payload.idempotency_key）；同 key 重放命中查重→返回任务终态不重复决定（实测只 1 份决定）。

## 3. 运维只读页（`/ops`）

- 核心组件 + 可选能力（`/readyz` 真实形状——postgres/neo4j/tushare/llm/review_write）；
- 数据源能力探针（`/admin/capabilities`=source_capability 行，裸数组支持）；
- 最近采集/标准化运行（状态+PARTIAL_SUCCESS 如实着色）；
- 投影水位与对账（`/admin/projection/status` + `reconciliation` JSON 如实渲染）；
- 数据新鲜度；待审任务只读计数（审核决定引导至审核工作台）。
- **危险运维写操作不进入首版**（issue 要求）——页面全部只读。

## 4. 审核工作台（`/review`）

- **登录入口能力感知**：`review_write` 能力 UNAVAILABLE → 登录表单不渲染，显示"审核功能未启用"+只读/CLI 引导（无死按钮）；
- Reviewer Key 内存态（**不入 localStorage/URL**——测试断言）；
- 队列任务摘要（谓词/阶段/置信度/证据数/优先级/理由码）；
- 接受/拒绝 → 决定对话框：**理由必填**（空则确认禁用）+ 状态机后果预览（NEEDS_REVIEW→ACCEPTED/Evidence/Provenance/审计/Outbox 同事务）；
- 409 并发冲突 → 黄色横幅"已被其他审核者处理"+自动刷新队列；
- 提交带 Idempotency-Key（crypto.randomUUID）+ 双击抑制（isPending 禁用）。

## 5. 测试证据

| 层 | 结果 |
|----|------|
| 后端 API（`tests/db/test_review_api.py`） | **8 passed**：开关 503/无 key 401/错 key 403 零副作用/接受全链（Claim→ACCEPTED+任务 COMPLETED+幂等锚点）/幂等重放（同 key 二次→原样返回+只 1 份决定）/第二 Reviewer 409+事实不被覆盖/队列+证据计数/非 UUID 422/decision 校验 |
| 后端全量 | **420 passed**（+8；修 4 个能力矩阵断言：config×2/health×1/worker×1——review_write 新能力项加入 dict） |
| 前端 Vitest | **42 passed**（+5：登录入口能力感知渲染/队列+理由必填+409 冲突提示/key 不入存储/运维页真实形状/错误态可见） |
| Playwright | **22 passed**（+3：登录→队列→决定→成功；运维页真实数据；错 key 403 分类）+5 张截图（queue/dialog/after/403/ops-page） |
| tsc/eslint/build | 绿 |

GWT 对照：
- 双 Reviewer 并发 → 409 + 当前状态 + 事实不被覆盖（API+UI 双层测试）✓
- 重复提交（同幂等键）→ 一次决定一份审计 ✓
- 非 Reviewer/过期/开关关 → 401/403/503 零副作用 ✓
- 提交后崩溃重连 → 幂等键重放返回终态（不盲目再写）✓
- 理由必填 + 状态机后果展示 ✓

## 6. 移交 #34

- 双浏览器（真两进程）并发审核演示录屏（本轮用 API 级并发测试+UI 409 流程覆盖语义；真双浏览器录屏在 #34 E2E 矩阵）；
- 会话过期（API-Key 无会话——401 即"过期"等价；OIDC 升级后补会话过期场景）；
- 审核队列筛选/排序增强、Claim 与 Evidence 并排详情（当前摘要级——全文对照在 #34 打磨）。
