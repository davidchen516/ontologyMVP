import { AlertTriangle, ArrowLeft, RefreshCw, WifiOff } from "lucide-react";
import { Link } from "react-router";
import { toApiClientError } from "../../lib/api/client";

/** 分类错误态（product-ui.md §4）：可重试 + trace_id，不泄露内部细节。 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = toApiClientError(error);
  const isPermission = apiError.kind === "permission";
  const Icon = apiError.kind === "network" || apiError.kind === "timeout" ? WifiOff : AlertTriangle;

  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-card border border-border bg-surface p-6 shadow-card"
    >
      <div className="flex items-center gap-2">
        <Icon aria-hidden className={`size-5 ${isPermission ? "text-warning" : "text-danger"}`} />
        <h2 className="text-sm font-semibold">
          {isPermission ? "无权限" : "请求失败"}
        </h2>
      </div>
      <p className="text-sm text-fg-muted">{apiError.message}</p>
      {apiError.kind === "validation" && apiError.detail ? (
        <p className="text-sm text-fg-muted">
          原因：{typeof apiError.detail === "string"
            ? apiError.detail
            : JSON.stringify(apiError.detail)}
        </p>
      ) : null}
      {apiError.traceId ? (
        <p className="font-mono text-xs text-fg-muted">
          trace_id：{apiError.traceId}
        </p>
      ) : null}
      <div className="flex gap-2">
        {onRetry && !isPermission ? (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg"
            onClick={onRetry}
          >
            <RefreshCw aria-hidden className="size-3.5" /> 重试
          </button>
        ) : null}
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm"
        >
          <ArrowLeft aria-hidden className="size-3.5" /> 返回首页
        </Link>
      </div>
    </div>
  );
}
