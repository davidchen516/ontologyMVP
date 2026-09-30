---
title: 开发与治理
nav_order: 4
has_children: true
permalink: /developer-guide.html
---

# 开发者指南

## 先确认仓库阶段

当前 `main` 已完成 V0.1 后端纵向闭环（#1～#12），包括采集、标准化、语义、证据、Claim、图投影、查询和质量门禁；当前产品缺口是可交互 Web UI。开始界面工作前先查看 [V0.2 Epic #29](https://github.com/davidchen516/ontologyMVP/issues/29)、[产品界面设计](product-ui.html)与对应子 Issue。

## 仓库地图

```text
.
├── docs/                  对外指南、技术设计与 ADR
├── ontology/              OWL、SKOS、SHACL、规则与映射
├── apps/                  FastAPI 与 Worker 入口
├── src/core/              配置、健康、日志和追踪基线
├── src/semantic/          SemanticRuntime 项目端口
├── tests/                 单元与集成测试
├── scripts/               设计资产校验
├── .github/workflows/     运行时、设计校验与 Pages 发布
├── docker-compose.yml     PostgreSQL、Neo4j、API 与 Worker
├── pyproject.toml         Python 依赖与工具配置
├── uv.lock                精确 Python 依赖锁
├── web/                    V0.2 计划新增的产品 Web UI（尚未创建）
├── _config.yml            Jekyll / Just the Docs 配置
├── Gemfile                文档构建依赖
├── README.md              仓库入口
├── CONTRIBUTING.md        GitHub 贡献入口
└── SECURITY.md            安全报告入口
```

V0.1 收尾证据见 [Epic 收尾报告](epic-closeout.html)和[MVP 验收报告](mvp-acceptance-report.html)。V0.2 按 [Roadmap](roadmap.html) 和 #30～#34 推进；在 #30 完成前不要预建另一套前端框架。

## 本地代码工作流

```bash
cp .env.example .env          # 仅本地使用，并替换示例密码
uv sync --frozen
uv run ruff check .
uv run pytest
uv run python scripts/validate_design.py
```

需要真实本地依赖时，再运行 `docker compose up --build -d --wait`。当前自动化集成测试使用不可达端口注入依赖故障，不把 Mock 成功等同于真实 PostgreSQL/Neo4j 验收。

## 本地文档工作流

```bash
bundle config set --local path vendor/bundle
bundle install
bundle exec jekyll serve --baseurl /ontologyMVP
```

提交前：

```bash
python scripts/validate_design.py
bundle exec jekyll build --strict_front_matter
```

不要提交 `_site/`、`.jekyll-cache/` 或 `vendor/bundle/`。

## 设计边界

任何运行时实现都必须遵守：

- PostgreSQL 是唯一事实权威；Neo4j 是投影。
- 业务模块依赖项目 `SemanticRuntime`，不直接导入 Semantica 内部模块。
- 正式经营结论以 Claim 为中心，并保留 Evidence、Provenance 和双时态。
- LLM 输出先进入受控 Schema，不直接执行自由 SQL/Cypher 或写事实库。
- 产品特定业务规则、授权判断、风险限制和权威事务状态留在项目自有模块。
- 大型基础能力先评估成熟开源项目，优先使用薄适配器，不复制或分叉上游实现。

## 依赖决策

增加重要依赖前，应记录：

1. 候选项目与官方资料；
2. 功能/领域适配度、维护状态、稳定性、社区、文档和性能；
3. 许可证、商业使用、安全和供应链风险；
4. 集成与运维成本、平台兼容性、迁移与退出条件；
5. 对最难需求的最小 PoC；
6. 精确或兼容版本、锁文件和许可证通知。

文档站选型见 [ADR-0004](adr/0004-documentation-site.html)，产品界面选型见 [ADR-0005](adr/0005-product-ui-stack.html)。

## 变更类型

### 文档

- 更新对应指南和设计原文；
- 保持站点导航与本地链接有效；
- 不把计划能力写成已经可用；
- 状态变化时同步 Roadmap 与 Issue 链接。

### 本体、词表与 Shapes

- 遵守稳定 IRI 和语义化版本；
- 增加正向与负向样例；
- 同步检查规则、映射、历史 Claim 和黄金查询影响；
- 运行 Turtle、YAML、SHACL 与质量门禁。

### 数据库与状态机

- 使用可重复、可回滚的迁移；
- 非法状态迁移必须失败；
- 并发、重复提交和事务中断有明确行为；
- 不通过物理删除历史事实进行回滚。

### 外部连接器

- Secret 仅注入，不进入仓库；
- 权限拒绝、429、网络失败、空结果和 Schema 变化分别处理；
- CI 默认使用固定 Fixture，不访问真实外部服务；
- 读取动作必须限流、幂等和可恢复。

## Pull Request 最小证据

- 变更目标、范围、非目标和依赖；
- 设计边界或 ADR 影响；
- 自动化测试和真实运行证据；
- 适用的错误、重复、并发、崩溃恢复、权限拒绝和回滚结果；
- 监控、对账或审计证据；
- 新依赖的许可证与版本说明；
- 不构成投资建议和已知限制（涉及对外结果时）。

完整提交规范见[贡献指南](contributing.html)。
