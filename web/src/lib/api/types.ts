/**
 * 后端契约类型（受控、手写严格化）。
 *
 * 与 FastAPI 契约的一致性由 tests/api-contract.test.ts 守护：
 * 后端漂移（路径/字段缺失）会让契约测试失败，而不是运行时爆炸。
 */

export type ReadinessStatus = "OK" | "DEGRADED" | "FAIL";

export interface ComponentStatus {
  status: "OK" | "FAIL";
  error_type?: string | null;
  detail?: string | null;
}

export interface CapabilityStatus {
  status: "OK" | "UNAVAILABLE";
}

export interface ReadinessReport {
  status: ReadinessStatus;
  components: Record<string, ComponentStatus>;
  capabilities: Record<string, CapabilityStatus>;
}

export interface GroundedResult {
  company_id: string;
  company_name: string;
  security_code: string | null;
  standard_product: string | null;
  claim_ids: string[];
  business_stage: string | null;
  evidence_state: string | null;
  valid_from: string | null;
  valid_to: string | null;
  financial_value: number | null;
  report_period: string | null;
  currency: string | null;
  financial_detail: { period_end: string; value: number }[];
  evidence_ids: string[];
  evidence_quotes: {
    evidence_id: string;
    page_number: number | null;
    quote_text: string;
  }[];
  reasoning_path: string[];
  data_freshness: string | null;
}

export interface QueryResponse {
  query_id: string;
  status: string;
  intent: string;
  results: GroundedResult[];
  excluded: string[];
  unknowns: string[];
  conflicts: string[];
  period_rule: string | null;
  degraded: boolean;
  degradation_notes: string[];
  trace_id: string | null;
  error: string | null;
}

export interface ScreenRequest {
  concept_name?: string | null;
  business_stage?: string | null;
  metric_code?: string | null;
  operator?: "TOTAL_POSITIVE" | "CONSECUTIVE_POSITIVE" | "MIN_VALUE" | "MAX_VALUE";
  threshold?: number | null;
  fiscal_years?: 3 | 5;
  evidence_required?: boolean;
  as_of?: string | null;
  max_results?: number;
}

export interface CompanyRecord {
  id: string;
  canonical_name: string;
  unified_social_credit_code: string | null;
  company_type: string | null;
  status: string | null;
  created_at: string;
}

export interface ClaimRecord {
  id: string;
  predicate_code: string;
  claim_status: string;
  business_stage: string | null;
  evidence_state: string | null;
  confidence: number | null;
  valid_from: string | null;
  valid_to: string | null;
  recorded_at: string;
}

export interface CompanyListItem {
  id: string;
  canonical_name: string;
  unified_social_credit_code: string | null;
  company_type: string | null;
  status: string | null;
  created_at: string;
  security_code: string | null;
  produces_claims: number;
}

export interface CompanyListResponse {
  companies: CompanyListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface OverviewStats {
  companies: number;
  accepted_claims: number;
  pending_reviews: number;
  evidence_fragments: number;
  financial_observations: number;
  data_freshness: {
    latest_claim_at: string | null;
    latest_observation_at: string | null;
  };
  snapshot_note: string;
}

export interface RunRecord {
  id: string;
  dataset_name: string;
  status: string;
  finished_at: string | null;
  source_system?: string;
}

export interface RecentRunsResponse {
  ingest_runs: RunRecord[];
  normalization_runs: RunRecord[];
}
