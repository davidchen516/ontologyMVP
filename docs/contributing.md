---
title: 贡献指南
parent: 开发与治理
nav_order: 3
permalink: /contributing.html
---

# 贡献指南

感谢你帮助改进 ontologyMVP。项目目前处于设计基线向可运行 MVP 过渡阶段，贡献应保持范围清晰、证据充分和可独立验收。

## 开始之前

1. 阅读[项目定位](index.html)、[系统设计](components.html)和相关 ADR。
2. 搜索[开放与关闭 Issues](https://github.com/davidchen516/ontologyMVP/issues)，避免重复工作。
3. 对较大变更，先在 Issue 中说明目标、范围、非目标、依赖和验收方式。
4. 认领一个依赖已经满足、能够独立交付的能力边界。

## 本地检查

设计资产：

```bash
uv sync --frozen
uv run ruff check .
uv run pytest
uv run python scripts/validate_design.py
```

文档站：

```bash
bundle config set --local path vendor/bundle
bundle install
bundle exec jekyll build --baseurl /ontologyMVP --strict_front_matter
```

## 提交约束

- 不提交 TuShare Token、数据库密码、模型 Key、私有 URL 或真实敏感 Payload。
- 不把“计划能力”写成已经完成；真实状态应与 Issues 和测试证据一致。
- 不从平台概念标签直接生成量产、收入或供应关系。
- 不删除历史 Claim、Evidence 或 Provenance 来掩盖错误。
- 不让业务层直接依赖 Semantica 内部 API。
- 不把 Neo4j 变成事实权威或通过跨库同步双写绕过 Outbox。
- 不引入大型依赖来替代少量清晰的项目领域逻辑。

## 本体与词表贡献

除代码审查外，本体变更还需要：

- 说明新增概念与现有类/属性的差异；
- 保持 IRI 稳定，添加标签和定义；
- 检查 Domain/Range、SKOS 层级和循环；
- 同步 SHACL、规则、映射与样例；
- 说明语义版本变化和历史数据迁移；
- 由至少一名领域审核者确认。

## Pull Request 清单

- [ ] 目标、范围和非目标明确；
- [ ] 关联 Issue 和依赖关系正确；
- [ ] 变更保持 ADR 与权威数据边界；
- [ ] 正常、错误和边界场景有测试；
- [ ] 适用时覆盖重复、并发、崩溃恢复、权限拒绝和回滚；
- [ ] 提供真实运行、故障注入、监控或对账证据；
- [ ] 新依赖有许可证、版本、退出条件和 PoC 记录；
- [ ] 文档、Roadmap 和示例同步更新；
- [ ] 没有凭证、投资建议或未经授权的外部副作用。

## 许可证提醒

仓库当前还没有项目级 `LICENSE`。在维护者完成许可证决策前，请不要假设提交内容已经按某个开源许可证分发。维护者需要先明确贡献和项目许可证政策；详见[许可证与免责声明](license-and-disclaimer.html)。
