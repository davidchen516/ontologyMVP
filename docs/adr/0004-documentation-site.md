---
title: ADR-0004：GitHub Pages 文档站选型
parent: 架构决策记录
grand_parent: 设计原文
nav_order: 4
permalink: /adr/0004-documentation-site.html
---

# ADR-0004：使用 Jekyll 与 Just the Docs 发布 GitHub Pages

- 状态：Accepted
- 日期：2026-09-29
- 决策者：ontologyMVP 项目组

## 背景

项目需要面向外部用户的静态使用说明站，能够复用现有 Markdown、展示 Mermaid 架构图、提供层级导航和本地搜索，并通过 GitHub Pages 以最小运维成本发布。仓库当前没有前端工程，也不需要服务端、账号、数据库或交互应用能力。

## 候选比较

| 方案 | 优点 | 主要问题 | 结论 |
|---|---|---|---|
| Jekyll 4.4.1 + Just the Docs 0.12.0 | GitHub Pages 原生契合；官方现有仓库模板；Markdown、导航、Lunr 搜索、Mermaid；MIT | 维护者集中；中文搜索分词能力需验证；Jekyll 发布节奏较慢 | 采用 |
| Docusaurus 3.10.2 | 维护活跃；版本化、i18n、Mermaid 和 React 扩展强 | Node/React/MDX 依赖更重；官方搜索依赖 Algolia，本地搜索为社区插件 | 当前需求过重；中文搜索成为硬要求时重新评估 |
| Material for MkDocs 9.7.7 | Markdown 体验成熟；内置搜索支持中文分词；配置简单 | 上游公告公共关键/安全修复将在 2026-11-05 结束，当前新建站点迁移风险过高 | 拒绝 |

## 决策

1. 使用 Jekyll `4.4.1` 与 Just the Docs `0.12.0`。
2. 通过 `Gemfile.lock` 固定完整依赖树，不使用未锁定的 `remote_theme`。
3. GitHub Actions 在 Pull Request 上只构建，在 `main` 推送时构建并发布 Pages。
4. 工作流只授予 `contents: read`、`pages: write` 和 `id-token: write`。
5. 主题只负责渲染、导航、客户端搜索和 Mermaid；业务设计仍以仓库 Markdown、ADR、本体资产和 Issues 为权威。
6. 不覆盖主题内部 layout/sidebar；只使用公开配置和支持的自定义 Sass 入口，降低升级耦合。
7. Mermaid 精确锁定为 `12.0.0`，通过 jsDelivr 加载；定期复核安全与兼容性。
8. 使用 Dependabot 跟踪 Bundler 和 GitHub Actions 更新。

## 责任边界

进入主路径的开源组件：

- **Jekyll**：将 Markdown 构建为静态 HTML；MIT。
- **Just the Docs**：提供文档布局、导航、Lunr 搜索和 Mermaid 集成；MIT。
- **GitHub Pages Actions**：构建产物上传与 Pages 部署。

保持项目自有：

- 文档内容、信息架构、ADR 与业务术语；
- Claim/Evidence、数据权威和安全边界；
- Pages 发布触发条件与最小权限配置。

本决策不会为 ontologyMVP 内容选择许可证；项目许可证仍是独立 TODO。

## 已知限制与采用门禁

- Just the Docs 的 Lunr 搜索未承诺中文分词。本次构建实测“事实主库”和“数据与证据模型”返回大量宽泛、排序不相关的结果，`PostgreSQL` 可以精确命中；当前搜索明确面向英文技术标识，中文用户使用分组导航和页面内查找。
- 不静默加入未经评估的 tokenizer。代表性中文查询继续作为后续搜索方案的采用门禁。
- 若完整中文搜索成为发布硬要求，优先重新比较 Docusaurus + Algolia 或成熟的 Jekyll 搜索扩展，并单独记录许可证、数据外发和运维影响。
- Mermaid 由外部 CDN 加载；离线阅读时图形可能不可用，但源代码和文字说明仍可读。
- Just the Docs 维护者集中度较高；依赖更新要复核发布说明和迁移指南。

## 验证与退出条件

验收：

- 干净环境可根据锁文件构建；
- Pages 工作流在 PR 构建、在 `main` 部署；
- 内部链接、导航和 Mermaid 正常；
- 桌面与移动端可读；
- 代表性中文与标识符搜索得到可接受结果；
- 构建产物不含 Secret 或内部数据。

退出或替换条件：

- Jekyll/Just the Docs 停止安全维护；
- GitHub Pages 不再兼容当前构建方式；
- 中文搜索、版本化、多语言或交互能力成为无法通过薄扩展满足的硬需求；
- 主题升级要求长期维护大量内部覆盖。

替换时保留普通 Markdown 和最小 front matter，避免内容绑定到主题私有组件。

## 官方参考

- [Just the Docs 官方模板](https://github.com/just-the-docs/just-the-docs-template)
- [Just the Docs 0.12.0](https://github.com/just-the-docs/just-the-docs/releases/tag/v0.12.0)
- [Jekyll 4.4.1](https://github.com/jekyll/jekyll/releases/tag/v4.4.1)
- [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Material for MkDocs 安全维护政策](https://github.com/squidfunk/mkdocs-material/blob/master/SECURITY.md)
- [Docusaurus 搜索说明](https://docusaurus.io/docs/search)
