import { apiFetch } from "./client";
import type {
  ClaimRecord,
  CompanyListResponse,
  CompanyRecord,
  OverviewStats,
  QueryResponse,
  ReadinessReport,
  RecentRunsResponse,
  ScreenRequest,
} from "./types";

export const api = {
  readyz: () => apiFetch<ReadinessReport>("/readyz"),
  healthz: () => apiFetch<{ status: string }>("/healthz"),

  screen: (body: ScreenRequest, signal?: AbortSignal) =>
    apiFetch<QueryResponse>("/api/v1/screen", {
      method: "POST",
      body,
      signal,
    }),

  query: (body: { plan: Record<string, unknown> }, signal?: AbortSignal) =>
    apiFetch<QueryResponse>("/api/v1/query", { method: "POST", body, signal }),

  company: (companyId: string, signal?: AbortSignal) =>
    apiFetch<CompanyRecord>(`/api/v1/companies/${companyId}`, { signal }),

  companyClaims: (companyId: string, signal?: AbortSignal) =>
    apiFetch<{ company_id: string; claims: ClaimRecord[]; count: number }>(
      `/api/v1/companies/${companyId}/claims`,
      { signal },
    ),

  companyTimeline: (companyId: string, signal?: AbortSignal) =>
    apiFetch<{ company_id: string; timeline: unknown[]; count: number }>(
      `/api/v1/companies/${companyId}/timeline`,
      { signal },
    ),
};

export const overviewApi = {
  stats: (signal?: AbortSignal) =>
    apiFetch<OverviewStats>("/api/v1/overview/stats", { signal }),
  recentRuns: (signal?: AbortSignal) =>
    apiFetch<RecentRunsResponse>("/api/v1/overview/recent-runs", { signal }),
  companies: (
    params: { q?: string; stage?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.stage) search.set("stage", params.stage);
    if (params.limit) search.set("limit", String(params.limit));
    if (params.offset) search.set("offset", String(params.offset));
    const qs = search.size > 0 ? `?${search.toString()}` : "";
    return apiFetch<CompanyListResponse>(`/api/v1/companies${qs}`, { signal });
  },
};
