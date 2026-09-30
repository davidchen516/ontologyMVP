import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api/endpoints";
import type { QueryResponse, ScreenRequest } from "../lib/api/types";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";

/**
 * 查询工作台（issue #31 核心路径）：受控结构化表单 → /api/v1/screen →
 * Grounded 结果分区展示（results/excluded/unknowns/conflicts/
 * degradation_notes/period_rule/trace_id 全部可见——Epic #29 不变量：
 * 不能只展示命中结果）。
 *
 * 自然语言入口故意不存在：Planner 未接入（issue #31 范围明确——
 * 不伪装成已可用的聊天查询）。
 */
const STAGES = ["MASS_PRODUCTION", "SMALL_BATCH", "SAMPLE_VALIDATION",
  "PROTOTYPE", "RESEARCH", "TECHNOLOGY_RESERVE"] as const;
const OPERATORS = ["TOTAL_POSITIVE", "CONSECUTIVE_POSITIVE",
  "MIN_VALUE", "MAX_VALUE"] as const;

function Section({
  title, items, tone,
}: { title: string; items: string[]; tone: "neutral" | "warn" | "danger" }) {
  if (items.length === 0) return null;
  const toneClass =
    tone === "warn" ? "border-warning-muted bg-warning-muted"
    : tone === "danger" ? "border-danger-muted bg-danger-muted"
    : "border-border bg-surface-muted";
  return (
    <section aria-label={title} className={`rounded-card border p-4 text-sm ${toneClass}`}>
      <h3 className="mb-2 font-semibold">{title}（{items.length}）</h3>
      <ul className="space-y-1">
        {items.map((item) => (
          <li key={item} className="text-fg-muted">{item}</li>
        ))}
      </ul>
    </section>
  );
}

