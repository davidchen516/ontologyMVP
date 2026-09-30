# ADR-0006：V0.2 审核工作台认证/授权方案

- 状态：Accepted
- 日期：2026-09-30
- 关联：issue #33（Claim 审核与系统运营工作台）；`docs/product-ui.md`

## 背景

V0.2 #33 要求审核写操作（接受/拒绝 Claim）必须先有认证、授权、并发
守卫与审计。Epic #29 明确：参考图中的"登录"不是既有能力、不自研认证
协议、优先标准 OIDC、审核功能有服务端开关默认关闭。

## 决策

**首版（本 ADR）：静态 Reviewer API Key + 服务端开关（默认关）**

| 维度 | 决策 | 理由 |
|------|------|------|
| 认证 | `REVIEW_API_KEYS` 环境变量（逗号分隔 SHA-256 哈希值；服务端比对常量时间） | 单机/小团队 MVP；不自研密码协议、无会话、浏览器不保存长期 Token（每次请求头携带，密钥存内存态 store） |
| 传输 | 仅同源（nginx 反代）+ 现有 TLS/本地边界；生产必须 HTTPS | compose 拓扑内不引入新端口 |
| 授权 | 持有有效 key ⇒ Reviewer 角色（写审核）；无 key ⇒ 只读 | 角色单一——#33 的写面只有审核决定 |
| 开关 | `Settings.review_write_enabled`（默认 False）——关闭时写端点返回 503 + 能力 UNAVAILABLE，UI 登录入口不渲染 | Epic 回滚要求：默认关，关掉只留只读 |
| 并发 | 沿用 #7 `review_decide` 的悲观锁 NOWAIT + 乐观守卫（`ConcurrentClaimUpdateError` → HTTP 409 + 当前任务/Claim 状态回传） | 既有资产，不重复造 |
| 幂等 | `Idempotency-Key` 请求头（客户端生成 UUID）：决定提交后以 `ops.audit_event`（event_type=REVIEW_DECISION_IDEMPOTENCY + payload.idempotency_key）锚点查重，同 key 重放返回任务终态 | GWT：网络重试不产生第二个决定 |
| CSRF | 无 Cookie 会话 ⇒ CSRF 面不存在；未配置 CORSMiddleware ⇒ 严格同源（跨域请求被浏览器默认拦截——比显式空 allowlist 更直接） | API-Key 方案的天然属性 |
| 审计 | 沿用 `accept_claim`/review 事务内 `audit_event`（决定/理由/审核人/前后状态/trace_id）+ `reviewed_by = key 的 reviewer 标识` | 既有资产 |
| 密钥存储 | 服务端只存 SHA-256 哈希；明文仅存在于运维交付渠道（.env 不入库——gitleaks 扫描 + 哈希对哈希比对的 fail-closed：误配明文密钥时哈希不匹配、认证恒失败） | Secret 不入仓库/日志（红线） |

**升级路径（触发条件即换）**：多人协作/跨网络访问/细粒度角色 → 标准
OIDC（Keycloak/Auth0）+ 短时会话；本方案的路由守卫/幂等/审计层不变，
只替换 `authenticate_reviewer()` 的实现。退出条件：若 OIDC 引入成本
超过 MVP 收益，保留 API Key 并在工作台明示"单机模式"。

## 后果

- 审核写 API 面最小：`POST /api/v1/review/tasks/{id}/decision`（唯一写
  端点）；其余 #33 交付物全部只读。
- 运维页无认证要求（只读 `/admin/*`），但生产部署必须经网关控制。
- 威胁模型速记：key 泄露 → 单 Reviewer 权限 + 审计可追溯；暴力破解 →
  常量时间比对 + 部署层限速（nginx）；重放 → Idempotency-Key 查重 +
  乐观并发双保险。
