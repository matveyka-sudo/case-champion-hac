import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import capabilities from "./fixtures/api/capabilities.json";
import result from "./fixtures/api/urban_passenger.json";

const json = (value: unknown) => new Response(JSON.stringify(value));
function interruptedBody(signal: AbortSignal) {
  return new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('{"analysis_id":'));
    signal.addEventListener("abort", () => controller.error(signal.reason), { once: true });
  } }));
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Deadline exceeded", "TimeoutError")), milliseconds);
    return controller.signal;
  });
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("HTTP response body interruption", () => {
  it("keeps the fifteen-second body deadline and retries a safe GET after the existing delay", async () => {
    let reads = 0;
    const client = new ApiClient(async (path, options) => String(path).endsWith("/capabilities") ? json(capabilities)
      : ++reads === 1 ? interruptedBody(options!.signal!) : json(result));
    const outcome = client.request("/analyses/saved", "AnalysisResult").then(value => ({ value, error: null }), error => ({ value: null, error }));
    await vi.advanceTimersByTimeAsync(14999);
    expect(reads).toBe(1);
    await vi.advanceTimersByTimeAsync(500);
    expect(reads).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect((await outcome).value).toEqual(result);
    expect(reads).toBe(2);
    expect(vi.mocked(AbortSignal.timeout).mock.calls.every(([milliseconds]) => milliseconds === 15000)).toBe(true);
  });

  it.each([undefined, "original-key"])("retries an interrupted POST only with its original idempotency key (%s)", async (key) => {
    const posts: RequestInit[] = [];
    const client = new ApiClient(async (path, options) => {
      if (String(path).endsWith("/capabilities")) return json(capabilities);
      posts.push(options!);
      return posts.length === 1 ? interruptedBody(options!.signal!) : json(result);
    });
    const outcome = client.request("/analyses", "AnalysisResult", { body: { existing: "request" }, key })
      .then(value => ({ value, error: null }), error => ({ value: null, error }));
    await vi.advanceTimersByTimeAsync(15500);
    const observed = await outcome;
    expect(posts).toHaveLength(key ? 2 : 1);
    if (key) {
      expect(observed.value).toEqual(result);
      expect(posts.map(item => (item.headers as Record<string, string>)["Idempotency-Key"])).toEqual([key, key]);
      expect(posts[1].body).toBe(posts[0].body);
    } else expect(observed.error).toMatchObject({ code: "network_error", retryable: true });
  });

  it("propagates caller cancellation during body reading without a retry", async () => {
    const controller = new AbortController(), reason = new DOMException("Cancelled locally", "AbortError");
    let reads = 0;
    const client = new ApiClient(async (path, options) => String(path).endsWith("/capabilities") ? json(capabilities)
      : (reads++, interruptedBody(options!.signal!)));
    const outcome = client.request("/analyses/saved", "AnalysisResult", { signal: controller.signal }).catch(error => error);
    await vi.advanceTimersByTimeAsync(1);
    controller.abort(reason);
    expect(await outcome).toBe(reason);
    expect(reads).toBe(1);
  });

  it("continues to classify malformed JSON as an invalid response without retrying a POST", async () => {
    let posts = 0;
    const client = new ApiClient(async path => String(path).endsWith("/capabilities") ? json(capabilities)
      : (posts++, new Response("private malformed response")));
    await expect(client.request("/analyses", "AnalysisResult", { body: {}, key: "original-key" })).rejects.toMatchObject({ code: "invalid_response", retryable: false });
    expect(posts).toBe(1);
  });
});
