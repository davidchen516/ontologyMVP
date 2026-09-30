---
title: 快速开始
nav_order: 2
has_children: true
permalink: /getting-started.html
---

# 快速开始

这份指南帮助你启动 V0.1 后端闭环、检查健康状态、运行测试并预览文档。数据库迁移、TuShare 采集、Claim/Evidence、Neo4j 投影和证据化查询已经实现；当前尚未提供正式产品 Web UI，使用入口是 API、演示脚本和测试快照。

## 你现在可以做什么

- 用 Docker Compose 启动 PostgreSQL、Neo4j、API 和 Worker；
- 通过 `/healthz` 与 `/readyz` 检查存活、核心依赖和可选能力；
- 运行 Python 单元/集成测试、静态检查和设计资产校验；
- 加载并检查 Turtle 本体、YAML 规则、TuShare 映射和本地链接；
- 本地预览本 GitHub Pages 站点；
- 构建合成 MVP 快照并运行黄金查询/演示脚本；
- 按 V0.2 设计继续实现产品 Web UI。

## 环境要求

| 用途 | 当前要求 | 状态 |
|---|---|---|
| 应用与校验 | Python 3.11、`uv`、`uv.lock` | 已配置 |
| 文档站点 | Ruby 3.3、Jekyll 4.4.1、Just the Docs 0.12.0 | 已配置 |
| 语义运行时 | Semantica `0.7.0` | Adapter、持久化 Provenance 与契约测试已实现 |
| 事实主库 | `pgvector/pgvector:pg16` | Schema、9 个 Alembic 迁移与角色边界已实现 |
| 图查询投影 | Neo4j `5.26` | Outbox、对账与全量重建已实现 |
| 本地运行拓扑 | Docker Compose v2 | API、Worker、PostgreSQL、Neo4j 已配置 |
| 结构化数据源 | TuShare Pro 账号和用户自己的 Token | Connector 已实现；无 Token 可使用合成快照 |
| 产品 Web UI | React/Vite（V0.2 计划） | 尚未实现，见 Epic #29 |

## 1. 获取仓库

```bash
git clone https://github.com/davidchen516/ontologyMVP.git
cd ontologyMVP
```

## 2. 配置本地环境

```bash
cp .env.example .env
```

在 `.env` 中把 `POSTGRES_PASSWORD` 和 `NEO4J_PASSWORD` 的示例值替换为本地随机密码。`TUSHARE_TOKEN` 和 `LLM_API_KEY` 可以留空；留空时核心服务仍可运行，但 `/readyz` 会把相应能力标记为不可用。

{: .warning }
`.env` 已被 Git 忽略。不要把真实 Token、密码或 Key 写回 `.env.example`、提交、Issue、日志或截图。

## 3. 启动工程基线

```bash
docker compose up --build -d --wait
docker compose ps
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/readyz
```

- `/healthz` 只表示 API 进程存活，正常返回 `{"status":"OK"}`。
- `/readyz` 检查 PostgreSQL、Neo4j 和可选能力：核心依赖不可达返回 HTTP 503；仅缺少 TuShare/LLM 凭证时返回 HTTP 200 和 `DEGRADED`。
- Neo4j 浏览器只绑定本机 `127.0.0.1:7474`；PostgreSQL 不发布宿主机端口，可用 `docker compose exec postgres psql -U ontology -d ontology` 调试。

停止服务但保留数据卷：

```bash
docker compose down
```

删除数据卷会丢失本地数据，因此本指南不把 `docker compose down -v` 作为常规命令。

## 4. 运行开发检查

安装 [uv](https://docs.astral.sh/uv/) 后：

```bash
uv sync --frozen
uv run ruff check .
uv run pytest
uv run python scripts/validate_design.py
```

设计校验覆盖 Turtle、YAML、本地链接和必需文件；完整 CI 还覆盖真实 PostgreSQL/Neo4j、迁移、状态机、并发/崩溃恢复、Semantica 契约、图重建、30 条黄金查询、覆盖率、依赖与 Secret 扫描。V0.1 的合成快照闭环已经通过，但**合成验收不代表真实 TuShare 数据质量已经达标**。

## 5. 预览文档站点

```bash
bundle config set --local path vendor/bundle
bundle install
bundle exec jekyll serve --baseurl /ontologyMVP
```

浏览器打开 `http://127.0.0.1:4000/ontologyMVP/`。提交前执行严格构建：

```bash
bundle exec jekyll build --baseurl /ontologyMVP --strict_front_matter
```

生成目录为 `_site/`，不应提交到 Git。依赖由 `Gemfile.lock` 固定；不要使用未固定版本的全局主题。

## 6. TuShare 安全配置

TuShare Connector 已实现能力探针、限流、断点续跑和 Raw 幂等采集。Token 必须通过环境变量或 Secret 管理系统注入，不得写入代码、示例、日志、Fixture、Issue 或截图。

本地 `.env` 示例只使用占位符：

```dotenv
TUSHARE_TOKEN=replace-with-your-own-token
```

安全约束：

- 只在本地未跟踪的 `.env` 或系统 Secret Store 中保存真实值；
- `.env.example` 只列变量名，不填真实值；
- 日志和错误响应必须脱敏；
- Pull Request CI 使用固定 Fixture，不需要真实 Token，也不消耗真实接口额度；
- 线上能力探针必须只读、限流，并区分无权限、限流、网络错误和 Schema 变化；
- Token 一旦出现在提交或日志中，应立即在 TuShare 侧轮换，并清理暴露面。

更多数据源行为见 [TuShare 与数据接入设计](data-sources-and-ingestion.html)，漏洞与凭证处理见[安全说明](security.html)。

## 7. 下一步

- 想理解系统：阅读[架构与组件](components.html)。
- 想改本体：阅读[本体指南](ontology-guide.html)和[贡献指南](contributing.html)。
- 想实现产品界面：阅读[产品界面设计](product-ui.html)，从 [#30 前端基础](https://github.com/davidchen516/ontologyMVP/issues/30)开始。
- 想评估完成度：使用[测试与验收](testing-and-acceptance.html)，不要把“CI 绿”当作唯一关闭条件。
