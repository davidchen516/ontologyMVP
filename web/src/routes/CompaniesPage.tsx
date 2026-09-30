import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { overviewApi } from "../lib/api/endpoints";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";

const PAGE_SIZE = 20;

/** 公司搜索/列表页（issue #31）：参数化搜索 + 分页 + 量产计数。 */
export function CompaniesPage() {
  const [search, setSearch] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [page, setPage] = useState(0);

  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["companies", submittedQuery, page],
    queryFn: (context) =>
      overviewApi.companies(
        { q: submittedQuery || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE },
        context.signal,
      ),
    placeholderData: keepPreviousData,
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setSubmittedQuery(search);
    setPage(0);
  };

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section aria-label="公司列表" className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">公司列表</h1>
        <p className="text-sm text-fg-muted">
          共 {total} 家主体（含合成快照公司——以数据快照说明为准）。
        </p>
      </header>

      <form onSubmit={submit} className="flex gap-2">
        <input
          type="search"
          value={search}
          maxLength={200}
          placeholder="按公司名称搜索…"
          aria-label="搜索公司名称"
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm"
        />
        <button
          type="submit"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg"
        >
          搜索
        </button>
      </form>

      {isPending ? <LoadingSkeleton rows={5} /> : null}
      {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : null}

      {data && data.companies.length === 0 ? (
        <EmptyState
          title={submittedQuery ? `未找到匹配“${submittedQuery}”的公司` : "暂无公司数据"}
          hint={submittedQuery ? "尝试其他关键词，或清除搜索查看全部。" : undefined}
        />
      ) : null}

      {data && data.companies.length > 0 ? (
        <div className="overflow-x-auto rounded-card border border-border bg-surface shadow-card">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs text-fg-muted">
              <tr>
                <th scope="col" className="px-4 py-3">公司</th>
                <th scope="col" className="px-4 py-3">证券代码</th>
                <th scope="col" className="px-4 py-3">类型</th>
                <th scope="col" className="px-4 py-3 text-right">量产 Claim</th>
              </tr>
            </thead>
            <tbody>
              {data.companies.map((company) => (
                <tr key={company.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <Link
                      to={`/workbench/companies/${company.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {company.canonical_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {company.security_code ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-fg-muted">
                    {company.company_type ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {company.produces_claims}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {pageCount > 1 ? (
        <nav aria-label="分页" className="flex items-center justify-between text-sm">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40"
          >
            上一页
          </button>
          <span>
            第 {page + 1} / {pageCount} 页
          </span>
          <button
            type="button"
            disabled={page + 1 >= pageCount}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40"
          >
            下一页
          </button>
        </nav>
      ) : null}
    </section>
  );
}