function GroundedResultCard({ result }: { result: QueryResponse["results"][number] }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className="rounded-card border border-border bg-surface p-4 shadow-card">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="font-medium">{result.company_name}</span>
        <span className="flex items-center gap-2 text-xs text-fg-muted">
          {result.security_code ? <span>{result.security_code}</span> : null}
          {result.standard_product ? <span>{result.standard_product}</span> : null}
          <span>{result.business_stage ?? "—"}</span>
          <span aria-hidden>{expanded ? "−" : "+"}</span>
        </span>
      </button>

      <div className="mt-2 flex flex-wrap gap-4 text-sm">
        {result.financial_value !== null ? (
          <span>
            三年现金流合计：
            <strong className="ml-1">
              {result.financial_value} {result.currency ?? ""}
            </strong>
            <span className="ml-1 text-xs text-fg-muted">
              （{result.report_period}）
            </span>
          </span>
        ) : null}
      </div>

      {expanded ? (
        <div className="mt-3 space-y-3 border-t border-border pt-3 text-sm">
          <div>
            <h4 className="text-xs font-semibold text-fg-muted">Claim ID</h4>
            <p className="font-mono text-xs">{result.claim_ids.join("， ")}</p>
          </div>
          <div>
            <h4 className="text-xs font-semibold text-fg-muted">证据原文（可定位）</h4>
            <ul className="space-y-1">
              {result.evidence_quotes.slice(0, 3).map((quote) => (
                <li key={quote.evidence_id} className="text-fg-muted">
                  第{quote.page_number ?? "?"}页：“{quote.quote_text.slice(0, 80)}…”
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="text-xs font-semibold text-fg-muted">推理路径</h4>
            <ol className="list-inside list-decimal space-y-0.5 text-xs text-fg-muted">
              {result.reasoning_path.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
          <div>
            <h4 className="text-xs font-semibold text-fg-muted">财务明细（各财年）</h4>
            <ul className="text-xs">
              {result.financial_detail.map((d) => (
                <li key={d.period_end}>
                  {d.period_end}：{d.value}
                </li>
              ))}
            </ul>
          </div>
          <div className="text-xs text-fg-muted">
            有效期：{result.valid_from ?? "开放式"} ~ {result.valid_to ?? "至今"} ·
            数据新鲜度：{result.data_freshness ?? "—"}
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function QueryWorkbench() {
  const [form, setForm] = useState<ScreenRequest>({
    business_stage: "MASS_PRODUCTION",
    evidence_required: true,
    max_results: 50,
  });
  const [validationError, setValidationError] = useState<string | null>(null);

  const mutation = useMutation<QueryResponse, unknown, ScreenRequest>({
    mutationFn: (body: ScreenRequest) => api.screen(body),
  });

  const needsThreshold =
    form.operator === "MIN_VALUE" || form.operator === "MAX_VALUE";

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setValidationError(null);
    if (needsThreshold && (form.threshold === null || form.threshold === undefined)) {
      setValidationError("该算子需要阈值（threshold）");
      return;
    }
    // 提交体只含受控字段；空概念/无财务时不发送该字段
    const body: ScreenRequest = { ...form };
    if (!body.concept_name) delete body.concept_name;
    if (!body.metric_code) delete body.metric_code;
    if (!needsThreshold) delete body.threshold;
    mutation.mutate(body);
  };

  const data = mutation.data;

  return (
    <section aria-label="查询工作台" className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold">查询工作台</h1>
        <p className="text-sm text-fg-muted">
          受控结构化筛选：语义条件 × 财务口径 × 双时态。所有结果携带
          Claim、可定位证据与推理路径。
        </p>
      </header>

      <form
        onSubmit={submit}
        className="grid gap-4 rounded-card border border-border bg-surface p-4 shadow-card md:grid-cols-2"
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">业务阶段</span>
          <select
            value={form.business_stage ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, business_stage: e.target.value || undefined }))
            }
            className="w-full rounded-md border border-border bg-bg px-3 py-2"
          >
            <option value="">不限</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium">概念（平台分类标签）</span>
          <input
            type="text"
            value={form.concept_name ?? ""}
            maxLength={200}
            placeholder="如：人形机器人（可选）"
            onChange={(e) =>
              setForm((f) => ({ ...f, concept_name: e.target.value || undefined }))
            }
            className="w-full rounded-md border border-border bg-bg px-3 py-2"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium">财务指标</span>
          <select
            value={form.metric_code ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, metric_code: e.target.value || undefined }))
            }
            className="w-full rounded-md border border-border bg-bg px-3 py-2"
          >
            <option value="">不限</option>
            <option value="NET_CF_OPERATING">经营活动现金流净额</option>
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium">口径（算子）</span>
          <select
            value={form.operator ?? "TOTAL_POSITIVE"}
            onChange={(e) =>
              setForm((f) => ({ ...f, operator: e.target.value as ScreenRequest["operator"] }))
            }
            className="w-full rounded-md border border-border bg-bg px-3 py-2"
          >
            {OPERATORS.map((op) => (
              <option key={op} value={op}>{op}</option>
            ))}
          </select>
        </label>

        {needsThreshold ? (
          <label className="text-sm">
            <span className="mb-1 block font-medium">阈值</span>
            <input
              type="number"
              step="any"
              value={form.threshold ?? ""}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  threshold: e.target.value === "" ? undefined : Number(e.target.value),
                }))
              }
              className="w-full rounded-md border border-border bg-bg px-3 py-2"
            />
          </label>
        ) : null}

        <label className="text-sm">
          <span className="mb-1 block font-medium">财年窗口</span>
          <select
            value={form.fiscal_years ?? 3}
            onChange={(e) =>
              setForm((f) => ({ ...f, fiscal_years: Number(e.target.value) as 3 | 5 }))
            }
            className="w-full rounded-md border border-border bg-bg px-3 py-2"
          >
            <option value={3}>最近 3 个完整财年</option>
            <option value={5}>最近 5 个完整财年</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.evidence_required ?? true}
            onChange={(e) =>
              setForm((f) => ({ ...f, evidence_required: e.target.checked }))
            }
          />
          <span>仅返回有证据的结果（evidence_required）</span>
        </label>

        {validationError ? (
          <p role="alert" className="text-sm text-danger md:col-span-2">
            {validationError}
          </p>
        ) : null}

        <div className="md:col-span-2">
          <button
            type="submit"
            disabled={mutation.isPending}
            aria-busy={mutation.isPending}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg disabled:opacity-50"
          >
            {mutation.isPending ? "查询中…" : "执行筛选"}
          </button>
        </div>
      </form>

      {mutation.isPending ? <LoadingSkeleton rows={4} /> : null}

      {mutation.error ? (
        <ErrorState
          error={mutation.error}
          onRetry={() => mutation.mutate(mutation.variables!)}
        />
      ) : null}

      {data ? (
        <div className="space-y-4">
          <div
            className="flex flex-wrap items-center gap-3 rounded-card border border-border bg-surface p-3 text-sm"
          >
            <span>状态：
              <strong className={
                data.status === "SUCCEEDED" ? "text-success"
                : data.status === "DEGRADED" ? "text-warning" : "text-danger"
              }>{data.status}</strong>
            </span>
            {data.period_rule ? (
              <span>口径：<code>{data.period_rule}</code></span>
            ) : null}
            {data.trace_id ? (
              <span className="font-mono text-xs text-fg-muted">
                trace_id：{data.trace_id}
              </span>
            ) : null}
          </div>

          {data.degradation_notes.length > 0 ? (
            <Section title="降级说明" items={data.degradation_notes} tone="warn" />
          ) : null}

          {data.results.length === 0 ? (
            <EmptyState
              title="无命中结果"
              hint="筛选条件可能过严，或候选公司均被排除/证据不足——查看下方排除与未知说明。"
            />
          ) : (
            <div className="space-y-3">
              {data.results.map((r) => (
                <GroundedResultCard key={r.company_id} result={r} />
              ))}
            </div>
          )}

          <Section title="被排除候选" items={data.excluded} tone="neutral" />
          <Section title="证据不足（未知）" items={data.unknowns} tone="warn" />
          <Section title="冲突警告" items={data.conflicts} tone="danger" />
        </div>
      ) : null}
    </section>
  );
}
