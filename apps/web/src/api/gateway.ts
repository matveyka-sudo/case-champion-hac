import { ApiClient, pause, ApiError } from "./client";
import type { AnalysisRequest, Schemas, AnalysisResult } from "./types";
export class BackendGateway {
  readonly client: ApiClient;
  constructor(client = new ApiClient()) {
    this.client = client;
  }
  capabilities = () => this.client.capabilities();
  territories = (level: "city" | "region", query = "", signal?: AbortSignal) =>
    this.client.territories(level, query, signal);
  submit = (request: AnalysisRequest, key: string, signal?: AbortSignal) =>
    this.client.request("/analyses", "JobAccepted", {
      body: request,
      key,
      signal,
    });
  job = (id: string, signal?: AbortSignal) =>
    this.client.request("/jobs/" + encodeURIComponent(id) + "?include_progress=true", "JobView", {
      signal,
    });
  cancel = (id: string, signal?: AbortSignal) =>
    this.client.request(
      "/jobs/" + encodeURIComponent(id) + "/cancel",
      "JobView",
      { body: {}, signal },
    );
  result = (id: string, signal?: AbortSignal) =>
    this.client.request(
      "/analyses/" + encodeURIComponent(id),
      "AnalysisResult",
      { signal },
    );
  explain = (
    id: string,
    body: Schemas["ExplanationRequest"],
    signal?: AbortSignal,
  ) =>
    this.client.request(
      "/analyses/" + encodeURIComponent(id) + "/explanation",
      "Explanation",
      { body, signal },
    );
  async proposals(id: string, signal?: AbortSignal) {
    await this.client.capabilities();
    return this.client.list(
      "/analyses/" + encodeURIComponent(id) + "/scenarios",
      "ScenarioProposal",
      signal,
    );
  }
  scenario = (id: string, key: string, signal?: AbortSignal) =>
    this.client.request(
      "/scenarios/" + encodeURIComponent(id) + "/runs",
      "JobAccepted",
      { body: { schema_version: "2" }, key, signal },
    );
  scenarioResult = (id: string, signal?: AbortSignal) =>
    this.client.request(
      "/scenario-runs/" + encodeURIComponent(id),
      "ScenarioResult",
      { signal },
    );
  async wait(
    accepted: Schemas["JobAccepted"] | Schemas["JobView"],
    signal: AbortSignal,
    onJob: (j: Schemas["JobAccepted"] | Schemas["JobView"]) => void,
  ) {
    onJob(accepted);
    let j = "created_at" in accepted ? accepted : await this.job(accepted.job_id, signal);
    if (j.job_id !== accepted.job_id) throw new ApiError(0, "invalid_response", "Ответ относится к другому заданию");
    onJob(j);
    while (j.status === "queued" || j.status === "running") {
      await pause((j.poll_after_seconds ?? 2) * 1000, signal);
      j = await this.job(j.job_id, signal);
      if (j.job_id !== accepted.job_id) throw new ApiError(0, "invalid_response", "Ответ относится к другому заданию");
      onJob(j);
    }
    if (j.status === "failed")
      throw new ApiError(
        0,
        j.error?.code || "job_failed",
        j.error?.message || "Расчёт завершился ошибкой",
        j.error?.field_errors || [],
        j.error?.request_id || j.request_id,
        j.error?.retryable || false,
      );
    return j;
  }
  async analyze(
    request: AnalysisRequest,
    key: string,
    signal: AbortSignal,
    onJob: (j: Schemas["JobAccepted"] | Schemas["JobView"]) => void,
  ): Promise<AnalysisResult | null> {
    const accepted = await this.submit(request, key, signal);
    const j = await this.wait(accepted, signal, onJob);
    if (j.status === "cancelled") return null;
    if (!j.analysis_id)
      throw new ApiError(
        0,
        "invalid_response",
        "Задание не содержит идентификатор анализа",
      );
    return this.result(j.analysis_id, signal);
  }
}
export const backend = new BackendGateway();
