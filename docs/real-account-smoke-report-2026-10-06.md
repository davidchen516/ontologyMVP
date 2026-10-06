# 真实账户冒烟报告（自动生成，脱敏）

- 运行时间：2026-10-06T08:24:00.495710+00:00
- 数据模式：real
- 额度消耗（请求数）：探针 6 + 采集 10 = 16

## 探针（能力矩阵）

| 数据集 | 状态 | expected 缺失 |
|---|---|---|
| stock_basic | AVAILABLE | exchange, list_status |
| stock_company | AVAILABLE | fullname |
| income_vip | AVAILABLE | - |
| balancesheet_vip | AVAILABLE | - |
| cashflow_vip | AVAILABLE | - |
| fina_indicator_vip | AVAILABLE | - |

## 采集（默认路径 + 候选钉住路径，每批 1 请求）

| 数据集 | 状态 | 行数(收/入/拒) | 请求数 |
|---|---|---|---|
| stock_basic | SUCCEEDED | 3/3/0 | 3 |
| stock_company | SUCCEEDED | 3/3/0 | 3 |
| income_vip | SUCCEEDED | 1/1/0 | 1 |
| balancesheet_vip | SUCCEEDED | 1/1/0 | 1 |
| cashflow_vip | SUCCEEDED | 1/1/0 | 1 |
| fina_indicator_vip | SUCCEEDED | 1/1/0 | 1 |

Claim 候选尝试：002230.SZ、000063.SZ；胜出（真实档案文本可抽取）：000063.SZ

## 标准化

| 数据集 | 写入 | 拒绝 |
|---|---|---|
| stock_basic | 3 | 0 |
| stock_company | 2 | 1 |
| income_vip | 2 | 0 |
| cashflow_vip | 1 | 0 |
| fina_indicator_vip | 2 | 0 |

## 证据/Claim（真实档案文本 → 规则抽取 → intake）

文档 2 / 片段 4 / 候选 1 / 接受 1

## 主数据与质量分布

- master_security_total: 3
- master_security_status_unknown: 3
- master_security_status_active: 0
- master_company_total: 2
- financial_observation_total: 5

### 真实响应字段形态（仅字段名，脱敏）

