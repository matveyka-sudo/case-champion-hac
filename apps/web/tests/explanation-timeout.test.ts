import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, REQUEST_TIMEOUT_MS } from "../src/api/client";
import { BackendGateway, EXPLANATION_TIMEOUT_MS } from "../src/api/gateway";
import capabilities from "./fixtures/api/capabilities.json";

const json = (value: unknown) => new Response(JSON.stringify(value));
afterEach(() => vi.restoreAllMocks());

it("waits for the backend explanation budget instead of the default deadline", async () => {
  const timeouts = vi.spyOn(AbortSignal, "timeout");
  const fetcher = (async (path: RequestInfo | URL) =>
    String(path).endsWith("/capabilities") ? json(capabilities) : json({})) as typeof fetch;
  const gateway = new BackendGateway(new ApiClient(fetcher));
  await gateway.explain("saved", { schema_version: "2", question: "Почему?" }).catch(() => undefined);
  const used = timeouts.mock.calls.map(([milliseconds]) => milliseconds);
  expect(EXPLANATION_TIMEOUT_MS).toBeGreaterThan(90000);
  expect(used).toEqual([REQUEST_TIMEOUT_MS, EXPLANATION_TIMEOUT_MS]);
});
