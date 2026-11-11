import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/client";
import { BackendGateway } from "../src/api/gateway";
import { decode } from "../src/api/validation";
import { savedRequestDraft } from "../src/api/adapters/savedRequest";
import { toRequest } from "../src/api/adapters/request";
import { parseSavedPointer, savedPointerHash } from "../src/features/analysis/savedPointer";
import { useAnalysisSession } from "../src/features/analysis/useAnalysisSession";
import type { Schemas } from "../src/api/types";
import resultRaw from "./fixtures/api/urban_passenger.json";
import cargoRaw from "./fixtures/api/urban_cargo.json";
import capsRaw from "./fixtures/api/capabilities.json";
import territoriesRaw from "./fixtures/api/territories.json";
const base = decode("AnalysisResult", resultRaw);
const caps = decode("Capabilities", capsRaw);
const territories = territoriesRaw.map((value) => decode("TerritoryOption", value));
const job = (status: Schemas["JobView"]["status"] = "succeeded"): Schemas["JobView"] => ({
  job_id: "saved-job", status, request_id: "trace", created_at: "2026-11-09T00:00:00Z", started_at: "2026-11-09T00:00:00Z",
  finished_at: status === "running" || status === "queued" ? null : "2026-11-09T00:00:01Z",
  analysis_id: status === "succeeded" ? base.analysis_id : null, scenario_run_id: null, error: null,
});
function gateway() {
  return Object.assign(new BackendGateway(), { result: vi.fn().mockResolvedValue(base), proposals: vi.fn().mockResolvedValue([]),
    job: vi.fn().mockResolvedValue(job()), submit: vi.fn(), cancel: vi.fn() });
}
afterEach(cleanup);
describe("saved result pointers", () => {
  it("round trips opaque historical identifiers with exactly one URL decoding", () => {
    for (const id of ["pending:scenario:0123", "analysis-1", "сохранённый анализ", "id%2Fpart"]) {
      expect(parseSavedPointer(savedPointerHash({ kind: "analysis", id }))).toEqual({ kind: "analysis", id });
    }
    expect(parseSavedPointer("#atlas?job=a%252Fb")).toEqual({ kind: "job", id: "a%2Fb" });
    expect(parseSavedPointer("#atlas")).toBeNull();
  });
  it.each(["#atlas?analysis=", "#atlas?analysis=a&analysis=b", "#atlas?analysis=a&job=b", "#atlas?unknown=a", "#atlas?analysis=%", "#atlas?analysis=%FF", "#atlas?analysis=%0Asecret", "#atlas?analysis=+a", `#atlas?analysis=${"a".repeat(257)}`, "#atlas-other"])("refuses malformed/unsupported pointer %s", (hash) => {
    expect(() => parseSavedPointer(hash)).toThrow(/некорректна|не поддерживается/);
  });
});
describe("saved request restoration", () => {
  it.each([["city-1", "Москва", "Europe/Moscow", "2026-11-09T21:15:00Z", "2026-11-10", 1],
    ["city-2", "Калининград", "Europe/Kaliningrad", "2026-11-09T22:15:00Z", "2026-11-10", 1],
    ["novorossiysk", "Новороссийск", "Europe/Moscow", "2026-11-09T21:15:00Z", "2026-11-10", 1]] as const)("restores %s from the historical request in its IANA zone", (id, name, timezone, departure, date, slot) => {
    const value = structuredClone(base);
    value.request = { ...base.request, analysis_kind: "urban_passenger", city_id: id, departure_at: departure,
      passenger_mode: "walk", area_id: null, categories: ["school"], priorities: { school: "high" } };
    const catalog = [...territories.filter((p) => p.territory_id !== id), { territory_id: id, name, timezone, level: "city" as const, parent_region_id: null, capital_city_id: null }];
    const restored = savedRequestDraft(value, catalog, caps);
    expect(restored.draft).toMatchObject({ cityId: id, date, slot, travel: "walk", cats: { c04: 3 }, snapshotId: base.snapshot_id });
    expect(restored.warning).toBe("");
    // No current source preset, default categories or wall-clock date replace saved values.
    expect(restored.draft.cats).toEqual({ c04: 3 });
  });
  it("retains retired snapshot/profile selections without upgrading the saved result", () => {
    const value = structuredClone(base);
    value.request = { ...base.request, analysis_kind: "urban_passenger", city_id: "city-1", passenger_mode: "walk", categories: ["school"], priorities: { school: "high" } };
    value.snapshot_id = "retired-snapshot";
    const { draft } = savedRequestDraft(value, territories, caps);
    expect(draft.snapshotId).toBe("retired-snapshot");
    expect(() => toRequest(draft, territories, caps)).toThrow(/набор данных/);
    expect(value.snapshot_id).toBe("retired-snapshot");
    const cargo = decode("AnalysisResult", cargoRaw);
    const unavailable = savedRequestDraft(cargo, territories, { ...caps, cargo_profiles: [] });
    expect(unavailable.draft.profileId).toBe("demo-general");
    expect(unavailable.warning).not.toBe("");
    expect(unavailable.cargoProfile).toBeNull();
  });
});
describe("read-only saved analysis recovery", () => {
  it("opens only through GET and rejects a mismatched analysis identity", async () => {
    const g = gateway();
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore(savedPointerHash({ kind: "analysis", id: base.analysis_id })));
    expect(result.current.result).toEqual(base);
    expect(result.current.phase).toBe("done");
    expect(g.submit).not.toHaveBeenCalled();
    await act(() => result.current.restore("#atlas?analysis=foreign"));
    expect(result.current.result).toBeNull();
    expect(result.current.error?.message).toContain("другому анализу");
  });
  it.each([404, 410])("revalidates on retry and exposes no previous result on session/expiry %s", async (status) => {
    const g = gateway();
    const { result } = renderHook(() => useAnalysisSession(g));
    const hash = savedPointerHash({ kind: "analysis", id: base.analysis_id });
    await act(() => result.current.restore(hash));
    g.result.mockRejectedValue(new ApiError(status, "unknown_id", "Private detail"));
    await act(() => result.current.restore(hash));
    await act(() => result.current.restore(hash));
    expect(result.current.result).toBeNull();
    expect(result.current.error?.message).toMatch(/недоступен в этой сессии|доступа.*истёк/);
    expect(g.result).toHaveBeenCalledTimes(3);
    expect(g.submit).not.toHaveBeenCalled();
  });
  it("ignores late navigation replies and local draft edits without cancelling the durable job", async () => {
    let finish!: (value: typeof base) => void;
    const g = gateway();
    g.result.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let first!: Promise<unknown>;
    act(() => { first = result.current.restore("#atlas?analysis=first"); });
    await act(() => result.current.restore(savedPointerHash({ kind: "analysis", id: base.analysis_id })));
    await act(async () => { finish({ ...base, analysis_id: "first" }); await first; });
    expect(result.current.result?.analysis_id).toBe(base.analysis_id);
    act(() => result.current.changed());
    expect(result.current.dirty).toBe(true);
    expect(g.cancel).not.toHaveBeenCalled();
  });
  it("does not publish a response after the browser URL changes before the next restore effect", async () => {
    const g = gateway();
    let finish!: (value: typeof base) => void;
    let selected = true;
    g.result.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let pending!: Promise<unknown>;
    act(() => { pending = result.current.restore("#atlas?analysis=first", () => selected); });
    selected = false;
    await act(async () => { finish({ ...base, analysis_id: "first" }); await pending; });
    expect(result.current.result).toBeNull();
    expect(g.proposals).not.toHaveBeenCalled();
  });
  it("rechecks an accepted job, awaits published result GET, and never resubmits it", async () => {
    const g = gateway();
    const running = { ...job("running"), progress: { completed_batches: 88, total_batches: 88, phase: "finalizing" as const } };
    g.job.mockResolvedValueOnce(running).mockResolvedValueOnce(job());
    let finish!: (value: typeof base) => void;
    g.result.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useAnalysisSession(g));
    let restored!: Promise<unknown>;
    act(() => { restored = result.current.restore("#atlas?job=saved-job"); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2100)); });
    expect(result.current.phase).toBe("running");
    expect(result.current.result).toBeNull();
    expect(result.current.job?.status).toBe("succeeded");
    await act(async () => { finish(base); await restored; });
    expect(result.current.phase).toBe("done");
    expect(g.submit).not.toHaveBeenCalled();
  });
  it("rejects scenario-job results in analysis pointers and malformed links before any GET", async () => {
    const g = gateway();
    g.job.mockResolvedValue({ ...job(), analysis_id: null, scenario_run_id: "pending:scenario:one" });
    const { result } = renderHook(() => useAnalysisSession(g));
    await act(() => result.current.restore("#atlas?job=saved-job"));
    expect(result.current.error?.message).toContain("не относится к заданию анализа");
    expect(g.result).not.toHaveBeenCalled();
    g.job.mockClear();
    await act(() => result.current.restore("#atlas?job=a&analysis=b"));
    expect(g.job).not.toHaveBeenCalled();
    expect(g.submit).not.toHaveBeenCalled();
  });
  it("cancels a recovered job only on explicit action and retries by GET", async () => {
    const g = gateway();
    g.job.mockResolvedValue(job("running"));
    g.cancel.mockResolvedValue(job("cancelled"));
    const { result } = renderHook(() => useAnalysisSession(g));
    let restored!: Promise<unknown>;
    act(() => { restored = result.current.restore("#atlas?job=saved-job"); });
    await act(async () => { await Promise.resolve(); });
    expect(g.cancel).not.toHaveBeenCalled();
    await act(() => result.current.cancel());
    await act(async () => { await restored; });
    expect(result.current.phase).toBe("cancelled");
    g.job.mockResolvedValue(job());
    await act(() => result.current.restore("#atlas?job=saved-job"));
    expect(result.current.result).toEqual(base);
    expect(g.submit).not.toHaveBeenCalled();
  });
});
