import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api/client";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingSkeleton } from "../components/states/States";

/**
 * 运维只读视图（issue #33）：能力矩阵、运行状态、投影水位、对账、健康。
 * 全部消费 /admin/* 只读端点——Operator 无认证要求（只读），
 * 但密钥/内部细节绝不渲染。
 */

interface AdminListResponse<T> {
  items?: T[];
  runs?: T[];
  total?: number;
}

async function adminList<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const data = await apiFetch<AdminListResponse<T>>(path, { signal });
  return data.items ?? data.runs ?? [];
}

export function OpsPage() {
  const capabilities = useQuery({
    queryKey: ["admin-capabilities"],
    queryFn: (ctx) => apiFetch<Record<string, unknown>>("/admin/capabilities", { signal: ctx.signal }),
  });
  const ingestRuns = useQuery({
    queryKey: ["admin-ingest-runs"],
    queryFn: (ctx) => adminList<Record<string, unknown>>("/admin/ingest-runs?limit=10", ctx.signal),
  });
  const normalizationRuns = useQuery({
    queryKey: ["admin-normalization-runs"],
    queryFn: (ctx) => adminList<Record<string, unknown>>("/admin/normalization-runs?limit=10", ctx.signal),
  });
  const projection = useQuery({
    queryKey: ["admin-projection"],
    queryFn: (ctx) => apiFetch<Record<string, unknown>>("/admin/projection/status", { signal: ctx.signal }),
  });
  const reconciliation = useQuery({
    queryKey: ["admin-reconciliation"],
    queryFn: (ctx) => apiFetch<Record<string, unknown>>("/admin/projection/reconciliation", { signal: ctx.signal }),
  });
  const freshness = useQuery({
    queryKey: ["admin-freshness"],
    queryFn: (ctx) => apiFetch<Record<string, unknown>>("/admin/data-freshness", { signal: ctx.signal }),
  });
  const reviewTasks = useQuery({
    queryKey: ["admin-review-tasks"],
    queryFn: (ctx) => adminList<Record<string, unknown>>("/admin/review-tasks?limit=5", ctx.signal),
  });

  return (
    <section aria-label="运维视图" className="space-y-6">
      <header>
        <h1 className="text-lg font-semibold">运维视图</h1>
        <p className="text-sm text-fg-muted">
          系统能力、运行状态、投影水位与对账（全部只读——危险操作不进入首版）。
        </p>
      </header>

      <Panel title="能力矩阵" query={capabilities}>
        {(data) => (
          <ul className="space-y-1 text-sm">
            {Object.entries(
              (data["capabilities"] as Record<string, unknown>) ?? data,
            ).map(([name, state]) => (
              <li key={name} className="flex items-center justify-between">
                <span>{name}</span>
                <span className={String(state).includes("OK") ? "text-success" : "text-fg-muted"}>
                  {String(state)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="最近采集运行" query={ingestRuns}>
          {(runs) => (
            <ul className="space-y-1 text-sm">
              {runs.slice(0, 8).map((run, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="truncate">{String(run["dataset_name"] ?? run["dataset"] ?? i)}</span>
                  <span className={String(run["status"]).includes("SUCCEEDED") ? "text-success" : "text-warning"}>
                    {String(run["status"])}
                  </span>
                </li>
              ))}
              {runs.length === 0 ? <li className="text-fg-muted">暂无运行记录</li> : null}
            </ul>
          )}
        </Panel>

        <Panel title="最近标准化运行" query={normalizationRuns}>
          {(runs) => (
            <ul className="space-y-1 text-sm">
              {runs.slice(0, 8).map((run, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="truncate">{String(run["dataset_name"] ?? i)}</span>
                  <span className={String(run["status"]).includes("SUCCEEDED") ? "text-success" : "text-warning"}>
                    {String(run["status"])}
                  </span>
                </li>
              ))}
              {runs.length === 0 ? <li className="text-fg-muted">暂无运行记录</li> : null}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="投影水位与对账" query={projection}>
        {(data) => (
          <div className="space-y-2 text-sm">
            <pre className="overflow-x-auto rounded-md bg-surface-muted p-3 text-xs">
              {JSON.stringify(data, null, 2)}
            </pre>
            {reconciliation.data ? (
              <div>
                <h3 className="text-xs font-semibold text-fg-muted">对账报告</h3>
                <pre className="mt-1 overflow-x-auto rounded-md bg-surface-muted p-3 text-xs">
                  {JSON.stringify(reconciliation.data, null, 2)}
                </pre>
              </div>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel title="数据新鲜度" query={freshness}>
        {(data) => (
          <pre className="overflow-x-auto rounded-md bg-surface-muted p-3 text-xs">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
      </Panel>

      <Panel title="待审任务（只读计数）" query={reviewTasks}>
        {(tasks) => (
          <p className="text-sm text-fg-muted">
            当前待审任务 {tasks.length} 条（顶部计数）——审核决定请前往
            Claim 审核工作台（需 Reviewer 权限）。
          </p>
        )}
      </Panel>
    </section>
  );
}

/** 面板壳：加载/错误态统一（失败不隐藏——Operator 必须看到异常）。 */
function Panel({
  title, query, children,
}: {
  title: string;
  query: { isPending: boolean; error: unknown; data: unknown; refetch: () => unknown };
  children: (data: never) => React.ReactNode;
}) {
  return (
    <div className="rounded-card border border-border bg-surface p-4 shadow-card">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {query.isPending ? <LoadingSkeleton rows={2} /> : null}
      {query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : null}
      {!query.isPending && !query.error && query.data !== undefined
        ? children(query.data as never)
        : null}
    </div>
  );
}
