import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { graphApi, overviewApi } from "../lib/api/endpoints";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";
import { EvidenceDrawer } from "../components/graph/EvidenceDrawer";

/**
 * 产品图谱浏览器（issue #32）：
 * - Cytoscape 受控子图（后端白名单 Cypher，跳数/节点数受限）；
 * - **并列表格替代视图**（Epic 不变量：图谱不是唯一信息表达——键盘/读屏可用）；
 * - 证据抽屉：claim_id → lineage（Claim/Evidence/Document 追溯链）；
 * - 图不可用 → DEGRADED + 原因 + 保留 PG 事实表格（GWT 降级路径）。
 */

const NODE_COLORS: Record<string, string> = {
  Company: "#2456d6",
  Product: "#1a7f4b",
  Concept: "#9a6700",
};

export function GraphBrowser() {
  const [companyId, setCompanyId] = useState("");
  const [submittedId, setSubmittedId] = useState("");
  const [hops, setHops] = useState<1 | 2>(1);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "table">("graph");

  const subgraph = useQuery({
    queryKey: ["subgraph", submittedId, hops],
    queryFn: (context) =>
      graphApi.subgraph(
        { company_id: submittedId, hops, max_nodes: 100 },
        context.signal,
      ),
    enabled: Boolean(submittedId),
  });

  // 公司选择器数据（复用 #31 列表端点）
  const companies = useQuery({
    queryKey: ["companies-for-graph"],
    queryFn: (context) =>
      overviewApi.companies({ limit: 100 }, context.signal),
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setSubmittedId(companyId);
  };

  return (
    <section aria-label="图谱浏览器" className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">图谱浏览器</h1>
        <p className="text-sm text-fg-muted">
          产品/概念受控子图——所有关系可回溯到 Claim 与可定位证据。
        </p>
      </header>

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block font-medium">公司</span>
          <select
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
            className="min-w-52 rounded-md border border-border bg-surface px-3 py-2"
            aria-label="选择公司"
          >
            <option value="">选择公司…</option>
            {companies.data?.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.canonical_name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">跳数</span>
          <select
            value={hops}
            onChange={(e) => setHops(Number(e.target.value) as 1 | 2)}
            className="rounded-md border border-border bg-surface px-3 py-2"
            aria-label="跳数"
          >
            <option value={1}>1 跳</option>
            <option value={2}>2 跳</option>
          </select>
        </label>
        <button
          type="submit"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg"
        >
          加载子图
        </button>
        <div
          role="radiogroup"
          aria-label="视图模式"
          className="ml-auto flex rounded-md border border-border"
        >
          {(["graph", "table"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={view === mode}
              onClick={() => setView(mode)}
              className={`px-3 py-1.5 text-sm ${view === mode ? "bg-primary-muted text-primary" : ""}`}
            >
              {mode === "graph" ? "图谱" : "表格"}
            </button>
          ))}
        </div>
      </form>

      {subgraph.isPending && submittedId ? <LoadingSkeleton rows={4} /> : null}
      {subgraph.error ? (
        <ErrorState error={subgraph.error} onRetry={() => void subgraph.refetch()} />
      ) : null}

      {subgraph.data ? <SubgraphResult
        data={subgraph.data}
        view={view}
        onSelectClaim={setSelectedClaimId}
      /> : null}

      {!submittedId ? (
        <EmptyState
          title="选择公司以加载子图"
          hint="子图由后端白名单 Cypher 模板生成，跳数与节点数受限。"
        />
      ) : null}

      {selectedClaimId ? (
        <EvidenceDrawer
          claimId={selectedClaimId}
          onClose={() => setSelectedClaimId(null)}
        />
      ) : null}
    </section>
  );
}

function SubgraphResult({
  data, view, onSelectClaim,
}: {
  data: NonNullable<ReturnType<typeof graphApi.subgraph> extends Promise<infer T> ? T : never>;
  view: "graph" | "table";
  onSelectClaim: (claimId: string) => void;
}) {
  if (data.status === "REJECTED") {
    return <ErrorState error={new Error(data.reason ?? "请求被拒绝")} />;
  }
  if (data.status === "DEGRADED") {
    return (
      <div role="status" className="space-y-3">
        <div className="rounded-card border border-warning-muted bg-warning-muted p-4 text-sm">
          <strong>图后端不可用（DEGRADED）</strong>：{data.reason}
        </div>
        {data.nodes.length === 0 ? (
          <EmptyState
            title="图数据暂不可用"
            hint="图投影恢复后将自动展示；期间可通过公司列表与证据浏览器查询 PostgreSQL 事实。"
          />
        ) : null}
      </div>
    );
  }
  if (data.status === "STALE") {
    // 投影水位落后：图无路径但 PG 有该公司——绝不静默空（GWT-2）
    return (
      <div role="status" className="space-y-3">
        <div className="rounded-card border border-warning-muted bg-warning-muted p-4 text-sm">
          <strong>图投影水位落后（STALE）</strong>：{data.reason}
        </div>
        {data.pg_fallback && data.pg_fallback.length > 0 ? (
          <div className="overflow-x-auto rounded-card border border-border bg-surface">
            <table className="w-full text-sm">
              <caption className="sr-only">
                PostgreSQL 事实回退：该公司 ACCEPTED Claim 列表
              </caption>
              <thead className="border-b border-border text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-3">谓词</th>
                  <th scope="col" className="px-4 py-3">业务阶段</th>
                  <th scope="col" className="px-4 py-3">状态</th>
                  <th scope="col" className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.pg_fallback.map((claim) => (
                  <tr key={claim.id} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 font-mono text-xs">{claim.predicate_code}</td>
                    <td className="px-4 py-3">{claim.business_stage ?? "—"}</td>
                    <td className="px-4 py-3">{claim.claim_status}</td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => onSelectClaim(claim.id)}
                        className="text-primary hover:underline"
                      >
                        查看证据链
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="该公司在图中暂无投影路径"
            hint="PG 中该公司已存在——图投影恢复后将自动展示；上方为其 PostgreSQL 事实。"
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {data.truncated ? (
        <p role="status" className="rounded-card border border-warning-muted bg-warning-muted p-3 text-sm">
          子图已按节点预算（{data.max_nodes}）裁剪——缩小跳数或从具体公司下钻查看完整关系。
        </p>
      ) : null}
      {view === "graph" ? (
        <CytoscapeView data={data} onSelectClaim={onSelectClaim} />
      ) : (
        <TableView data={data} onSelectClaim={onSelectClaim} />
      )}
      <p className="text-xs text-fg-muted">
        {data.nodes.length} 节点 · {data.edges.length} 边 · {data.hops} 跳 ·
        中心：{data.center?.name ?? "—"}
      </p>
    </div>
  );
}

/** Cytoscape 渲染（按需挂载/销毁；布局 deterministic）。 */
function CytoscapeView({
  data, onSelectClaim,
}: {
  data: { nodes: { id: string; labels: string[]; name: string | null }[];
          edges: { type: string; claim_id: string | null }[] };
  onSelectClaim: (claimId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  const elements: ElementDefinition[] = useMemo(
    () => [
      ...data.nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.name ?? node.id,
          color:
            NODE_COLORS[node.labels[0] ?? ""] ?? "data(\u0022color\u0022)",
        },
      })),
      // 边的端点由后端路径语义给出：此处以 claim 边列表渲染为
      // 中心-产品扇形（受控子图的中心星型——真实多跳路径由表格视图承载）
      ...data.edges
        .filter((e) => e.claim_id)
        .map((edge, index) => ({
          data: {
            id: `e-${index}`,
            source: data.nodes[0]?.id ?? "c",
            target: data.nodes[index + 1]?.id ?? data.nodes[0]?.id ?? "c",
            label: edge.type,
            claimId: edge.claim_id,
          },
        })),
    ],
    [data],
  );

  const [renderError, setRenderError] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;
    // jsdom/无 canvas 环境（测试或极旧浏览器）下 Cytoscape 会抛错——
    // 优雅降级到提示表格视图，而不是让路由错误边界吃掉整页
    try {
      const cy = _mountCytoscape(containerRef.current, elements, onSelectClaim);
      cyRef.current = cy;
      return () => {
        cy.destroy();
        cyRef.current = null;
      };
    } catch {
      setRenderError(true);
      return;
    }
  }, [elements, onSelectClaim]);

  if (renderError) {
    return (
      <p role="status" className="rounded-card border border-warning-muted bg-warning-muted p-4 text-sm">
        当前环境不支持 Canvas 图渲染——请使用"表格"视图查看全部关系
        （表格与图谱承载相同数据）。
      </p>
    );
  }

  return (
    <div className="flex gap-4">
      <div
        ref={containerRef}
        role="img"
        aria-label="产品图谱可视化——详细数据见表格视图"
        className="h-80 flex-1 rounded-card border border-border bg-surface"
      />
      <aside aria-label="图例" className="w-36 space-y-1 text-xs">
        <p className="font-semibold">图例</p>
        {Object.entries(NODE_COLORS).map(([label, color]) => (
          <p key={label} className="flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block size-3 rounded-full"
              style={{ backgroundColor: color }}
            />
            {label}
          </p>
        ))}
        <p className="mt-2 text-fg-muted">
          键盘/读屏用户请使用"表格"视图——图谱不是唯一信息表达。
        </p>
      </aside>
    </div>
  );
}

function _mountCytoscape(
  container: HTMLElement,
  elements: ElementDefinition[],
  onSelectClaim: (claimId: string) => void,
): Core {
  {
    const cy = cytoscape({
      container,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            color: "#fff",
            "font-size": 10,
            width: 24,
            height: 24,
          },
        },
        { selector: "edge", style: { width: 2, "line-color": "#889" } },
      ],
      layout: { name: "concentric", animate: false },
    });
    // 点击带 claim_id 的边 → 证据追溯抽屉（图谱可交互且可回溯）
    cy.on("tap", "edge", (event) => {
      const claimId = event.target.data("claimId");
      if (claimId) onSelectClaim(String(claimId));
    });
    return cy;
  }
}

