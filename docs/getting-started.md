---
title: 快速开始
nav_order: 2
has_children: true
permalink: /getting-started.html
---

# 快速开始

这份指南帮助你启动当前工程基线、检查健康状态、运行测试并预览文档。工程基线能够启动 API、Worker、PostgreSQL 和 Neo4j，但尚未实现数据库迁移、TuShare 采集、Claim 业务流程、图投影和完整查询场景。

## 你现在可以做什么

- 用 Docker Compose 启动 PostgreSQL、Neo4j、API 和 Worker；
- 通过 `/healthz` 与 `/readyz` 检查存活、核心依赖和可选能力；
- 运行 Python 单元/集成测试、静态检查和设计资产校验；
- 加载并检查 Turtle 本体、YAML 规则、TuShare 映射和本地链接；
- 本地预览本 GitHub Pages 站点；
- 基于现有 ADR 和 Issues 继续实现业务能力。

## 环境要求

| 用途 | 当前要求 | 状态 |
|---|---|---|
| 应用与校验 | Python 3.11、`uv`、`uv.lock` | 已配置 |
| 文档站点 | Ruby 3.3、Jekyll 4.4.1、Just the Docs 0.12.0 | 已配置 |
| 语义运行时 | Semantica `0.7.0` | 已锁版并建立端口；Adapter/契约待 #5 |
| 事实主库 | `pgvector/pgvector:pg16` | 容器已配置；Schema/迁移待 #2 |
| 图查询投影 | Neo4j `5.26` | 容器已配置；投影/重建待 #8 |
| 本地运行拓扑 | Docker Compose v2 | 已实现工程基线 |
| 结构化数据源 | TuShare Pro 账号和用户自己的 Token | 可选配置已接入；Connector 待 #3 |

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

设计校验覆盖 Turtle、YAML、本地链接和必需文件；运行时测试覆盖当前配置、健康检查、日志、追踪、Worker 和 Semantica 导入边界。这些结果证明工程基线行为，**不证明尚未实现的完整业务链路正确**。

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

当前配置层能识别 TuShare Token 并在就绪响应中报告能力状态，但尚未实现 TuShare Connector。实现 #3 后，Token 仍必须通过环境变量或 Secret 管理系统注入，不得写入代码、示例、日志、Fixture、Issue 或截图。

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
- 想实现业务能力：从 [Roadmap 中依赖已满足的开放 Issue](roadmap.html) 开始，并遵守 ADR 的权威边界。
- 想评估完成度：使用[测试与验收](testing-and-acceptance.html)，不要把“CI 绿”当作唯一关闭条件。
