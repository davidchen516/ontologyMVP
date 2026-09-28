---
title: FAQ
parent: 快速开始
nav_order: 1
permalink: /faq.html
---

# FAQ

## 现在能直接启动完整系统吗？

可以启动工程基线，但不能运行完整业务场景。当前仓库能启动 PostgreSQL、Neo4j、API 和 Worker，并提供健康检查；数据库迁移、TuShare 数据管道、Claim/Outbox、图投影和证据化查询仍由 [Roadmap](roadmap.html) 跟踪。启动方法见[快速开始](getting-started.html)。

## 为什么同时使用 PostgreSQL 和 Neo4j？

PostgreSQL 适合权威事务、财务计算、证据、审核和任务状态；Neo4j 适合产品层级与产业链多跳路径。两者通过 Transactional Outbox 解耦，Neo4j 可以从 PostgreSQL 重建。

## 为什么不把所有内容直接存成图边？

普通图边难以完整表达来源、原文、审核状态、冲突和双时态。Claim 作为一等对象保留这些信息，高频图边只是带 `claim_id` 的投影。

## Semantica 是硬依赖吗？

V0.1 计划使用 Semantica `0.7.0`，但业务层只依赖项目的 `SemanticRuntime` 端口。如果契约、维护或运维要求不再满足，可以替换 Adapter，而不重写领域模型。

## 没有找到证据，是否说明公司没有这项业务？

不是。系统必须返回 `EVIDENCE_INSUFFICIENT` 或 `UNKNOWN`，不能把未发现证据自动解释为否定。公司正式否认需要单独的可定位来源。

## TuShare Token 放在哪里？

复制 `.env.example` 为未跟踪的 `.env`，仅在其中填写自己的 `TUSHARE_TOKEN`，或使用 Secret Store/环境变量注入。当前配置层会据此报告能力状态，但 Connector 尚未实现。不要把 Token 写入仓库、Issue、日志、Fixture 或截图；Pull Request CI 不需要真实 Token。

## 是否可以执行任意自然语言查询？

自然语言只能被转换为受控 `QueryPlan`。实体、关系、指标、操作符、跳数和结果数都经过白名单校验；LLM 返回的 SQL、Cypher、URL 或工具指令不会执行。

## 这个项目会给出投资建议吗？

不会。项目解释业务事实与证据，不预测价格，不给出买卖指令，也不提供自动交易接口。

## 仓库已经是开源许可证了吗？

还不是。仓库当前没有项目级 `LICENSE`。公开可见不等于已经授予开源许可，详见[许可证与免责声明](license-and-disclaimer.html)。

## 如何贡献？

先搜索现有 Issues，选择依赖已满足且可独立验收的任务，再阅读[贡献指南](contributing.html)。较大的架构或本体变更应先形成 Issue 或 ADR。

## 为什么站内搜索更适合英文标识符？

当前 Just the Docs 使用 Lunr，本次实测 `PostgreSQL`、`Claim` 等标识符可以命中，但中文词组没有可靠分词和召回。中文阅读请优先使用分组导航、页面目录或浏览器页内查找。完整中文搜索如果成为硬要求，将按 [ADR-0004](adr/0004-documentation-site.html) 重新评估成熟方案，不会临时加入未经审查的 tokenizer。