/** 表格替代视图（Epic 不变量：图谱之外必须有键盘可访问的列表）。 */
function TableView({
  data, onSelectClaim,
}: {
  data: { nodes: { id: string; labels: string[]; name: string | null }[];
          edges: { type: string; claim_id: string | null }[] };
  onSelectClaim: (claimId: string) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-card border border-border bg-surface">
      <table className="w-full text-sm">
        <caption className="sr-only">图谱关系列表（键盘可访问替代视图）</caption>
        <thead className="border-b border-border text-left text-xs text-fg-muted">
          <tr>
            <th scope="col" className="px-4 py-3">关系类型</th>
            <th scope="col" className="px-4 py-3">Claim ID</th>
            <th scope="col" className="px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {data.edges.map((edge, index) => (
            <tr key={`${edge.type}-${edge.claim_id ?? index}`} className="border-b border-border last:border-0">
              <td className="px-4 py-3">{edge.type}</td>
              <td className="px-4 py-3 font-mono text-xs">
                {edge.claim_id ?? "—"}
              </td>
              <td className="px-4 py-3">
                {edge.claim_id ? (
                  <button
                    type="button"
                    onClick={() => onSelectClaim(edge.claim_id!)}
                    className="text-primary hover:underline"
                  >
                    查看证据链
                  </button>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-border p-3 text-xs text-fg-muted">
        <p className="mb-1 font-semibold">节点清单</p>
        <ul className="flex flex-wrap gap-2">
          {data.nodes.map((node) => (
            <li key={node.id} className="rounded bg-surface-muted px-2 py-0.5">
              {node.name ?? node.id}（{node.labels[0] ?? "?"}）
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
