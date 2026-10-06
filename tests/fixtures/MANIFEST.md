# Fixture 清单（issue #10：固定版本、内容 Hash、来源说明与更新流程）

清单版本：v2（与 MANIFEST.yaml 的 version 字段同步，由
tests/unit/test_fixture_manifest.py 守护）

所有 Fixture 均为**手工审定的静态快照**：CI 离线运行、结果确定、不含
真实 Token/密码/个人数据。本文件由 `tests/fixtures/MANIFEST.yaml`
（机器可读 Hash 清单）与下述流程说明组成；测试启动时校验 Hash 不匹配
立即失败（`tests/unit/test_fixture_manifest.py`）。

## 目录与来源

| 目录 | 来源 | 数据集 |
|------|------|--------|
| `tushare/` | TuShare Pro API 公开接口样例（脱敏手工重制；`stock_basic.low_tier.json` 为低层级账户字段子集形态，issue #43 字段层级覆盖） | stock_basic / stock_company / namechange / ths_index / ths_member / dc_index / dc_member / index_classify / index_member_all / fina 三表 / fina_mainbz_vip / top10_holders / anns_d / stk_surv / irm_qa_sh|sz + 6 个错误响应 |
| `claims/` | 披露文档语料样例（手工构造的合成文本） | （预留目录，#7 Claim 抽取语料按需补充） |

约定：
- 一个文件 = 一个 TuShare API 名称（与 `ontology/mappings/tushare.yaml`
  的注册名一致）；
- `error_*.json` = 采集器错误路径的响应形态（HTTP 429 / 无权限 / 参数
  错误等），用于故障注入与负向场景；
- 文件内容为最小可行样例（几行~几十行记录），不含真实客户数据。

## 更新流程（受控）

1. 修改任何 Fixture 文件前，在本文件与 MANIFEST.yaml 中登记变更理由
   （如：接口新增字段、覆盖新分支）；
2. 更新文件后重算 SHA-256 并同步 MANIFEST.yaml；
3. 在 PR 中说明影响面（哪些测试行为会变化）并经独立审查确认；
4. 回滚：git revert 恢复上一版本（文件即基线，无外部状态）。

禁止事项：
- 禁止在 Fixture 中写入真实 Token、密码、签名 URL 或完整敏感 Payload；
- 禁止用修改 Fixture 的方式"修复"失败测试（Fixture 是输入契约，
  不是断言）。
