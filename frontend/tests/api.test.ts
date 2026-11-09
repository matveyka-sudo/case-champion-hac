import { describe, it, expect, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";
import { BackendGateway } from "../src/api/gateway";
import { decode } from "../src/api/validation";
import {
  departureAt,
  initialDraft,
  toRequest,
} from "../src/api/adapters/request";
import { measureText, safeSource } from "../src/api/adapters/result";
import capabilities from "./fixtures/api/capabilities.json";
import territoriesRaw from "./fixtures/api/territories.json";
import result from "./fixtures/api/urban_passenger.json";
import intercity from "./fixtures/api/intercity_passenger.json";
import urbanCargo from "./fixtures/api/urban_cargo.json";
import intercityCargo from "./fixtures/api/intercity_cargo.json";
import proposal from "./fixtures/api/proposal.json";
import scenario from "./fixtures/api/scenario.json";
const territories = territoriesRaw.map((t) => decode("TerritoryOption", t));
const caps = () => decode("Capabilities", capabilities);
function job(status = "succeeded") {
  return {
    job_id: "job-1",
    status,
    request_id: "req-1",
    created_at: "2026-11-09T08:00:00Z",
    started_at: "2026-11-09T08:00:01Z",
    finished_at: status === "running" ? null : "2026-11-09T08:00:02Z",
    analysis_id: status === "succeeded" ? result.analysis_id : null,
    scenario_run_id: null,
    error: null,
    poll_after_seconds: 1,
  };
}
const response = (v: unknown, status = 200, headers?: Record<string, string>) =>
  new Response(JSON.stringify(v), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
describe("canonical API contracts", () => {
  it("accepts four canonical backend result fixtures", () => {
    for (const value of [result, intercity, urbanCargo, intercityCargo])
      expect(decode("AnalysisResult", value).analysis_id).toBe(
        value.analysis_id,
      );
    expect(caps().schema_version).toBe("2");
    expect(decode("ScenarioProposal", proposal).scenario_id).toBe(
      proposal.scenario_id,
    );
    expect(decode("ScenarioResult", scenario).run_id).toBe(scenario.run_id);
  });
  it("rejects unknown response fields and missing required data", () => {
    expect(() =>
      decode("AnalysisResult", { ...result, scores: undefined }),
    ).toThrow();
    expect(() => decode("JobAccepted", { job_id: "j" })).toThrow();
  });
  it("maps city categories and priorities without mock territory IDs", () => {
    const d = {
      ...initialDraft(),
      cityId: "city-1",
      date: "2026-11-09",
      cats: { c04: 3 },
    };
    const request = toRequest(d, territories, caps());
    expect(request).toMatchObject({
      city_id: "city-1",
      categories: ["school"],
      priorities: { school: "high" },
      departure_at: "2026-11-09T08:30:00+03:00",
    });
    expect(() =>
      toRequest({ ...d, cityId: "msk" }, territories, caps()),
    ).toThrow();
    expect(() => toRequest({ ...d, cats: {} }, territories, caps())).toThrow();
  });
  it("maps passenger direction, region IDs, and all selected modes", () => {
    const d = {
      ...initialDraft(),
      kind: "intercity_passenger" as const,
      originId: "region-1",
      destinationId: "region-2",
      level: "region" as const,
      date: "2026-11-09",
    };
    expect(toRequest(d, territories, caps())).toMatchObject({
      origin_id: "region-1",
      destination_id: "region-2",
      territory_level: "region",
      passenger_modes: ["air", "rail", "road", "private_car", "water"],
    });
    expect(() =>
      toRequest({ ...d, destinationId: "region-1" }, territories, caps()),
    ).toThrow();
  });
  it("separates urban freight, multimodal delivery and pipelines", () => {
    const d = {
      ...initialDraft(),
      cityId: "city-1",
      originId: "city-1",
      destinationId: "city-2",
      date: "2026-11-09",
      profileId: "demo-general",
    };
    expect(
      toRequest(
        { ...d, kind: "urban_cargo", direction: "in" },
        territories,
        caps(),
      ),
    ).toMatchObject({ direction: "inbound", city_id: "city-1" });
    expect(
      toRequest({ ...d, kind: "intercity_cargo" }, territories, caps()),
    ).toMatchObject({
      cargo_transport: "multimodal",
      cargo_modes: ["road", "rail", "air", "water"],
    });
    expect(
      toRequest(
        {
          ...d,
          kind: "intercity_cargo",
          pipeline: true,
          pipelineProduct: "oil",
          profileId: "pipe-oil",
        },
        territories,
        caps(),
      ),
    ).toMatchObject({
      cargo_transport: "pipeline",
      pipeline_product: "oil",
      cargo_modes: ["pipeline"],
    });
    expect(() =>
      toRequest(
        { ...d, kind: "intercity_cargo", pipeline: true },
        territories,
        caps(),
      ),
    ).toThrow();
  });
  it("uses origin IANA time and rejects missing or ambiguous local times", () => {
    expect(departureAt("2026-11-09", 34, "Europe/Kaliningrad")).toBe(
      "2026-11-09T08:30:00+02:00",
    );
    expect(() => departureAt("2026-03-08", 10, "America/New_York")).toThrow();
    expect(() => departureAt("2026-11-01", 6, "America/New_York")).toThrow();
    expect(() => departureAt("2026-11-09", 96, "Europe/Moscow")).toThrow();
  });
  it("keeps unknown, pending and zero distinct and formats seconds", () => {
    const value = decode("Metric", result.metrics[0]);
    expect(measureText({ ...value, value: null, status: "unknown" })).toBe(
      "Нет данных",
    );
    expect(measureText({ ...value, value: null, status: "pending" })).toBe(
      "Вычисляется",
    );
    expect(
      measureText({ ...value, value: 0, status: "available", unit: "seconds" }),
    ).toBe("0 мин");
    expect(safeSource("javascript:alert(1)")).toBeUndefined();
  });
});
describe("HTTP and jobs", () => {
  it("bootstraps the session before mutation, preserves a retry key and credentials", async () => {
    vi.useFakeTimers();
    try {
      let posts = 0;
      const calls: { path: string; options?: RequestInit }[] = [];
      const fetcher = vi.fn(
        async (path: RequestInfo | URL, options?: RequestInit) => {
          calls.push({ path: String(path), options });
          if (String(path).endsWith("capabilities"))
            return response(capabilities);
          if (++posts === 1)
            return response(
              {
                error: {
                  code: "engine_unavailable",
                  message: "Unavailable",
                  field_errors: [],
                  request_id: "r",
                  retryable: true,
                },
              },
              503,
            );
          return response(
            {
              job_id: "j",
              status: "queued",
              request_id: "r",
              poll_after_seconds: 1,
            },
            202,
          );
        },
      );
      const client = new ApiClient(fetcher);
      const promise = client.request("/analyses", "JobAccepted", {
        body: { test: true },
        key: "stable-key",
      });
      await vi.runAllTimersAsync();
      await promise;
      expect(calls[0].path).toBe("/api/v1/capabilities");
      const mutations = calls.filter((c) => c.options?.method === "POST");
      expect(mutations).toHaveLength(2);
      expect(mutations[0].options?.body).toBe(mutations[1].options?.body);
      expect(
        mutations.map(
          (c) =>
            (c.options?.headers as Record<string, string>)["Idempotency-Key"],
        ),
      ).toEqual(["stable-key", "stable-key"]);
      expect(
        mutations.every((c) => c.options?.credentials === "same-origin"),
      ).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
  it("does not retry 409 or expose raw error bodies", async () => {
    let count = 0;
    const client = new ApiClient(async (path) => {
      if (String(path).endsWith("capabilities")) return response(capabilities);
      count++;
      return response({ secret: "hidden" }, 409);
    });
    await expect(
      client.request("/analyses", "JobAccepted", { body: {}, key: "k" }),
    ).rejects.toMatchObject({ status: 409, code: "http_error" });
    expect(count).toBe(1);
  });
  it("polls sequentially and stops on terminal success", async () => {
    vi.useFakeTimers();
    try {
      let polls = 0;
      const gateway = new BackendGateway(
        new ApiClient(async (path) =>
          String(path).endsWith("capabilities")
            ? response(capabilities)
            : response(job(++polls === 1 ? "running" : "succeeded")),
        ),
      );
      const pending = gateway.wait(
        {
          job_id: "job-1",
          status: "queued",
          request_id: "r",
          poll_after_seconds: 1,
        },
        new AbortController().signal,
        () => {},
      );
      await vi.runAllTimersAsync();
      expect((await pending).analysis_id).toBe(result.analysis_id);
      expect(polls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
  it("abort ends local polling without a durable cancel request", async () => {
    const urls: string[] = [];
    const controller = new AbortController();
    const gateway = new BackendGateway(
      new ApiClient(async (path) => {
        urls.push(String(path));
        return String(path).endsWith("capabilities")
          ? response(capabilities)
          : response(job("running"));
      }),
    );
    const pending = gateway.wait(
      { job_id: "j", status: "queued", request_id: "r" },
      controller.signal,
      (j) => {
        if (j.status === "running") controller.abort();
      },
    );
    await expect(pending).rejects.toBeDefined();
    expect(urls.every((u) => !u.endsWith("/cancel"))).toBe(true);
  });
  it("keeps field errors and stored-result incompatibility typed", async () => {
    const client = new ApiClient(async (path) =>
      String(path).endsWith("capabilities")
        ? response(capabilities)
        : response(
            {
              error: {
                code: "stored_result_incompatible",
                message: "Recalculate",
                field_errors: [],
                request_id: "saved-r",
                retryable: false,
              },
            },
            409,
          ),
    );
    const pending = client.request("/analyses/a", "AnalysisResult");
    await expect(pending).rejects.toBeInstanceOf(ApiError);
    await expect(pending).rejects.toMatchObject({
      code: "stored_result_incompatible",
      requestId: "saved-r",
    });
  });
});

describe("failure and optional metric boundaries", () => {
  it.each([404, 422])(
    "does not retry HTTP %s and preserves safe field errors",
    async (status) => {
      let calls = 0;
      const client = new ApiClient(async (path) => {
        if (String(path).endsWith("capabilities"))
          return response(capabilities);
        calls++;
        return response(
          {
            error: {
              code: "invalid_request",
              message: "Safe error",
              field_errors: [
                {
                  location: ["body", "categories"],
                  code: "invalid",
                  message: "Choose category",
                },
              ],
              request_id: "r-fields",
              retryable: false,
            },
          },
          status,
        );
      });
      await expect(
        client.request("/analyses/a", "AnalysisResult"),
      ).rejects.toMatchObject({
        status,
        fields: [
          {
            location: ["body", "categories"],
            code: "invalid",
            message: "Choose category",
          },
        ],
      });
      expect(calls).toBe(1);
    },
  );
  it("honours Retry-After for a bounded 429 retry", async () => {
    vi.useFakeTimers();
    try {
      let calls = 0;
      const client = new ApiClient(async (path) =>
        String(path).endsWith("capabilities")
          ? response(capabilities)
          : ++calls === 1
            ? response(
                {
                  error: {
                    code: "busy",
                    message: "Wait",
                    field_errors: [],
                    request_id: "r-busy",
                    retryable: true,
                  },
                },
                429,
                { "Retry-After": "2" },
              )
            : response(result),
      );
      const promise = client.request("/analyses/a", "AnalysisResult");
      await vi.advanceTimersByTimeAsync(1999);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(1);
      expect((await promise).analysis_id).toBe(result.analysis_id);
      expect(calls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
  it("stops on failed and cancelled jobs, including a terminal submission", async () => {
    for (const status of ["failed", "cancelled"] as const) {
      const raw = {
        ...job(status),
        error:
          status === "failed"
            ? {
                code: "engine_unavailable",
                message: "Safe failure",
                request_id: "r-job",
                retryable: false,
                field_errors: [],
              }
            : null,
      };
      const gateway = new BackendGateway(
        new ApiClient(async (path) =>
          String(path).endsWith("capabilities")
            ? response(capabilities)
            : response(raw),
        ),
      );
      const waiting = gateway.wait(
        { job_id: "job-1", status, request_id: "r" },
        new AbortController().signal,
        () => {},
      );
      if (status === "failed")
        await expect(waiting).rejects.toMatchObject({
          code: "engine_unavailable",
        });
      else expect((await waiting).status).toBe("cancelled");
    }
  });
  it("does not hide a ready score when a separate count is pending", () => {
    const data = decode("AnalysisResult", {
      ...result,
      counts: [
        {
          metric_id: "count",
          status: "pending",
          value: null,
          threshold_seconds: null,
          dependency_job_id: "count-job",
          reason_code: "PENDING",
          evidence_refs: [],
        },
      ],
    });
    expect(data.counts[0].status).toBe("pending");
    expect(data.scores[0].status).toBe("available");
    expect(measureText(data.scores[0])).not.toBe("Вычисляется");
  });
});
