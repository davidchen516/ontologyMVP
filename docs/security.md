---
title: 安全说明
parent: 开发与治理
nav_order: 4
permalink: /security.html
---

# 安全说明

## 报告漏洞

请不要在公开 Issue 中披露尚未修复的漏洞、真实 Token、利用代码或敏感数据。

优先使用仓库的 **Security → Report a vulnerability** 私密通道（如果已启用）。如果该入口不可用，请先通过 [GitHub 仓库所有者主页](https://github.com/davidchen516) 联系维护者，确认私密报告渠道后再发送技术细节。

报告建议包含：

- 受影响的组件、版本或提交；
- 可复现步骤和最小 PoC；
- 影响与攻击前提；
- 已尝试的缓解方式；
- 是否已在其他位置公开。

当前尚无正式发布版本或安全支持周期；维护者应在首次发布前补充支持版本和响应目标。

## Secret 与 TuShare Token

- Secret 只通过环境变量、CI Secret 或专用 Secret Store 注入。
- `.env.example` 只能包含变量名和无效占位符。
- Token 不得进入 Git 历史、日志、Fixture、缓存、错误响应、截图或文档。
- CI 默认不访问真实 TuShare、LLM、数据库或官方站点。
- 在线契约任务必须显式触发、只读、限流，并使用最小权限凭证。
- 发现泄露时立即轮换凭证；仅删除当前文件不足以消除 Git 历史和日志中的暴露。

## 数据库与接口

- API 使用只读数据库角色；Worker 只有完成任务所需的写权限；Migration 使用独立角色。
- 所有 SQL/Cypher 使用参数化模板和白名单；不执行 LLM 或用户返回的自由查询。
- 管理操作鉴权、限流并写审计；日志脱敏但保留 `trace_id`。
- 文档下载限制域名、协议、大小、MIME、重定向和解析资源，防止 SSRF、压缩炸弹和解析器利用。
- 文件内容、网页和 Prompt 都视为不可信数据，不执行其中指令。

## 供应链与 Pages

- 文档构建依赖由 `Gemfile.lock` 固定，并通过 Dependabot 跟踪 Bundler 与 Actions 更新。
- Pages 工作流使用最小权限；Pull Request 只构建，不部署。
- 构建产物是公开内容，不得包含凭证、内部地址、私有数据或未公开的验收报告。
- GitHub Pages 强制 HTTPS；外部脚本按精确版本加载并定期复核。

详细运行时安全设计见[质量、安全与测试方案](quality-and-testing.html#8-安全设计)。
