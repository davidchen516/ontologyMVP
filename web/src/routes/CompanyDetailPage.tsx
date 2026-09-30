import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api/endpoints";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";

/** 公司详情页（issue #31）：基本信息 + Claim 列表 + 时间线。 */
export function CompanyDetailPage() {
  const { companyId } = useParams<{ companyId: string }>();

  const company = useQuery({
    queryKey: ["company", companyId],
    queryFn: (context) => api.company(companyId!, context.signal),
    enabled: Boolean(companyId),
  });
  const claims = useQuery({
    queryKey: ["company-claims", companyId],
    queryFn: (context) => api.companyClaims(companyId!, context.signal),
    enabled: Boolean(companyId),
  });
  const timeline = useQuery({
    queryKey: ["company-timeline", companyId],
    queryFn: (context) => api.companyTimeline(companyId!, context.signal),
    enabled: Boolean(companyId),
  });

  if (company.isPending) return <LoadingSkeleton rows={4} />;
  if (company.error) {
    return <ErrorState error={company.error} onRetry={() => void company.refetch()} />;
  }
  const c = company.data;

  return (
    <section aria-label="公司详情" className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold">{c.canonical_name}</h1>
        <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm md:grid-cols-2">
          <div className="flex gap-2">
            <dt className="text-fg-muted">主体 ID：</dt>
            <dd className="font-mono text-xs">{c.id}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">统一社会信用代码：</dt>
            <dd className="font-mono text-xs">{c.unified_social_credit_code ?? "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">类型：</dt>
            <dd>{c.company_type ?? "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">状态：</dt>
            <dd>{c.status ?? "—"}</dd>
          </div>
        </dl>
      </header>

      <div className="rounded-card border border-border bg-surface p-4 shadow-card">
        <h2 className="mb-3 text-sm font-semibold">Claim（{claims.data?.count ?? "…"}）</h2>
        {claims.isPending ? <LoadingSkeleton rows={3} /> : null}
        {claims.error ? (
          <ErrorState error={claims.error} onRetry={() => void claims.refetch()} />
        ) : null}
        {claims.data && claims.data.claims.length === 0 ? (
          <EmptyState title="该公司暂无 Claim 记录" />
        ) : null}
        {claims.data && claims.data.claims.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4">谓词</th>
                  <th scope="col" className="py-2 pr-4">状态</th>
                  <th scope="col" className="py-2 pr-4">业务阶段</th>
                  <th scope="col" className="py-2 pr-4">置信度</th>
                  <th scope="col" className="py-2 pr-4">有效期</th>
                  <th scope="col" className="py-2">记录时间</th>
                </tr>
              </thead>
              <tbody>
                {claims.data.claims.map((claim) => (
                  <tr key={claim.id} className="border-t border-border">
                    <td className="py-2 pr-4 font-mono text-xs">{claim.predicate_code}</td>
                    <td className="py-2 pr-4">{claim.claim_status}</td>
                    <td className="py-2 pr-4">{claim.business_stage ?? "—"}</td>
                    <td className="py-2 pr-4 tabular-nums">
                      {claim.confidence ?? "—"}
                    </td>
                    <td className="py-2 pr-4 text-xs text-fg-muted">
                      {claim.valid_from ?? "开放"} ~ {claim.valid_to ?? "至今"}
                    </td>
                    <td className="py-2 text-xs text-fg-muted">
                      {claim.recorded_at.slice(0, 19)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <div className="rounded-card border border-border bg-surface p-4 shadow-card">
        <h2 className="mb-3 text-sm font-semibold">
          时间线（ACCEPTED，{timeline.data?.count ?? "…"} 条）
        </h2>
        {timeline.isPending ? <LoadingSkeleton rows={2} /> : null}
        {timeline.error ? (
          <ErrorState error={timeline.error} onRetry={() => void timeline.refetch()} />
        ) : null}
        {timeline.data && timeline.data.count === 0 ? (
          <EmptyState title="暂无 ACCEPTED 时间线事件" />
        ) : null}
      </div>
    </section>
  );
}
