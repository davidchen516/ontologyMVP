# 真实 TuShare 账户接入 Runbook（issue #45）

面向用**真实（含低积分层级）TuShare 账户**接入本系统的运维/开发人员。
按本文档可独立完成：token 配置 → 能力探针 → 字段层级预期 → 一键端到端
冒烟 → 数据质量报告解读。

> 合成快照（无 token）路径见 `scripts/build_mvp_snapshot.py`；本文档只覆盖
> 真实账户。**"CI 绿"不构成真实账户验收**——真实运行证据是唯一验收标准。

## 1. 前置条件

- docker compose 栈已启动（`docker compose up -d`，PostgreSQL/Neo4j/API/Worker/Web）。
- 维护者已将真实 token 写入本地 `.env`（**只经环境变量注入**，compose
  传递给容器；token 永不出现在命令行参数、日志、报告或仓库中）：

  ```dotenv
  TUSHARE_TOKEN=<your-token>   # 56 位 hex，TuShare 个人主页获取
  ```

- token 修改后重启栈使其生效：`docker compose up -d --force-recreate api worker`。

## 2. 一键端到端冒烟

在仓库根目录执行（宿主机挂载运行，使用容器内与 main 一致的依赖）：

```bash
docker compose run --rm -v "$PWD:/host" api \
  /app/.venv/bin/python /host/scripts/real_account_smoke.py \
  --report-out /host/real_smoke_report.md
```

> 报告必须写到 `/host/...`（宿主机挂载路径）——容器自身文件系统随
> `--rm` 销毁，写到容器内 `/tmp` 的报告不可找回。

脚本一次性完成并如实报告：

| 阶段 | 动作 | 额度（请求数） |
|---|---|---|
| 1 探针 | 6 数据集能力探测（探针参数限 1 行） | 6 |
| 2 采集·默认路径 | stock_basic + stock_company 默认参数各 1 批 | 2 |
| 2 采集·候选路径 | Claim 候选公司（钉住 ts_code）：每家证券+档案 2 请求，首家档案文本可抽取者胜出（最多 3 家） | ≤6 |
| 2 采集·胜出者财务 | 财务三表+指标按 `period=20251231`+胜出 ts_code 钉住，各 1 批 | 4 |
| 3 标准化 | stock_basic → stock_company → 财务（PG 内） | 0 |
| 4 证据/Claim | 胜出公司真实档案文本 → 规则抽取 → intake | 0 |
| 5 查询 API | 容器内 uvicorn + `POST /api/v1/screen` | 0 |

**总请求数有界：≤18（典型 14：首家候选即胜出 = 探针 6 + 默认 2 +
候选 2 + 财务 4）。** 退出码：`0` 全部不变量满足；`2` 配置失败（缺
token / 一次性库名守护拒绝）；`3` 不变量未满足（不假装成功，失败项逐条
列出）。

> 额度语义：计数为**逻辑请求数**（一次探针/一批采集各计 1）。瞬时错误
> （限频/网络）下 `TushareClient` 会指数退避重试（上限
> `tushare_max_retries=3`），真实 HTTP 调用数可至逻辑数的 ~4×——
> 报告计数按既有口径如实入账（含重试的运行会在 ingest run 的
> request_count 中体现）。

**幂等性**：脚本可安全重跑——每次运行 DROP+CREATE 全新一次性库
（跨运行无残留），库内采集/标准化走既有幂等键（payload_hash /
watermark）；重复行不会重复插入。

写入一次性库 `real_smoke`（复跑自动 DROP+CREATE），**不触碰共享演示库**。

> 2026-10-06 后注（#56 分页修复）：stock_basic 已全量分页——冒烟默认
> 路径单请求现在拉取 ≤1000 行（`max_batches=1` 短批即止，状态如实
> PARTIAL_SUCCESS/max_batches_reached），探针仍为 limit=1 单行；
> 冒烟的有界性语义（请求计数）不变。

### 为什么要"钉住 ts_code"（两次真实运行的设计教训）

注册表的探针参数是探针导向的（stock_basic `limit=1`、stock_company
固定 `000001.SZ`）——真实端到端需要主数据/公司/财务/Claim/screen 在
**同一真实公司**上闭环，冒烟脚本用 `dataclasses.replace` 在脚本本地把
参数钉住到候选公司（不修改主链路配置）。实测佐证：低层级账户
stock_basic `list_status=L&limit=1` 实际返回 `920202.BJ`（北交所），
与 stock_company 默认的 `000001.SZ` 不同——不钉住则公司标准化必然
拒绝（证券未标准化）。

