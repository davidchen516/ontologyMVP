import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, overviewApi } from "../lib/api/endpoints";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";

/**
 * 首页（#30 里程碑）：真实系统状态 + 能力列表 + 交付导航。
 * 全部数据来自 /readyz 真实响应；无数据渲染空态，不放示例数字。
 */
export function HomePage() {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["readyz"],
    queryFn: api.readyz,
    refetchInterval: 30_000,
  });

  if (isPending) {
    return (
      <section aria-label="系统概览" className="space-y-4">
        <h1 className="text-lg font-semibold">系统概览</h1>
        <LoadingSkeleton rows={4} />
      </section>
    );
  }
  if (error) {
    return (
      <section aria-label="系统概览" className="space-y-4">
        <h1 className="text-lg font-semibold">系统概览</h1>
        <ErrorState error={error} onRetry={() => void refetch()} />
      </section>
    );
  }

  const componentEntries = Object.entries(data?.components ?? {});
  const capabilityEntries = Object.entries(data?.capabilities ?? {});

  return (
    <section aria-label="系统概览" className="space-y-6">
      <h1 className="text-lg font-semibold">系统概览</h1>

      <div className="flex flex-wrap items-center gap-3">
        <span className="rounded-card border border-border bg-surface px-3 py-1.5 text-sm">
          总体状态：
          <span
            className={
              data?.status === "OK"
                ? "text-success"
                : data?.status === "DEGRADED"
                  ? "text-warning"
                  : "text-danger"
            }
          >
            {data?.status ?? "未知"}
          </span>
        </span>
        <button
          type="button"
          onClick={() => void refetch()}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
        >
          刷新
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-card border border-border bg-surface p-4 shadow-card">
          <h2 className="mb-3 text-sm font-semibold">核心组件</h2>
          {componentEntries.length === 0 ? (
            <EmptyState title="暂无组件数据" />
          ) : (
            <ul className="space-y-2 text-sm">
              {componentEntries.map(([name, component]) => (
                <li key={name} className="flex items-center justify-between">
                  <span>{name}</span>
                  <span
                    className={
                      component.status === "OK" ? "text-success" : "text-danger"
                    }
                  >
                    {component.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-card border border-border bg-surface p-4 shadow-card">
          <h2 className="mb-3 text-sm font-semibold">可选能力</h2>
          {capabilityEntries.length === 0 ? (
            <EmptyState title="暂无能力数据" />
          ) : (
            <ul className="space-y-2 text-sm">
              {capabilityEntries.map(([name, capability]) => (
                <li key={name} className="flex items-center justify-between">
                  <span>{name}</span>
                  <span
                    className={
                      capability.status === "OK" ? "text-success" : "text-fg-muted"
                    }
                  >
                    {capability.status === "OK" ? "可用" : "未启用"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <StatsPanel />
      <RecentActivityPanel />

      <div className="rounded-card border border-border bg-surface p-4 shadow-card">
        <h2 className="mb-2 text-sm font-semibold">交付里程碑</h2>
        <p className="text-sm text-fg-muted">
          查询/图谱/证据/审核工作台将在 #31~#33 逐步开放（侧栏中显示
          "未启用" 的入口按里程碑解锁）。当前里程碑：#30 应用壳。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            to="/workbench/query"
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg"
          >
            打开查询工作台
          </Link>
          <Link
            to="/workbench/companies"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
          >
            浏览公司列表
          </Link>
        </div>
      </div>
    </section>
  );
}


/** 真实统计面板：全部来自 /overview/stats 实数；携带新鲜度与快照说明。 */
function StatsPanel() {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["overview-stats"],
    queryFn: (context) => overviewApi.stats(context.signal),
    refetchInterval: 60_000,
  });

  if (isPending) return <LoadingSkeleton rows={2} />;
  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }
  if (!data) return null;

  const cards: { label: string; value: number }[] = [
    { label: "公司主体", value: data.companies },
    { label: "已接受 Claim", value: data.accepted_claims },
    { label: "待审核 Claim", value: data.pending_reviews },
    { label: "证据片段", value: data.evidence_fragments },
    { label: "财务观察值", value: data.financial_observations },
  ];

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {cards.map((card) => (
          <div
            key={card.label}
            className="rounded-card border border-border bg-surface p-4 shadow-card"
          >
            <p className="text-xs text-fg-muted">{card.label}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{card.value}</p>
          </div>
        ))}
      </div>
      <p className="text-xs text-fg-muted">
        {data.snapshot_note}
        {data.data_freshness.latest_claim_at
          ? ` 最新事实记录：${data.data_freshness.latest_claim_at.slice(0, 19)}`
          : ""}
      </p>
    </div>
  );
}

/** 最近活动面板：最近采集/标准化运行（真实记录）。 */
function RecentActivityPanel() {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["overview-recent-runs"],
    queryFn: (context) => overviewApi.recentRuns(context.signal),
    refetchInterval: 60_000,
  });

  if (isPending) return null;
  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }
  if (!data || (data.ingest_runs.length === 0 && data.normalization_runs.length === 0)) {
    return null;
  }

  return (
    <div className="rounded-card border border-border bg-surface p-4 shadow-card">
      <h2 className="mb-3 text-sm font-semibold">最近活动</h2>
      <ul className="space-y-1 text-sm text-fg-muted">
        {data.ingest_runs.slice(0, 5).map((run) => (
          <li key={run.id} className="flex items-center justify-between gap-2">
            <span>采集 · {run.dataset_name}</span>
            <span className={run.status === "SUCCEEDED" ? "text-success" : "text-warning"}>
              {run.status}
            </span>
          </li>
        ))}
        {data.normalization_runs.slice(0, 5).map((run) => (
          <li key={run.id} className="flex items-center justify-between gap-2">
            <span>标准化 · {run.dataset_name}</span>
            <span className={run.status === "SUCCEEDED" ? "text-success" : "text-warning"}>
              {run.status}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
