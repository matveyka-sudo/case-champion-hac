import { decode } from "./validation";
import type { Schemas } from "./types";
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: Schemas["ErrorDetail"]["field_errors"];
  readonly requestId: string | null;
  readonly retryable: boolean;
  constructor(
    status: number,
    code: string,
    message: string,
    fields: Schemas["ErrorDetail"]["field_errors"] = [],
    requestId: string | null = null,
    retryable = false,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}
export function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
export class ApiClient {
  private bootstrap: Promise<Schemas["Capabilities"]> | undefined;
  private readonly fetcher: typeof fetch;
  constructor(fetcher: typeof fetch = fetch) {
    this.fetcher = fetcher;
  }
  capabilities() {
    this.bootstrap ??= this.send("/capabilities", "Capabilities").catch(
      (error) => {
        this.bootstrap = undefined;
        throw error;
      },
    );
    return this.bootstrap;
  }
  async request<K extends keyof Schemas>(
    path: string,
    schema: K,
    options: { signal?: AbortSignal; body?: unknown; key?: string } = {},
  ): Promise<Schemas[K]> {
    await this.capabilities();
    options.signal?.throwIfAborted();
    return this.send(path, schema, options);
  }
  async list<K extends keyof Schemas>(
    path: string,
    schema: K,
    signal?: AbortSignal,
  ): Promise<Schemas[K][]> {
    await this.capabilities();
    const data = await this.raw(path, { signal });
    if (!Array.isArray(data))
      throw new ApiError(0, "invalid_response", "Неверный ответ сервера");
    try {
      return data.map((item) => decode(schema, item));
    } catch {
      throw new ApiError(
        0,
        "invalid_response",
        "Ответ сервера не соответствует опубликованному контракту",
      );
    }
  }
  async territories(
    level: "city" | "region",
    query: string,
    signal?: AbortSignal,
  ) {
    return this.list(
      "/territories?" + new URLSearchParams({ level, query }),
      "TerritoryOption",
      signal,
    );
  }
  private async send<K extends keyof Schemas>(
    path: string,
    schema: K,
    options: { signal?: AbortSignal; body?: unknown; key?: string } = {},
  ): Promise<Schemas[K]> {
    const data = await this.raw(path, options);
    try {
      return decode(schema, data);
    } catch {
      throw new ApiError(
        0,
        "invalid_response",
        "Ответ сервера не соответствует опубликованному контракту",
      );
    }
  }
  private async raw(
    path: string,
    options: { signal?: AbortSignal; body?: unknown; key?: string } = {},
  ): Promise<unknown> {
    const body =
      options.body === undefined ? undefined : JSON.stringify(options.body);
    const retryAllowed = body === undefined || !!options.key;
    for (let attempt = 0; ; attempt++) {
      options.signal?.throwIfAborted();
      const signal = AbortSignal.any([
        ...(options.signal ? [options.signal] : []),
        AbortSignal.timeout(15000),
      ]);
      let retryAfter = 0;
      let failure: ApiError;
      try {
        const fetcher = this.fetcher;
        const response = await fetcher("/api/v1" + path, {
          method: body === undefined ? "GET" : "POST",
          credentials: "same-origin",
          signal,
          headers: {
            Accept: "application/json",
            ...(body ? { "Content-Type": "application/json" } : {}),
            ...(options.key ? { "Idempotency-Key": options.key } : {}),
          },
          body,
        });
        const text = response.headers.get("Retry-After");
        if (text) {
          const seconds = Number(text);
          retryAfter = Number.isFinite(seconds)
            ? seconds * 1000
            : Math.max(0, Date.parse(text) - Date.now());
        }
        const value: unknown = await response.json().catch(() => {
          signal.throwIfAborted();
          return null;
        });
        if (response.ok) return value;
        let error: Schemas["ErrorDetail"] | null = null;
        try {
          error = decode("ErrorEnvelope", value).error;
        } catch {
          /* Do not expose raw provider errors. */
        }
        failure = new ApiError(
          response.status,
          error?.code || "http_error",
          error?.message || "Сервис временно недоступен",
          error?.field_errors || [],
          error?.request_id || response.headers.get("X-Request-ID"),
          !!error?.retryable,
        );
      } catch (error) {
        if (options.signal?.aborted) throw options.signal.reason;
        failure =
          error instanceof ApiError
            ? error
            : new ApiError(
                0,
                "network_error",
                "Не удалось связаться с сервером",
                [],
                null,
                true,
              );
      }
      if (failure.code === "unknown_id" && failure.message === "Unknown immutable snapshot")
        this.bootstrap = undefined;
      if (
        !retryAllowed ||
        attempt >= 2 ||
        !failure.retryable ||
        ![0, 429, 503].includes(failure.status)
      )
        throw failure;
      const delay = Math.max(
        500 * 2 ** attempt,
        Number.isFinite(retryAfter) ? retryAfter : 0,
      );
      if (delay > 15000) throw failure;
      await pause(delay, options.signal || new AbortController().signal);
    }
  }
}