### 冒烟数据集范围

- `stock_basic`、`stock_company`：主数据（证券 → 公司链路）。
- `income_vip` / `balancesheet_vip` / `cashflow_vip` / `fina_indicator_vip`：
  财务三表 + 指标（`period=20251231` 单报告期，每表 1 批 ≤1000 行）。
  注意 `balancesheet_vip` 无标准化处理器（与 `build_mvp_snapshot` 的
  `DATASETS_TO_RUN` 一致）——只入 raw 层供形态/质量分析。

## 3. 字段层级预期（低层级账户）

issue #43 的字段分级是真实账户可用性的核心（`ontology/mappings/tushare.yaml` v0.1.3）：

| 层级 | 语义 | stock_basic 中的字段 | 缺失时行为 |
|---|---|---|---|
| `required_fields` | 自然键/身份 | `ts_code` `symbol` `name` | **熔断 SCHEMA_CHANGED**（真实漂移） |
| `expected_fields` | 非键业务字段 | `exchange` `list_status` | **不熔断**：质量标记 `missing_expected_fields`，raw 层 `request_params` 行级记录 |

低层级账户 `stock_basic` 实测**缺 `exchange`/`list_status`**——探针与采集
均应返回 AVAILABLE + 缺失记录，全链路不熔断。

## 4. 质量报告解读

`--report-out` 生成的 markdown 报告各节含义：

- **探针表**：每数据集状态与 expected 缺失列。`AVAILABLE` + 缺失 =
  预期的低层级形态；`NO_PERMISSION` = 账户无该接口权限（如实跳过采集）；
  任何 `SCHEMA_CHANGED` = 回归（身份字段缺失/漂移），冒烟失败。
- **master 质量分布**：`master_security_status_unknown` = list_status
  缺失的证券数（#43 语义：**不再静默默认 ACTIVE**——退市证券不得标在市）。
- **标准化表**：`rows_rejected` 的主要来源是冒烟的有界性——财务三表单批
  含全市场行，而主数据只采了 1 只证券，未入 `master.security` 的 ts_code
  会被财务处理器拒绝（"security not standardized yet"）。这是**预期行为**
  而非数据质量问题；扩大主数据采集范围即可消除。
- **字段形态**：仅记字段名集合（无值），供字段层级演进参考。

## 5. 已知限制与查询结论影响

| 限制 | 影响 | 系统呈现 |
|---|---|---|
| `list_status` 缺失 | 无法区分退市证券 | `master.security.status = 'UNKNOWN'`（可查询区分，不静默 ACTIVE） |
| `exchange` 缺失（stock_basic） | 交易所归属降级 | 从 `ts_code` 后缀派生（SH→SSE/SZ→SZSE/BJ→BSE） |
| `name`/`fullname` 缺失（stock_company） | 公司名承载键为 `com_name` | #49 修复后标准化兼容；未修复时公司行被拒 |
| 财务 vip 接口无权限 | 财务观察值不可用 | 能力矩阵 `NO_PERMISSION`；screen 的财务指标过滤无数据（`excluded`/`unknowns` 如实呈现） |
| 冒烟 1 批有界采集 | 主数据仅少量证券 | 非数据质量问题；扩大主数据采集范围即消除 |

## 6. 真实运行证据（2026-10-06，低层级账户，三次运行）

> 本节数字由真实运行产出；**最终成功报告**（脱敏全文）：
> `docs/real-account-smoke-report-2026-10-06.md`。

### 最终运行（#49 修复合入后）：全部端到端不变量满足 ✅

- 额度消耗：探针 6 + 采集 10 = **16 请求**
- 采集：stock_basic 3 行 / stock_company 3 行 / 财务四表各 1 批（胜出者
  000063.SZ 单公司行）
- Claim 候选：002230.SZ 未命中抽取词表 → 000063.SZ 胜出（真实档案文本
  可抽取）
- 标准化：master.security **3**（全部 status=UNKNOWN——list_status 缺失
  的 #43 降级语义）、master.company **2**、finance 观测 **5**
- Claim：文档 2 / 片段 4 / 候选 1 / 接受 1（真实档案文本证据）
- **`POST /api/v1/screen` → 中兴通讯股份有限公司（000063.SZ）**，
  带 1 条 ACCEPTED claim + 1 条证据引文（business_stage=RESEARCH）
- 全链路零 SCHEMA_CHANGED 熔断

### 能力矩阵（三次运行一致）

