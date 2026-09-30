import { describe, expect, it } from "vitest";
import { ApiClientError, apiFetch, toApiClientError } from "../lib/api/client";

function mockFetchOnce(payload: {
  ok?: boolean;
  status?: number;
  json?: unknown;
  headers?: Record<string, string>;
  reject?: Error;
}) {
  const impl = async (): Promise<Response> => {
    if (payload.reject) throw payload.reject;
    const body = payload.json ?? {};
    return {
      ok: payload.ok ?? true,
      status: payload.status ?? 200,
      headers: new Headers(payload.headers ?? {}),
      json: async () => body,
    } as Response;
  };
  vi.stubGlobal("fetch", impl);
}

describe("apiFetch 错误分类", () => {
  it("网络失败 → network", async () => {
    mockFetchOnce({ reject: new TypeError("Failed to fetch") });
    await expect(apiFetch("/x")).rejects.toMatchObject({ kind: "network" });
  });

  it("401/403 → permission 且携带 trace_id", async () => {
    mockFetchOnce({
      ok: false,
      status: 403,
      headers: { "X-Trace-Id": "trace-abc" },
      json: { detail: "forbidden" },
    });
    const error = (await apiFetch("/x").catch(
      (e: unknown) => e as ApiClientError,
    )) as ApiClientError;
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error.kind).toBe("permission");
    expect(error.traceId).toBe("trace-abc");
  });

  it("422 → validation 且携带受控 detail", async () => {
    mockFetchOnce({
      ok: false,
      status: 422,
      json: { detail: "metric 'X' not in allowed catalog", trace_id: "t-1" },
    });
    const error = (await apiFetch("/x").catch(
      (e: unknown) => e as ApiClientError,
    )) as ApiClientError;
    expect(error.kind).toBe("validation");
    expect(error.detail).toContain("not in allowed catalog");
    expect(error.traceId).toBe("t-1");
  });

  it("500 → server", async () => {
    mockFetchOnce({ ok: false, status: 500, json: {} });
    await expect(apiFetch("/x")).rejects.toMatchObject({ kind: "server" });
  });

  it("超时 → timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      (_path: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const promise = apiFetch("/x", { timeoutMs: 5 });
    const assertion = expect(promise).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(10);
    await assertion;
    vi.useRealTimers();
  });

  it("外部取消 → network('请求已取消') 而非 timeout", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      (_path: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const promise = apiFetch("/x", { signal: controller.signal });
    const assertion = expect(promise).rejects.toMatchObject({
      kind: "network",
      message: "请求已取消",
    });
    controller.abort();
    await assertion;
    vi.useRealTimers();
  });

  it("toApiClientError 归一化未知错误", () => {
    const normalized = toApiClientError(new Error("boom"));
    expect(normalized).toBeInstanceOf(ApiClientError);
    expect(normalized.kind).toBe("unknown");
  });

  it("成功响应返回 JSON 并透传 trace 头", async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      headers: { "X-Trace-Id": "ok-1" },
      json: { status: "OK" },
    });
    await expect(apiFetch("/x")).resolves.toEqual({ status: "OK" });
  });

  it("429 → rate_limit", async () => {
    mockFetchOnce({ ok: false, status: 429, json: {} });
    const error = (await apiFetch("/x").catch(
      (e: unknown) => e as ApiClientError,
    )) as ApiClientError;
    expect(error.kind).toBe("rate_limit");
    expect(error.message).toContain("频繁");
  });
});