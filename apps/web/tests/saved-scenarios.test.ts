import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/client";
import { BackendGateway } from "../src/api/gateway";
import { decode } from "../src/api/validation";
import { useAnalysisSession } from "../src/features/analysis/useAnalysisSession";
import { parseSavedPointer, savedPointerHash } from "../src/features/analysis/savedPointer";
import type { Schemas } from "../src/api/types";
import baselineRaw from "./fixtures/api/urban_passenger.json";
import scenarioRaw from "./fixtures/api/scenario.json";
import proposalRaw from "./fixtures/api/proposal.json";
const baseline = decode("AnalysisResult", baselineRaw);
const scenario = { ...decode("ScenarioResult", scenarioRaw), run_id: "pending:scenario:historical" };
const proposal = decode("ScenarioProposal", proposalRaw);
const completed: Schemas["JobView"] = { job_id: "scenario-job", status: "succeeded", request_id: "trace", created_at: "2026-11-09T00:00:00Z",
  started_at: "2026-11-09T00:00:00Z", finished_at: "2026-11-09T00:00:01Z", analysis_id: null, scenario_run_id: scenario.run_id, error: null };
function gateway() {
  return Object.assign(new BackendGateway(), { result: vi.fn().mockResolvedValue(baseline), scenarioResult: vi.fn().mockResolvedValue(scenario),
    proposals: vi.fn().mockResolvedValue([]), job: vi.fn().mockResolvedValue(completed), submit: vi.fn(), scenario: vi.fn() });
}
afterEach(cleanup);
describe("saved scenario boundaries", () => {
  it("decodes opaque scenario pointers once and reads their canonical baseline without POST", async () => {
    const g = gateway();
    const { result } = renderHook(() => useAnalysisSession(g));
    const hash = savedPointerHash({ kind: "scenario", id: scenario.run_id });
    expect(parseSavedPointer(hash)).toEqual({ kind: "scenario", id: scenario.run_id });
    await act(() => result.current.restore(hash));
    expect(g.scenarioResult).toHaveBeenCalledWith(scenario.run_id, expect.any(AbortSignal));
    expect(g.result).toHaveBeenCalledWith(scenario.baseline_analysis_id, expect.any(AbortSignal));
    expect(result.current.result).toEqual(baseline);
    expect(result.current.scenario).toEqual(scenario);
    expect(g.submit).not.toHaveBeenCalled();
    expect(g.scenario).not.toHaveBeenCalled();
  });
  it.each(["run_id", "baseline_analysis_id", "baseline_result_hash"] as const)("rejects %s mismatch atomically before displaying either snapshot", async (field) => {
    const g = gateway();
    g.scenarioResult.mockResolvedValue({ ...scenario, [field]: "foreign" });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore(savedPointerHash({ kind: "scenario", id: scenario.run_id })));
    expect(result.current.phase).toBe("failed");
    expect(result.current.result).toBeNull();
    expect(result.current.scenario).toBeNull();
    expect(g.proposals).not.toHaveBeenCalled();
  });
  it.each(["scenario", "baseline"] as const)("does not disclose either snapshot when %s GET loses access", async (where) => {
    const g = gateway();
    (where === "scenario" ? g.scenarioResult : g.result).mockRejectedValue(new ApiError(404, "unknown_id", "Private data"));
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore(savedPointerHash({ kind: "scenario", id: scenario.run_id })));
    expect(result.current.result).toBeNull();
    expect(result.current.scenario).toBeNull();
    expect(result.current.error?.message).toContain("недоступен в этой сессии");
    expect(g.submit).not.toHaveBeenCalled();
    expect(g.scenario).not.toHaveBeenCalled();
  });
  it("resumes only scenario jobs in scenario_job pointers and rejects an analysis output", async () => {
    const g = gateway();
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore("#atlas?scenario_job=scenario-job"));
    expect(result.current.scenario).toEqual(scenario);
    g.job.mockResolvedValue({ ...completed, analysis_id: baseline.analysis_id, scenario_run_id: null });
    await act(() => result.current.restore("#atlas?scenario_job=scenario-job"));
    expect(result.current.result).toBeNull();
    expect(result.current.scenario).toBeNull();
    expect(result.current.error?.message).toContain("не относится к заданию сценария");
    expect(g.scenario).not.toHaveBeenCalled();
  });
  it("ignores late scenario GETs after navigating to a baseline", async () => {
    const g = gateway();
    let finish!: (value: typeof scenario) => void;
    g.scenarioResult.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let pending!: Promise<unknown>;
    act(() => { pending = result.current.restore(savedPointerHash({ kind: "scenario", id: scenario.run_id })); });
    await act(() => result.current.restore(savedPointerHash({ kind: "analysis", id: baseline.analysis_id })));
    await act(async () => { finish(scenario); await pending; });
    expect(result.current.result).toEqual(baseline);
    expect(result.current.scenario).toBeNull();
    expect(g.result).toHaveBeenCalledTimes(1);
  });
  it("writes distinct job/result pointers only during an explicit scenario run and preserves baseline", async () => {
    const g = gateway();
    g.scenario.mockResolvedValue({ job_id: "scenario-job", status: "queued", request_id: "trace", poll_after_seconds: 1 });
    const navigate = vi.fn();
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore(savedPointerHash({ kind: "analysis", id: baseline.analysis_id })));
    const before = structuredClone(result.current.result);
    await act(() => result.current.runScenario(proposal, navigate));
    expect(navigate.mock.calls).toEqual([["scenario_job", "scenario-job"], ["scenario", scenario.run_id]]);
    expect(result.current.result).toEqual(before);
    expect(result.current.scenario).toEqual(scenario);
  });
  it("abandons a late accepted scenario response when navigation has already left its context", async () => {
    const g = gateway();
    g.scenario.mockResolvedValue({ job_id: "scenario-job", status: "queued", request_id: "trace", poll_after_seconds: 1 });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore(savedPointerHash({ kind: "analysis", id: baseline.analysis_id })));
    await act(() => result.current.runScenario(proposal, () => false));
    expect(g.job).not.toHaveBeenCalled();
    expect(g.scenarioResult).not.toHaveBeenCalled();
    expect(result.current.scenario).toBeNull();
    expect(result.current.result).toEqual(baseline);
  });
});