| 数据集 | 探针状态 | 备注 |
|---|---|---|
| stock_basic | AVAILABLE | 缺 exchange/list_status（expected 层级质量标记） |
| stock_company | AVAILABLE | 缺 name/fullname，公司名在 **com_name**（→ issue #49） |
| income_vip / balancesheet_vip / cashflow_vip / fina_indicator_vip | AVAILABLE | 低层级账户可用财务 vip 接口；单批 1000 行全量返回 |

**全链路零 SCHEMA_CHANGED 熔断**（#43 字段层级在真实账户上成立）。

### 真实响应形态（脱敏，仅字段名）

- `stock_basic`：`area, name, market, symbol, cnspell, ts_code, act_name, industry, list_date, act_ent_type`（缺 exchange/list_status）
- `stock_company`：`city, email, com_id, office, manager, ts_code, website, chairman, com_name, exchange, province, employees, secretary, setup_date, reg_capital, introduction, main_business, business_scope`（缺 name/fullname；**exchange 存在**——层级差异按接口独立表现）
- 财务 vip 三表 + 指标：150+ 字段全量返回（required ts_code/end_date 齐全，标准化正常写入）

### 发现的缺口（按 issue #45 范围规则开新 issue 跟踪）

- **#49**：stock_company 公司名键位（com_name）——修复后 company>0 +
  screen≥1 不变量方可满足。

### 额度消耗

- 首次运行（v1 设计，发现连接缺口）：探针 6 + 采集 6 = **12 请求**
- 二次运行（v2 钉住设计，发现 com_name 缺口 → #49）：探针 6 + 采集 10 = **16 请求**
- 最终运行（#49 修复验证，全绿）：探针 6 + 采集 10 = **16 请求**
- 三次合计 **44 请求**，全程 stock_basic/stock_company 单行、财务单批 1000 行

## 7. 全市场数据接入（issue #56：`scripts/real_data_pipeline.py`）

冒烟（上文）是**有界验收**；把全市场真实数据装进本地栈并让界面
（公司分析 / 财务 / 图谱页）承载真实数据，用全市场管道：

```bash
docker compose run --rm -v "$PWD:/host" api \
  /app/.venv/bin/python /host/scripts/real_data_pipeline.py \
  --fresh --company-limit 300 \
  --report-out /host/real_pipeline_report.md
```

- **数据集**：stock_basic 全量分页（~5000+ 证券，issue #56 修复）；
  income/cashflow/fina_indicator/fina_mainbz vip 按 period 全市场分页；
  stock_company 逐只（`--company-limit`，默认 300 ≈ 5 分钟；0=全量
  ~5000 只，限频 60/min ≈ 85 分钟，运行时长如实入报告）。
- **投影**：清图 → `full_rebuild`（实体+Claim[真实数据为 0]+claim 边）
  → 真实主营构成 `PRODUCES` 边直接物化（`source: 'business_segment'`，
  projector 经营边以 claim_id 为幂等键，真实无 claim 故与演示快照同模式
  走直接物化）→ 对账报告。
- **一次性库** `real_market`（`--fresh` 重建；默认复跑幂等——payload_hash
  /watermark，行数不变）。
- **切栈**：让界面读真实数据——compose api 指向一次性库后重启：
  在 `.env` 加 `POSTGRES_DB=real_market` 并
  `docker compose up -d --force-recreate api web`；回滚 = 删该行重建
  演示切片（`build_mvp_snapshot`，本地演示栈数据可重建）。
- **刷新流程（栈切换后）**：`--fresh` 重建会**拒绝**作用于当前配置库
  （切换后 `.env` 的 `POSTGRES_DB=real_market` 即线上库）——先回切
  `.env` 或停栈再 `--fresh`；不重建的幂等复跑不受限（无 DROP）。
  DROP/CREATE 一律经维护库连接（`postgres` 库），绝不会自删当前连接库。
- **对账注意**：真实模式下经营边为直接物化（`source: 'business_segment'`，
  无 claim_id）——`reconciliation_report` 的"经营边 claim_id 比例"为 0%
  属既定代价，与演示切片（100%）语义不同；实体计数对账不受影响。
- **代价**（issue #56 非目标）：claim/证据页为空（演示切片专属）；
  概念/股东数据不接入（探针单点参数问题，另行立项）。

## 8. 编排回归锁定

`tests/db/test_real_smoke_script.py` 用 stub transport（低层级字段形态：
缺 exchange/list_status）在一次性 PG 上锁定编排逻辑：探针 AVAILABLE +
缺失记录 → 采集 → 标准化 → 真实文本证据/Claim → screen 不变量全部通过，
以及缺 token 负向场景（退出码 2）。真实账户运行本身是人工证据，不进 CI。
