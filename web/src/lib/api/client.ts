/**
 * 统一 API Client：同源 fetch + 分类错误 + trace_id + 超时/取消。
 *
 * 错误分类（product-ui.md §4 状态渲染规范的依据）：
 * - network    网络不可达 / DNS 失败
 * - timeout    超过 timeout_ms（默认 30s，可被调用方取消）
 * - permission 401/403
 * - validation 422（受控拒绝，如计划校验失败）
 * - not_found  404
 * - server     其他 5xx
 * - unknown    其余
 *
 * 所有错误携带 trace_id（响应头 X-Trace-Id，或错误体 trace_id），
 * 供 UI 展示"可上报的追踪标识"；绝不透出内部 SQL/Cypher/堆栈。
 */

export type ApiErrorKind =
  | "network"
  | "timeout"
  | "permission"
  | "validation"
  | "not_found"
  | "server"
  | "unknown";

export class ApiClientError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly traceId: string | null;
  /** 后端受控 detail（如 422 的计划校验原因）；不保证存在 */
  readonly detail: string | null;

  constructor(
    kind: ApiErrorKind,
    message: string,
    opts: { status?: number; traceId?: string | null; detail?: string | null } = {},
  ) {
    super(message);
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = opts.status;
    this.traceId = opts.traceId ?? null;
    this.detail = opts.detail ?? null;
  }
}

const DEFAULT_TIMEOUT_MS = 30_000;

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

function statusToKind(status: number): ApiErrorKind {
  if (status === 401 || status === 403) return "permission";
  if (status === 404) return "not_found";
  if (status === 422) return "validation";
  if (status >= 500) return "server";
  return "unknown";
}

function kindMessage(kind: ApiErrorKind): string {
  switch (kind) {
    case "network":
      return "网络不可达，请检查服务是否启动";
    case "timeout":
      return "请求超时，请稍后重试";
    case "permission":
      return "无权限访问该资源";
    case "validation":
      return "请求未通过校验";
    case "not_found":
      return "资源不存在";
    case "server":
      return "服务端错误，请稍后重试";
    default:
      return "请求失败";
  }
}

/** 全局错误到 ApiClientError 的归一化（供 QueryClient 默认 onError 使用） */
export function toApiClientError(error: unknown): ApiClientError {
  if (error instanceof ApiClientError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiClientError("timeout", kindMessage("timeout"));
  }
  return new ApiClientError("unknown", kindMessage("unknown"));
}

export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } =
    options;

  // 外部 signal（TanStack Query 取消）与超时合并
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onExternalAbort = () => controller.abort();
  signal?.addEventListener("abort", onExternalAbort, { once: true });

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      // 外部取消与超时共用 AbortError：以是否触发定时器区分语义
      throw signal?.aborted
        ? new ApiClientError("network", "请求已取消")
        : new ApiClientError("timeout", kindMessage("timeout"));
    }
    throw new ApiClientError("network", kindMessage("network"));
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }

  const traceId = response.headers.get("X-Trace-Id");

  if (!response.ok) {
    let detail: string | null = null;
    let bodyTraceId: string | null = null;
    try {
      const payload = (await response.json()) as {
        detail?: unknown;
        trace_id?: string;
      };
      if (typeof payload.detail === "string") detail = payload.detail;
      if (typeof payload.trace_id === "string") bodyTraceId = payload.trace_id;
    } catch {
      /* 非 JSON 错误体：仅用状态码分类 */
    }
    const kind = statusToKind(response.status);
    throw new ApiClientError(kind, kindMessage(kind), {
      status: response.status,
      traceId: traceId ?? bodyTraceId,
      detail,
    });
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
