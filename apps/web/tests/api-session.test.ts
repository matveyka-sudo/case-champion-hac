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
