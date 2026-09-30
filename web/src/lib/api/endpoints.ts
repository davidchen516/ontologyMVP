import { apiFetch } from "./client";
import type {
  ClaimRecord,
  CompanyRecord,
  QueryResponse,
  ReadinessReport,
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
