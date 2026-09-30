import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { graphApi } from "../lib/api/endpoints";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";

/**
 * 证据浏览器（issue #32）：文档 → 证据片段（页码/原文/字符区间）。
 * 按文档浏览全部可定位证据片段（页码/字符区间/原文）。
 */
export function EvidenceBrowser() {
  const [documentId, setDocumentId] = useState("");
  const [submittedId, setSubmittedId] = useState("");

  const evidence = useQuery({
    queryKey: ["document-evidence", submittedId],
    queryFn: (context) =>
      graphApi.documentEvidence(submittedId, 50, context.signal),
    enabled: Boolean(submittedId),
  });

  return (
    <section aria-label="证据浏览器" className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">证据浏览器</h1>
        <p className="text-sm text-fg-muted">
          文档 → 可定位证据片段（页码/字符区间）→ Claim 追溯。
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSubmittedId(documentId);
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={documentId}
          onChange={(e) => setDocumentId(e.target.value)}
          placeholder="输入文档 ID（可从公司详情 Claim 的证据链接获取）"
          aria-label="文档 ID"
          maxLength={64}
          className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm"
        />
        <button
          type="submit"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg"
        >
          查看证据
        </button>
      </form>

      {evidence.isPending ? <LoadingSkeleton rows={4} /> : null}
      {evidence.error ? (
        <ErrorState error={evidence.error} onRetry={() => void evidence.refetch()} />
      ) : null}

      {evidence.data ? (
        <div className="space-y-3">
          <div className="rounded-card border border-border bg-surface p-4 text-sm">
            <p className="font-medium">{evidence.data.document.title}</p>
            <p className="mt-1 text-xs text-fg-muted">
              {evidence.data.document.source_system} ·
              {evidence.data.document.document_type} ·
              版本 {evidence.data.document.version ?? "无"} ·
              解析 {evidence.data.document.parse_status ?? "未知"}
            </p>
          </div>

          {evidence.data.count === 0 ? (
            <EmptyState
              title="该文档暂无证据片段"
              hint="解析完成后片段会出现在这里。"
            />
          ) : (
            <ul className="space-y-2">
              {evidence.data.fragments.map((fragment) => (
                <li
                  key={fragment.id}
                  className="rounded-card border border-border bg-surface p-3 text-sm"
                >
                  <p className="text-xs text-fg-muted">
                    第 {fragment.page_number ?? "?"} 页 ·
                    字符 {fragment.char_start ?? "?"}–{fragment.char_end ?? "?"}
                  </p>
                  <blockquote className="mt-2 border-l-2 border-primary pl-3">
                    “{fragment.quote_text}”
                  </blockquote>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

    </section>
  );
}
