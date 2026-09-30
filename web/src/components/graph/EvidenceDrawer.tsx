import { useQuery } from "@tanstack/react-query";
import { graphApi } from "../../lib/api/endpoints";
import { ErrorState } from "../states/ErrorState";
import { LoadingSkeleton } from "../states/States";

/**
 * 证据抽屉（issue #32）：claim_id → Claim lineage → Evidence（页码/原文/
 * 字符区间）→ Document（来源/版本/状态）。
 * GWT：文档缺失或版本变化 → 显示缺失/版本状态，不回退到不受信任文本。
 */
export function EvidenceDrawer({
  claimId, onClose,
}: { claimId: string; onClose: () => void }) {
  const lineage = useQuery({
    queryKey: ["claim-lineage", claimId],
    queryFn: (context) => graphApi.claimLineage(claimId, context.signal),
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="证据追溯链"
      className="fixed inset-0 z-50 flex justify-end"
    >
      <button
        type="button"
        aria-label="关闭证据抽屉"
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
      />
      <div className="relative z-10 flex h-full w-full max-w-lg flex-col overflow-y-auto bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">证据追溯链</h2>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="rounded-md p-2 hover:bg-surface-muted"
          >
            ✕
          </button>
        </div>

        {lineage.isPending ? <LoadingSkeleton rows={5} /> : null}
        {lineage.error ? (
          <ErrorState error={lineage.error} onRetry={() => void lineage.refetch()} />
        ) : null}

        {lineage.data ? (
          <div className="space-y-5 text-sm">
            <section aria-label="Claim">
              <h3 className="mb-2 text-xs font-semibold text-fg-muted">Claim</h3>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                <dt className="text-fg-muted">谓词</dt>
                <dd className="font-mono text-xs">{lineage.data.claim.predicate_code}</dd>
                <dt className="text-fg-muted">状态</dt>
                <dd>{lineage.data.claim.claim_status}</dd>
                <dt className="text-fg-muted">业务阶段</dt>
                <dd>{lineage.data.claim.business_stage ?? "—"}</dd>
                <dt className="text-fg-muted">置信度</dt>
                <dd>{lineage.data.claim.confidence ?? "—"}</dd>
                <dt className="text-fg-muted">有效期</dt>
                <dd className="text-xs">
                  {lineage.data.claim.valid_from ?? "开放"} ~{" "}
                  {lineage.data.claim.valid_to ?? "至今"}
                </dd>
              </dl>
            </section>

            <section aria-label="证据片段">
              <h3 className="mb-2 text-xs font-semibold text-fg-muted">
                证据片段（{lineage.data.evidence.length}）
              </h3>
              {lineage.data.evidence.length === 0 ? (
                <p className="rounded-card border border-warning-muted bg-warning-muted p-3">
                  该 Claim 无关联证据片段（状态如实呈现——不回退到不受信任文本）。
                </p>
              ) : (
                <ul className="space-y-3">
                  {lineage.data.evidence.map((fragment) => (
                    <li
                      key={fragment.id}
                      className="rounded-card border border-border bg-bg p-3"
                    >
                      <p className="text-xs text-fg-muted">
                        第 {fragment.page_number ?? "?"} 页 ·
                        字符 {fragment.char_start ?? "?"}–{fragment.char_end ?? "?"} ·
                        版本 {fragment.document_version_id ? "已关联" : "未关联"}
                      </p>
                      <blockquote className="mt-2 border-l-2 border-primary pl-3">
                        “{fragment.quote_text}”
                      </blockquote>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-label="来源文档">
              <h3 className="mb-2 text-xs font-semibold text-fg-muted">来源文档</h3>
              {lineage.data.documents.map((doc, index) =>
                doc === null ? (
                  <p
                    key={`missing-${index}`}
                    className="rounded-card border border-warning-muted bg-warning-muted p-3"
                  >
                    文档记录缺失（如实呈现缺失状态）。
                  </p>
                ) : (
                  <div
                    key={doc.id}
                    className="rounded-card border border-border bg-bg p-3"
                  >
                    <p className="font-medium">{doc.title}</p>
                    <p className="mt-1 text-xs text-fg-muted">
                      {doc.source_system} · {doc.document_type} ·
                      版本 {doc.version ?? "无"} ·
                      解析状态 {doc.parse_status ?? "未知"}
                    </p>
                  </div>
                ),
              )}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
