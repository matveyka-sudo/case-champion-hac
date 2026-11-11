import { act, renderHook, waitFor, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAnalysisSession } from "../src/features/analysis/useAnalysisSession";
import { BackendGateway } from "../src/api/gateway";
import { ApiError } from "../src/api/client";
import { decode } from "../src/api/validation";
import { deltaText, timestampText } from "../src/api/adapters/result";
import resultRaw from "./fixtures/api/urban_passenger.json";
import proposalRaw from "./fixtures/api/proposal.json";
import scenarioRaw from "./fixtures/api/scenario.json";
const base = decode("AnalysisResult", resultRaw);
const proposal = decode("ScenarioProposal", proposalRaw);
const scenario = decode("ScenarioResult", scenarioRaw);
const gateway = () =>
  Object.assign(new BackendGateway(), {
    analyze: vi.fn().mockResolvedValue(base),
    proposals: vi.fn().mockResolvedValue([]),
  });
afterEach(cleanup);
describe("saved analysis session boundaries", () => {
  it("ignores late analysis replies after draft revision changes without cancelling the durable job", async () => {
    let finish!: (v: typeof base) => void;
    const g = gateway();
    g.analyze.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    g.cancel = vi.fn();
    const { result } = renderHook(() => useAnalysisSession(g));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.run(base.request);
    });
    act(() => result.current.changed());
    await act(async () => {
      finish(base);
      await pending;
    });
    expect(result.current.result).toBeNull();
    expect(result.current.phase).toBe("idle");
    expect(g.cancel).not.toHaveBeenCalled();
  });
  it("retains the saved snapshot when a new operation fails and uses a fresh idempotency key", async () => {
    const g = gateway();
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    act(() => result.current.changed());
    g.analyze.mockRejectedValueOnce(
      new ApiError(503, "engine_unavailable", "Unavailable"),
    );
    await act(() => result.current.run(base.request));
    expect(result.current.result).toEqual(base);
    expect(result.current.dirty).toBe(true);
    expect(result.current.phase).toBe("failed");
    expect(g.analyze.mock.calls[0][1]).not.toBe(g.analyze.mock.calls[1][1]);
  });
  it("recovers an accepted job after a lost POST response using the same manual-retry key", async () => {
    const g = gateway();
    const jobs = new Set<string>();
    let lost = true;
    g.analyze.mockImplementation(async (_request, key) => {
      jobs.add(key);
      if (lost) { lost = false; throw new ApiError(0, "network_error", "Lost POST response", [], null, true); }
      return base;
    });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    expect(result.current.phase).toBe("failed");
    const samePayload = Object.fromEntries(Object.entries(base.request).reverse()) as typeof base.request;
    await act(() => result.current.run(samePayload));
    expect(g.analyze.mock.calls[0][1]).toBe(g.analyze.mock.calls[1][1]);
    expect(jobs.size).toBe(1);
    expect(result.current.result).toEqual(base);
    await act(() => result.current.run(base.request));
    expect(g.analyze.mock.calls[2][1]).not.toBe(g.analyze.mock.calls[1][1]);
  });
  it("never reuses a pending submission key for changed payloads or a user revision", async () => {
    const g = gateway();
    g.analyze.mockRejectedValue(new ApiError(503, "RUNTIME_INPUTS_BUSY", "Busy", [], null, true));
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    const changedRequest = { ...base.request, departure_at: "2026-11-09T09:00:00+03:00" };
    await act(() => result.current.run(changedRequest));
    expect(g.analyze.mock.calls[0][1]).not.toBe(g.analyze.mock.calls[1][1]);
    act(() => result.current.changed());
    await act(() => result.current.run(changedRequest));
    expect(g.analyze.mock.calls[2][1]).not.toBe(g.analyze.mock.calls[1][1]);
  });
  it("clears a pending key after a confirmed terminal job failure even if its error is retryable", async () => {
    const g = gateway();
    g.analyze.mockImplementation(async (_request, _key, _signal, onJob) => {
      onJob({ job_id: "terminal-job", request_id: "r", status: "failed", created_at: "2026-11-10T00:00:00Z", started_at: null, finished_at: "2026-11-10T00:00:01Z", analysis_id: null, scenario_run_id: null, error: null });
      throw new ApiError(503, "worker_unavailable", "Terminal failure", [], null, true);
    });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    await act(() => result.current.run(base.request));
    expect(g.analyze.mock.calls[0][1]).not.toBe(g.analyze.mock.calls[1][1]);
  });
  it("clears a completed city context and prevents old analysis actions after reset", async () => {
    const g = gateway();
    g.explain = vi.fn();
    g.scenario = vi.fn();
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    act(() => result.current.reset());
    expect(result.current).toMatchObject({ result: null, scenario: null, job: null, phase: "idle", dirty: false,
      proposals: [], messages: [], chatBusy: false, error: null, warning: "" });
    await act(() => result.current.explain("Почему?", null));
    await act(() => result.current.runScenario(proposal));
    expect(g.explain).not.toHaveBeenCalled();
    expect(g.scenario).not.toHaveBeenCalled();
  });
  it("rejects the first city's late result and progress after an A → B → A reset sequence", async () => {
    let finish!: (v: typeof base) => void;
    const g = gateway();
    g.analyze.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let first!: Promise<void>;
    act(() => { first = result.current.run(base.request); });
    const firstSignal = g.analyze.mock.calls[0][2];
    const firstProgress = g.analyze.mock.calls[0][3];
    act(() => result.current.reset());
    expect(firstSignal.aborted).toBe(true);
    const cityB = structuredClone(base);
    if ("city_id" in cityB.request) cityB.request.city_id = "city-2";
    g.analyze.mockResolvedValueOnce(cityB);
    await act(() => result.current.run(cityB.request));
    expect(result.current.result?.request).toEqual(cityB.request);
    act(() => result.current.reset());
    const latestA = { ...base, analysis_id: "latest-city-a" };
    g.analyze.mockResolvedValueOnce(latestA);
    await act(() => result.current.run(base.request));
    await act(async () => {
      firstProgress({ job_id: "old-city-a-job", status: "running", request_id: "r" });
      finish({ ...base, analysis_id: "stale-city-a" });
      await first;
    });
    expect(result.current.result?.analysis_id).toBe("latest-city-a");
    expect(result.current.job).toBeNull();
    expect(result.current.phase).toBe("done");
  });
  it("blocks non-editable scenario proposals and keeps baseline immutable after an allowed run", async () => {
    const g = gateway();
    g.scenario = vi
      .fn()
      .mockResolvedValue({ job_id: "s", status: "succeeded", request_id: "r" });
    g.wait = vi
      .fn()
      .mockResolvedValue({
        job_id: "s",
        status: "succeeded",
        scenario_run_id: scenario.run_id,
      });
    g.scenarioResult = vi.fn().mockResolvedValue(scenario);
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    await act(() =>
      result.current.runScenario({ ...proposal, editable_local_graph: false }),
    );
    expect(g.scenario).not.toHaveBeenCalled();
    await act(() => result.current.runScenario(proposal));
    expect(g.scenario).toHaveBeenCalledOnce();
    expect(result.current.result).toEqual(base);
    expect(result.current.scenario).toEqual(scenario);
  });
  it("binds explanations to saved selection/hash and ignores a late reply in another context", async () => {
    const g = gateway();
    let finish!: (v: unknown) => void;
    g.explain = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    const selection = {
      entity_kind: "sample_point" as const,
      entity_id: "server-point",
    };
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.explain("Почему?", selection);
    });
    await waitFor(() => expect(g.explain).toHaveBeenCalled());
    expect(g.explain).toHaveBeenCalledWith(
      base.analysis_id,
      { schema_version: "2", question: "Почему?", selection },
      expect.any(AbortSignal),
    );
    act(() => result.current.changed());
    await act(async () => {
      finish({
        analysis_id: base.analysis_id,
        analysis_result_hash: base.result_hash,
      });
      await pending;
    });
    expect(result.current.messages).toEqual([]);
  });
  it("starts a new chat without losing the saved result or accepting the old pending answer", async () => {
    const g = gateway();
    let finish!: (value: unknown) => void;
    g.explain = vi.fn().mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    let pending!: Promise<void>;
    act(() => { pending = result.current.explain("Почему?", null); });
    act(() => result.current.clearExplanation());
    await act(async () => { finish({ analysis_id: base.analysis_id, analysis_result_hash: base.result_hash }); await pending; });
    expect(result.current.result).toEqual(base);
    expect(result.current.dirty).toBe(false);
    expect(result.current.chatBusy).toBe(false);
    expect(result.current.messages).toEqual([]);
  });
  it("keeps explanation failures separate from analysis failures when asking or clearing the chat", async () => {
    const g = gateway();
    const explanationError = new ApiError(503, "assistant_unavailable", "Explanation unavailable");
    const analysisError = new ApiError(503, "engine_unavailable", "Analysis unavailable");
    g.explain = vi.fn().mockRejectedValue(explanationError);
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    await act(() => result.current.explain("Почему?", null));
    expect(result.current.explanationError).toBe(explanationError);
    expect(result.current.error).toBeNull();
    expect(result.current.phase).toBe("done");
    expect(result.current.result).toEqual(base);
    act(() => result.current.clearExplanation());
    expect(result.current.explanationError).toBeNull();
    g.analyze.mockRejectedValueOnce(analysisError);
    await act(() => result.current.run(base.request));
    expect(result.current.error).toBe(analysisError);
    expect(result.current.explanationError).toBeNull();
    await act(() => result.current.explain("Почему?", null));
    expect(result.current.error).toBe(analysisError);
    expect(result.current.explanationError).toBe(explanationError);
    act(() => result.current.clearExplanation());
    expect(result.current.error).toBe(analysisError);
    expect(result.current.explanationError).toBeNull();
    act(() => result.current.reset());
    expect(result.current.error).toBeNull();
    expect(result.current.explanationError).toBeNull();
  });
  it("reports an explanation hash mismatch only in the chat while retaining the saved analysis", async () => {
    const g = gateway();
    g.explain = vi.fn().mockResolvedValue({ analysis_id: base.analysis_id, analysis_result_hash: "another-result" });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    await act(() => result.current.explain("Почему?", null));
    expect(result.current.explanationError).toMatchObject({ code: "analysis_mismatch" });
    expect(result.current.error).toBeNull();
    expect(result.current.phase).toBe("done");
    expect(result.current.result).toEqual(base);
    expect(result.current.messages).toEqual([]);
  });
  it("formats destination time independently of browser time zone and preserves null connection deltas", () => {
    expect(
      timestampText("2026-11-09T09:00:00+03:00", "Europe/Kaliningrad"),
    ).toContain("08:00");
    expect(timestampText(null, "Europe/Moscow")).toBe("Время неизвестно");
    const d = {
      ...scenario.deltas[0],
      delta: null,
      outcome: "lost_connection" as const,
      sign_convention: "after_minus_before" as const,
    };
    expect(deltaText(d)).toContain("Связь потеряна · Без численной разности");
    expect(deltaText({ ...d, outcome: "new_connection" })).toContain(
      "Связь появилась",
    );
  });
});

