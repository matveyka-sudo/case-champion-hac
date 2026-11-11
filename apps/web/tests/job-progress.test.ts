import { describe, expect, it } from "vitest";
import { decode } from "../src/api/validation";
import { jobProgressText } from "../src/api/adapters/jobProgress";
import { ApiClient } from "../src/api/client";
import { BackendGateway } from "../src/api/gateway";
import type { Schemas } from "../src/api/types";
import capabilities from "./fixtures/api/capabilities.json";

type Status = Schemas["JobView"]["status"];
function job(status: Status = "running", progress?: unknown) {
  return { job_id: "job-progress", request_id: "synthetic-progress", status,
    created_at: "2026-11-11T00:00:00Z", started_at: status === "queued" ? null : "2026-11-11T00:00:01Z",
    finished_at: ["succeeded", "failed", "cancelled"].includes(status) ? "2026-11-11T00:01:00Z" : null,
    analysis_id: status === "succeeded" ? "saved-analysis" : null, scenario_run_id: null,
    error: status === "failed" ? { code: "job_failed", message: "Synthetic failure", request_id: "r", field_errors: [], retryable: false } : null,
    ...(progress === undefined ? {} : { progress }) };
}
const progress = (phase = "routing", completed_batches = 31, total_batches = 88) => ({ phase, completed_batches, total_batches });

describe("persisted batch progress", () => {
  it.each(["queued", "running", "succeeded", "failed", "cancelled"] as const)("keeps omitted progress compatible and unmodified for legacy %s jobs", status => {
    const value = job(status), before = JSON.stringify(value);
    expect(decode("JobView", value)).toBe(value);
    expect(jobProgressText(decode("JobView", value), "running")).toBe("");
    expect(JSON.stringify(value)).toBe(before);
  });

  it("displays persisted package counts, finalization and retrieval without claiming result success", () => {
    expect(jobProgressText(decode("JobView", job("running", progress())), "running")).toBe("Пакеты 31 из 88 · Расчёт маршрутов");
    expect(jobProgressText(decode("JobView", job("running", progress("routing", 0))), "running")).toContain("Пакеты 0 из 88");
    expect(jobProgressText(decode("JobView", job("running", progress("finalizing", 88))), "running")).toBe("Пакеты 88 из 88 · Сохранение результата");
    const published = decode("JobView", job("succeeded", progress("published", 88)));
    expect(jobProgressText(published, "running")).toBe("Пакеты 88 из 88 · Получение сохранённого результата");
    expect(jobProgressText(published, "done")).toBe("");
    expect(jobProgressText(published, "failed")).toBe("");
    expect(jobProgressText(decode("JobView", job("failed", progress("stopped", 31))), "failed")).toBe("Пакеты 31 из 88 · Расчёт остановлен");
    expect(jobProgressText(decode("JobView", job("cancelled", progress("stopped", 88))), "running")).toBe("Пакеты 88 из 88 · Расчёт остановлен");
  });

  it("keeps enqueue responses outside the progress extension", () => {
    expect(() => decode("JobAccepted", { job_id: "new-job", request_id: "r", status: "queued", progress: progress() })).toThrow("API_CONTRACT_INVALID");
  });

  it.each([
    ["running", progress("routing", -1)], ["running", progress("routing", 1.5)], ["running", progress("routing", true as unknown as number)],
    ["running", progress("routing", 0, 0)], ["running", progress("routing", 0, 4097)], ["running", progress("routing", 89)],
    ["running", progress("routing", 88)], ["running", progress("finalizing", 87)], ["succeeded", progress("published", 87)],
    ["queued", progress()], ["running", progress("stopped")], ["succeeded", progress()], ["failed", progress("published", 88)],
    ["cancelled", progress()], ["running", progress("invented")], ["running", { ...progress(), eta_seconds: 1 }],
  ] as const)("rejects incoherent or unknown %s progress without clamping", (status, value) => {
    expect(() => decode("JobView", job(status, value))).toThrow("API_CONTRACT_INVALID");
  });

  it("opts into GET progress while old-server omission and cancel/enqueue requests retain their old shape", async () => {
    const requests: { path: string; method: string; body?: BodyInit | null }[] = [];
    const gateway = new BackendGateway(new ApiClient(async (path, options) => {
      requests.push({ path: String(path), method: options?.method || "GET", body: options?.body });
      const value = String(path).endsWith("/capabilities") ? capabilities : String(path) === "/api/v1/analyses"
        ? { job_id: "job-progress", request_id: "r", status: "queued" } : job("cancelled");
      return new Response(JSON.stringify(value));
    }));
    expect((await gateway.job("id:one")).status).toBe("cancelled");
    await gateway.cancel("id:one");
    await gateway.submit({} as Schemas["UrbanPassengerRequest"], "original-key");
    expect(requests.map(item => [item.path, item.method])).toEqual([
      ["/api/v1/capabilities", "GET"], ["/api/v1/jobs/id%3Aone?include_progress=true", "GET"],
      ["/api/v1/jobs/id%3Aone/cancel", "POST"], ["/api/v1/analyses", "POST"],
    ]);
    expect(requests[2].body).toBe("{}");
  });
});
