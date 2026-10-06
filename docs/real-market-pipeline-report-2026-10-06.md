# 真实全市场管道报告（自动生成，脱敏）

- 运行时间：2026-10-06T13:41:09.343577+00:00
- 一次性库：real_market
- company-limit：300
- 请求数：探针 6 + 采集 427
- 运行时长：1112s

## 探针

| 数据集 | 状态 |
|---|---|
| stock_basic | AVAILABLE |
| stock_company | AVAILABLE |
| income_vip | AVAILABLE |
| cashflow_vip | AVAILABLE |
| fina_indicator_vip | AVAILABLE |
| fina_mainbz_vip | AVAILABLE |

## 采集

| 数据集 | 状态 | 行数(收/入/拒) | 请求数 |
|---|---|---|---|
| stock_basic | SUCCEEDED | 5572/5572/0 | 6 |
| income_vip | SUCCEEDED | 7104/7104/0 | 8 |
| cashflow_vip | SUCCEEDED | 9163/9163/0 | 10 |
| fina_indicator_vip | SUCCEEDED | 6837/6837/0 | 7 |
| fina_mainbz_vip | SUCCEEDED | 95954/95954/0 | 96 |
| stock_company | PARTIAL | 300/300/0 | 300 |

## 标准化

| 数据集 | 读 | 写 | 拒 |
|---|---|---|---|
| stock_basic | 5572 | 5572 | 0 |
| stock_company | 300 | 300 | 0 |
| income_vip | 7104 | 12300 | 929 |
| cashflow_vip | 9163 | 8284 | 878 |
| fina_indicator_vip | 6837 | 11848 | 913 |
| fina_mainbz_vip | 95954 | 4882 | 91072 |

## 主数据（真实）

- master_security_total: 5572
- master_company_total: 300
- company_security_total: 300
- financial_observation_total: 32432
- business_segment_total: 4882
- master_product_total: 1193
- claim_total: 0

## 投影（full_rebuild + 主营构成边）

- 实体：{}
- 主营构成 PRODUCES 边：2119
- claim（真实数据预期 0）：0

## 不变量校验

✅ 全部满足（security≥4000 / company≥90% 目标 / 财务观测≥10000 / 真实产品+经营边>0）