describe("batch progress operation boundaries", () => {
  const runningJob = { job_id: "active-job", status: "running" as const, request_id: "r", created_at: "2026-11-11T00:00:00Z", started_at: "2026-11-11T00:00:01Z", finished_at: null, analysis_id: null, scenario_run_id: null, error: null };

  it("clears a previous published job before a new submission response arrives", async () => {
    const g = gateway();
    g.analyze.mockImplementationOnce(async (_request, _key, _signal, onJob) => {
      onJob({ ...runningJob, status: "succeeded", finished_at: "2026-11-11T00:01:00Z", analysis_id: base.analysis_id });
      return base;
    });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    expect(result.current.job?.status).toBe("succeeded");
    let finish!: (value: typeof base) => void;
    g.analyze.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.run(base.request); });
    expect(result.current.phase).toBe("running");
    expect(result.current.job).toBeNull();
    expect(result.current.result).toBe(base);
    await act(async () => { finish(base); await pending; });
  });

  it("keeps finalizing and published jobs loading until the result is delivered", async () => {
    const g = gateway();
    let finish!: (value: typeof base) => void, onJob!: Parameters<BackendGateway["analyze"]>[3];
    g.analyze.mockImplementationOnce((_request, _key, _signal, update) => { onJob = update; return new Promise(resolve => { finish = resolve; }); });
    const { result } = renderHook(() => useAnalysisSession(g));
    let pending!: Promise<void>;
    act(() => { pending = result.current.run(base.request); });
    act(() => onJob({ ...runningJob, progress: { completed_batches: 88, total_batches: 88, phase: "finalizing" } }));
    expect(result.current.phase).toBe("running");
    expect(result.current.result).toBeNull();
    act(() => onJob({ ...runningJob, status: "succeeded", finished_at: "2026-11-11T00:01:00Z", analysis_id: base.analysis_id,
      progress: { completed_batches: 88, total_batches: 88, phase: "published" } }));
    expect(result.current.phase).toBe("running");
    expect(result.current.result).toBeNull();
    await act(async () => { finish(base); await pending; });
    expect(result.current.phase).toBe("done");
  });

  it("does not borrow baseline batch progress while a scenario submission is pending", async () => {
    const g = gateway();
    g.analyze.mockImplementationOnce(async (_request, _key, _signal, onJob) => {
      onJob({ ...runningJob, status: "succeeded", finished_at: "2026-11-11T00:01:00Z", analysis_id: base.analysis_id,
        progress: { completed_batches: 88, total_batches: 88, phase: "published" } });
      return base;
    });
    let accept!: (value: { job_id: string; request_id: string; status: "queued" }) => void;
    g.scenario = vi.fn().mockImplementation(() => new Promise(resolve => { accept = resolve; }));
    g.wait = vi.fn().mockResolvedValue({ job_id: "scenario-job", status: "succeeded", scenario_run_id: scenario.run_id });
    g.scenarioResult = vi.fn().mockResolvedValue(scenario);
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.run(base.request));
    let pending!: Promise<void>;
    act(() => { pending = result.current.runScenario(proposal); });
    expect(result.current.phase).toBe("running");
    expect(result.current.job).toBeNull();
    await act(async () => { accept({ job_id: "scenario-job", request_id: "r", status: "queued" }); await pending; });
    expect(result.current.scenario).toBe(scenario);
  });

  it("ignores a late cancellation reply belonging to the previous operation", async () => {
    const g = gateway();
    let finishFirst!: (value: null) => void, finishSecond!: (value: typeof base) => void;
    let finishCancel!: (value: ReturnType<typeof cancelledJob>) => void;
    const cancelledJob = () => ({ ...runningJob, status: "cancelled" as const, finished_at: "2026-11-11T00:01:00Z" });
    g.analyze.mockImplementationOnce((_request, _key, _signal, onJob) => { onJob(runningJob); return new Promise(resolve => { finishFirst = resolve; }); });
    g.cancel = vi.fn().mockImplementation(() => new Promise(resolve => { finishCancel = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let first!: Promise<void>, cancellation!: Promise<void>, second!: Promise<void>;
    act(() => { first = result.current.run(base.request); });
    act(() => { cancellation = result.current.cancel(); });
    await act(async () => { finishFirst(null); await first; });
    g.analyze.mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve; }));
    act(() => { second = result.current.run(base.request); });
    const newSignal = g.analyze.mock.calls[1][2];
    await act(async () => { finishCancel(cancelledJob()); await cancellation; });
    expect(newSignal.aborted).toBe(false);
    expect(result.current.phase).toBe("running");
    expect(result.current.job).toBeNull();
    await act(async () => { finishSecond(base); await second; });
    expect(result.current.result).toBe(base);
  });
});