- `balancesheet_vip`: ['acc_exp', 'accounts_pay', 'accounts_receiv', 'accounts_receiv_bill', 'acc_receivable', 'acct_payable', 'acting_trading_sec', 'acting_uw_sec', 'adv_receipts', 'agency_bus_liab', 'amor_exp', 'ann_date', 'bond_payable', 'cap_rese', 'cash_reser_cb', 'cb_borr', 'cip', 'cip_total', 'client_depos', 'client_prov', 'comm_payable', 'comp_type', 'const_materials', 'contract_assets', 'contract_liab', 'cost_fin_assets', 'debt_invest', 'decr_in_disbur', 'defer_inc_non_cur_liab', 'deferred_inc', 'defer_tax_assets', 'defer_tax_liab', 'depos', 'depos_ib_deposits', 'depos_in_oth_bfi', 'depos_oth_bfi', 'depos_received', 'deriv_assets', 'deriv_liab', 'div_payable', 'div_receiv', 'end_date', 'end_type', 'estimated_liab', 'fa_avail_for_sale', 'fair_value_fin_assets', 'f_ann_date', 'fix_assets', 'fix_assets_total', 'fixed_assets_disp', 'forex_differ', 'goodwill', 'hfs_assets', 'hfs_sales', 'htm_invest', 'indem_payable', 'indep_acct_assets', 'indept_acc_liab', 'intan_assets', 'int_payable', 'int_receiv', 'inventories', 'invest_as_receiv', 'invest_loss_unconf', 'invest_real_estate', 'lending_funds', 'loan_oth_bank', 'loanto_oth_bank_fi', 'long_pay_total', 'lt_amor_exp', 'lt_borr', 'lt_eqt_invest', 'lt_payable', 'lt_payroll_payable', 'lt_rec', 'minority_int', 'money_cap', 'nca_within_1y', 'non_cur_liab_due_1y', 'notes_payable', 'notes_receiv', 'oil_and_gas_assets', 'ordin_risk_reser', 'oth_assets', 'oth_comp_income', 'oth_cur_assets', 'oth_cur_liab', 'oth_debt_invest', 'oth_eqt_tools', 'oth_eqt_tools_p_shr', 'oth_liab', 'oth_nca', 'oth_ncl', 'oth_payable', 'oth_pay_total', 'oth_rcv_total', 'oth_receiv', 'payables', 'payable_to_reinsurer', 'payroll_payable', 'ph_invest', 'ph_pledge_loans', 'pledge_borr', 'policy_div_payable', 'prec_metals', 'premium_receiv', 'prem_receiv_adva', 'prepayment', 'produc_bio_assets', 'pur_resale_fa', 'r_and_d', 'refund_cap_depos', 'refund_depos', 'reinsur_receiv', 'reinsur_res_receiv', 'report_type', 'reser_lins_liab', 'reser_lthins_liab', 'reser_outstd_claims', 'reser_une_prem', 'rr_reins_lins_liab', 'rr_reins_lthins_liab', 'rr_reins_outstd_cla', 'rr_reins_une_prem', 'rsrv_insur_cont', 'sett_rsrv', 'sold_for_repur_fa', 'special_rese', 'specific_payables', 'st_bonds_payable', 'st_borr', 'st_fin_payable', 'surplus_rese', 'taxes_payable', 'time_deposits', 'total_assets', 'total_cur_assets', 'total_cur_liab', 'total_hldr_eqy_exc_min_int', 'total_hldr_eqy_inc_min_int', 'total_liab', 'total_liab_hldr_eqy', 'total_nca', 'total_ncl', 'total_share', 'trad_asset', 'trading_fl', 'transac_seat_fee', 'treasury_share', 'ts_code', 'undistr_porfit', 'update_flag']
- `cashflow_vip`: ['amort_intang_assets', 'ann_date', 'beg_bal_cash', 'beg_bal_cash_equ', 'c_cash_equ_beg_period', 'c_cash_equ_end_period', 'c_disp_withdrwl_invest', 'c_fr_oth_operate_a', 'c_fr_sale_sg', 'c_inf_fr_operate_a', 'comp_type', 'conv_copbonds_due_within_1y', 'conv_debt_into_cap', 'c_paid_for_taxes', 'c_paid_goods_s', 'c_paid_invest', 'c_paid_to_for_empl', 'c_pay_acq_const_fiolta', 'c_pay_claims_orig_inco', 'c_pay_dist_dpcp_int_exp', 'c_prepay_amt_borr', 'c_recp_borrow', 'c_recp_cap_contrib', 'c_recp_return_invest', 'credit_impa_loss', 'decr_deferred_exp', 'decr_def_inc_tax_assets', 'decr_inventories', 'decr_oper_payable', 'depr_fa_coga_dpba', 'eff_fx_flu_cash', 'end_bal_cash', 'end_bal_cash_equ', 'end_date', 'end_type', 'fa_fnc_leases', 'f_ann_date', 'finan_exp', 'free_cashflow', 'ifc_cash_incr', 'im_net_cashflow_oper_act', 'im_n_incr_cash_equ', 'incl_cash_rec_saims', 'incl_dvd_profit_paid_sc_ms', 'incr_acc_exp', 'incr_def_inc_tax_liab', 'incr_oper_payable', 'invest_loss', 'loss_disp_fiolta', 'loss_fv_chg', 'loss_scr_fa', 'lt_amort_deferred_exp', 'n_cap_incr_repur', 'n_cashflow_act', 'n_cashflow_inv_act', 'n_cash_flows_fnc_act', 'n_depos_incr_fi', 'n_disp_subs_oth_biz', 'net_cash_rece_sec', 'net_dism_capital_add', 'net_profit', 'n_inc_borr_oth_fi', 'n_incr_cash_cash_equ', 'n_incr_clt_loan_adv', 'n_incr_dep_cbob', 'n_incr_disp_faas', 'n_incr_disp_tfa', 'n_incr_insured_dep', 'n_incr_loans_cb', 'n_incr_loans_oth_bank', 'n_incr_pledge_loan', 'n_recp_disp_fiolta', 'n_recp_disp_sobu', 'n_reinsur_prem', 'oth_cash_pay_oper_act', 'oth_cashpay_ral_fnc_act', 'oth_cash_recp_ral_fnc_act', 'others', 'oth_loss_asset', 'oth_pay_ral_inv_act', 'oth_recp_ral_inv_act', 'pay_comm_insur_plcy', 'pay_handling_chrg', 'prem_fr_orig_contr', 'proc_issue_bonds', 'prov_depr_assets', 'recp_tax_rends', 'report_type', 'st_cash_out_act', 'stot_cash_in_fnc_act', 'stot_cashout_fnc_act', 'stot_inflows_inv_act', 'stot_out_inv_act', 'ts_code', 'uncon_invest_loss', 'update_flag', 'use_right_asset_dep']
- `fina_indicator_vip`: ['adminexp_of_gr', 'ann_date', 'ar_turn', 'assets_to_eqt', 'assets_turn', 'assets_yoy', 'basic_eps_yoy', 'bps', 'bps_yoy', 'capital_rese_ps', 'cash_ratio', 'ca_to_assets', 'ca_turn', 'cfps', 'cfps_yoy', 'cogs_of_sales', 'currentdebt_to_debt', 'current_exint', 'current_ratio', 'debt_to_assets', 'debt_to_eqt', 'diluted2_eps', 'dp_assets_to_eqt', 'dt_eps', 'dt_eps_yoy', 'dt_netprofit_yoy', 'ebit', 'ebitda', 'ebit_of_gr', 'ebit_ps', 'ebt_yoy', 'end_date', 'eps', 'eqt_to_debt', 'eqt_to_interestdebt', 'eqt_to_talcapital', 'eqt_yoy', 'equity_yoy', 'expense_of_sales', 'extra_item', 'fa_turn', 'fcfe', 'fcfe_ps', 'fcff', 'fcff_ps', 'finaexp_of_gr', 'fixed_assets', 'gc_of_gr', 'gross_margin', 'grossprofit_margin', 'impai_ttm', 'interestdebt', 'int_to_talcap', 'invest_capital', 'longdeb_to_debt', 'nca_to_assets', 'netdebt', 'netprofit_margin', 'netprofit_yoy', 'networking_capital', 'noncurrent_exint', 'npta', 'ocfps', 'ocf_to_debt', 'ocf_to_shortdebt', 'ocf_yoy', 'op_income', 'op_of_gr', 'op_yoy', 'or_yoy', 'profit_dedt', 'profit_to_gr', 'profit_to_op', 'q_dt_roe', 'q_gc_to_gr', 'q_npta', 'q_ocf_to_sales', 'q_op_qoq', 'q_roe', 'q_saleexp_to_gr', 'q_sales_yoy', 'quick_ratio', 'retained_earnings', 'retainedps', 'revenue_ps', 'roa', 'roa2_yearly', 'roa_dp', 'roa_yearly', 'roe', 'roe_dt', 'roe_waa', 'roe_yearly', 'roe_yoy', 'roic', 'saleexp_to_gr', 'surplus_rese_ps', 'tangasset_to_intdebt', 'tangible_asset', 'tangibleasset_to_debt', 'tangibleasset_to_netdebt', 'tbassets_to_totalassets', 'total_revenue_ps', 'tr_yoy', 'ts_code', 'turn_days', 'undist_profit_ps', 'update_flag', 'working_capital']
- `income_vip`: ['adj_lossgain', 'admin_exp', 'ann_date', 'assets_impair_loss', 'ass_invest_income', 'basic_eps', 'biz_tax_surchg', 'capit_comstock_div', 'comm_exp', 'comm_income', 'compens_payout', 'compens_payout_refu', 'compr_inc_attr_m_s', 'compr_inc_attr_p', 'comp_type', 'comshare_payable_dvd', 'diluted_eps', 'distable_profit', 'distr_profit_shrhder', 'div_payt', 'ebit', 'ebitda', 'end_date', 'end_type', 'f_ann_date', 'fin_exp', 'fin_exp_int_exp', 'fin_exp_int_inc', 'forex_gain', 'fv_value_chg_gain', 'income_tax', 'insurance_exp', 'insur_reser_refu', 'int_exp', 'int_income', 'invest_income', 'minority_gain', 'n_asset_mg_income', 'nca_disploss', 'n_commis_income', 'n_income', 'n_income_attr_p', 'non_oper_exp', 'non_oper_income', 'n_oth_b_income', 'n_oth_income', 'n_sec_tb_income', 'n_sec_uw_income', 'operate_profit', 'oper_cost', 'oper_exp', 'oth_b_income', 'oth_compr_income', 'other_bus_cost', 'out_prem', 'prem_earned', 'prem_income', 'prem_refund', 'prfshare_payable_dvd', 'rd_exp', 'reins_cost_refund', 'reins_exp', 'reins_income', 'report_type', 'reser_insur_liab', 'revenue', 'sell_exp', 't_compr_income', 'total_cogs', 'total_profit', 'total_revenue', 'transfer_housing_imprest', 'transfer_oth', 'transfer_surplus_rese', 'ts_code', 'undist_profit', 'une_prem_reser', 'update_flag', 'withdra_biz_devfund', 'withdra_legal_pubfund', 'withdra_legal_surplus', 'withdra_oth_ersu', 'withdra_rese_fund', 'workers_welfare']
- `stock_basic`: ['act_ent_type', 'act_name', 'area', 'cnspell', 'industry', 'list_date', 'market', 'name', 'symbol', 'ts_code']
- `stock_company`: ['business_scope', 'chairman', 'city', 'com_id', 'com_name', 'email', 'employees', 'exchange', 'introduction', 'main_business', 'manager', 'office', 'province', 'reg_capital', 'secretary', 'setup_date', 'ts_code', 'website']

## 查询 API

```json
{
 "http_status": 200,
 "query_status": "SUCCEEDED",
 "result_count": 1,
 "companies": [
  {
   "company_name": "中兴通讯股份有限公司",
   "security_code": "000063.SZ",
   "claim_ids": 1,
   "evidence_quotes": 1,
   "business_stage": "RESEARCH"
  }
 ]
}
```

## 不变量校验

✅ 全部端到端不变量满足（security>0 / company>0 / screen≥1 真实公司 / 无 SCHEMA_CHANGED 熔断）
