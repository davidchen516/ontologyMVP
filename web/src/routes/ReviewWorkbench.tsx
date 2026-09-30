import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api/client";
import { ErrorState } from "../components/states/ErrorState";
import { EmptyState, LoadingSkeleton } from "../components/states/States";
import { ApiClientError } from "../lib/api/client";

/**
 * Claim 审核工作台（issue #33 / ADR-0006）。
 * - Reviewer Key 输入（内存态——绝不入 localStorage/URL）；
 * - 队列任务 + Claim 摘要 + 证据并排；
 * - 接受/拒绝必须填理由；提交带 Idempotency-Key（客户端 UUID）；
 * - 409 并发冲突 → 显示当前状态差异 + 刷新；
 * - 服务端开关关闭 → 引导只读路径（登录入口不渲染——能力感知）。
 */

interface ReviewTask {
  task_id: string;
  task_status: string;
  priority: string;
  reason_codes: string[] | null;
  claim_id: string;
  predicate_code: string;
  claim_status: string;
  business_stage: string | null;
  evidence_state: string | null;
  confidence: number | null;
  subject_entity_id: string;
  object_value: unknown;
  evidence_count: number;
}

export function ReviewWorkbench() {
  const [reviewerKey, setReviewerKey] = useState("");
  const [authedKey, setAuthedKey] = useState<string | null>(null);
  const [decisionFor, setDecisionFor] = useState<ReviewTask | null>(null);
  const [decision, setDecision] = useState<"ACCEPTED" | "REJECTED">("ACCEPTED");
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);

  const authHeaders = authedKey
    ? { "X-Reviewer-Key": authedKey }
    : undefined;

  const queue = useQuery({
    queryKey: ["review-queue", authedKey !== null],
    queryFn: async (ctx) => {
      const response = await fetch("/api/v1/review/queue", {
        headers: authHeaders ?? {},
        signal: ctx.signal,
      });
      if (response.status === 503) {
        throw new ApiClientError("permission", "审核写功能未启用（服务端开关关闭）");
      }
      if (!response.ok) {
        throw new ApiClientError(
          response.status === 401 ? "permission" : "server",
          "队列加载失败",
          { status: response.status },
        );
      }
      return (await response.json()) as {
        tasks: ReviewTask[]; total: number; write_available: boolean;
      };
    },
    enabled: authedKey !== null,
    refetchInterval: 15_000,
  });

  const submit = useMutation({
    mutationFn: async ({
      task, decision, reason,
    }: { task: ReviewTask; decision: string; reason: string }) => {
      const idem = crypto.randomUUID();
      return apiFetch<Record<string, unknown>>(
        `/api/v1/review/tasks/${task.task_id}/decision`,
        {
          method: "POST",
          body: { decision, reason },
          headers: {
            ...authHeaders,
            "Idempotency-Key": idem,
          },
        },
      );
    },
    onSuccess: () => {
      setDecisionFor(null);
      setReason("");
      setConflict(null);
      void queue.refetch();
    },
    onError: (error) => {
      if (error instanceof ApiClientError && error.status === 409) {
        // 后端 409 detail 可能是对象（并发冲突+当前状态）或字符串
        // （状态机/证据拒绝——具体可修复原因）——都如实呈现
        const detail = error.detail;
        if (typeof detail === "string") {
          setConflict(`决定被拒绝：${detail}`);
        } else if (detail && typeof detail === "object") {
          const d = detail as { message?: string; current_claim_status?: string };
          setConflict(
            `并发冲突：${d.message ?? ""}（当前状态：${d.current_claim_status ?? "未知"}）——请刷新查看最新状态。`,
          );
        } else {
          setConflict("该任务已被其他审核者处理（并发冲突）——请刷新查看最新状态。");
        }
        void queue.refetch();
      }
    },
  });

  // 登录入口只在能力可用时渲染（能力感知：readyz.capabilities.review_write）
  const readiness = useQuery({
    queryKey: ["readyz-review"],
    queryFn: (ctx) => apiFetch<{ capabilities: Record<string, { status: string }> }>("/readyz", { signal: ctx.signal }),
  });
  const writeCapability =
    readiness.data?.capabilities?.["review_write"]?.status === "OK";

  if (authedKey === null) {
    return (
      <section aria-label="Claim 审核" className="space-y-4">
        <header>
          <h1 className="text-lg font-semibold">Claim 审核</h1>
        </header>
        {!writeCapability ? (
          <EmptyState
            title="审核功能未启用"
            hint="服务端未配置审核写能力（开关关闭或缺 Reviewer 密钥哈希）。只读查询与 CLI 审核路径仍可用。"
          />
        ) : (
          <form
            className="max-w-md space-y-3 rounded-card border border-border bg-surface p-4 shadow-card"
            onSubmit={(e) => {
              e.preventDefault();
              if (reviewerKey) setAuthedKey(reviewerKey);
            }}
          >
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Reviewer Key</span>
              <input
                type="password"
                value={reviewerKey}
                onChange={(e) => setReviewerKey(e.target.value)}
                autoComplete="off"
                className="w-full rounded-md border border-border bg-bg px-3 py-2"
                aria-label="Reviewer Key"
              />
              <span className="mt-1 block text-xs text-fg-muted">
                密钥仅存内存态——不入 localStorage/URL/日志。
              </span>
            </label>
            <button
              type="submit"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg"
            >
              进入审核工作台
            </button>
          </form>
        )}
      </section>
    );
  }

  return (
    <section aria-label="Claim 审核工作台" className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">审核队列（{queue.data?.total ?? "…"}）</h1>
        <button
          type="button"
          className="text-sm text-fg-muted hover:text-fg"
          onClick={() => setAuthedKey(null)}
        >
          退出
        </button>
      </header>

      {conflict ? (
        <p role="alert" className="rounded-card border border-warning-muted bg-warning-muted p-3 text-sm">
          {conflict}
        </p>
      ) : null}

      {queue.error ? (
        <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
      ) : null}
      {queue.isPending ? <LoadingSkeleton rows={4} /> : null}

      {queue.data && queue.data.tasks.length === 0 ? (
        <EmptyState title="队列为空" hint="当前没有待审任务。" />
      ) : null}

      <ul className="space-y-3">
        {queue.data?.tasks.map((task) => (
          <li
            key={task.task_id}
            className="rounded-card border border-border bg-surface p-4 shadow-card"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="text-sm">
                <span className="font-medium">{task.predicate_code}</span>
                <span className="ml-2 text-fg-muted">
                  {task.business_stage ?? "—"} · 置信度 {task.confidence ?? "—"} ·
                  证据 {task.evidence_count} 条
                </span>
              </div>
              <span className="text-xs text-fg-muted">
                优先级 {task.priority}
                {task.reason_codes?.length
                  ? ` · 原因：${task.reason_codes.join("、")}`
                  : ""}
              </span>
            </div>

            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg"
                onClick={() => { setDecisionFor(task); setDecision("ACCEPTED"); setConflict(null); }}
              >
                接受
              </button>
              <button
                type="button"
                className="rounded-md border border-danger px-3 py-1.5 text-sm font-medium text-danger"
                onClick={() => { setDecisionFor(task); setDecision("REJECTED"); setConflict(null); }}
              >
                拒绝
              </button>
            </div>
          </li>
        ))}
      </ul>

      {decisionFor ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="审核决定"
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
        >
          <div className="absolute inset-0 bg-black/40" />
          <form
            className="relative z-10 w-full max-w-md space-y-4 rounded-card bg-surface p-6 shadow-lg"
            onSubmit={(e) => {
              e.preventDefault();
              if (reason.trim()) {
                submit.mutate({ task: decisionFor, decision, reason });
              }
            }}
          >
            <h2 className="text-base font-semibold">
              {decision === "ACCEPTED" ? "接受" : "拒绝"} Claim（{decisionFor.predicate_code}）
            </h2>
            <p className="text-xs text-fg-muted">
              状态机后果：{decision === "ACCEPTED"
                ? "NEEDS_REVIEW → ACCEPTED（Evidence/Provenance/审计/Outbox 同事务）"
                : "NEEDS_REVIEW → REJECTED（历史保留不物理删除）"}
            </p>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">理由（必填）</span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={2000}
                required
                rows={3}
                className="w-full rounded-md border border-border bg-bg px-3 py-2"
                aria-label="决定理由"
              />
            </label>
            {submit.error && !(submit.error instanceof ApiClientError && submit.error.status === 409) ? (
              <p role="alert" className="text-sm text-danger">{String(submit.error)}</p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded-md border border-border px-3 py-1.5 text-sm"
                onClick={() => setDecisionFor(null)}
              >
                取消
              </button>
              <button
                type="submit"
                disabled={submit.isPending || !reason.trim()}
                aria-busy={submit.isPending}
                className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-fg disabled:opacity-50"
              >
                {submit.isPending ? "提交中…" : "确认提交"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
