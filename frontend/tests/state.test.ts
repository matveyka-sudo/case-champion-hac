import { describe, it, expect, vi } from "vitest";
import { initialState, reducer } from "../src/features/state";
import { mockGateway } from "../src/mocks/gateway";
import { timeLabel } from "../src/shared/format";
describe("atlas state", () => {
  it("marks old results stale and preserves their conditions", () => {
    const s = initialState(),
      next = reducer(s, { type: "params", patch: { t: 80 } });
    expect(next.status).toBe("stale");
    expect(next.applied.t).toBe(34);
    expect(next.p.t).toBe(80);
  });
  it("ignores responses to superseded conditions", () => {
    const s = initialState(),
      changed = reducer(s, { type: "params", patch: { date: "2026-11-11" } });
    expect(
      reducer(changed, { type: "complete", revision: s.revision, params: s.p }),
    ).toBe(changed);
  });
  it("distinguishes unsupported dates from unavailable demo slots", () => {
    const s = initialState();
    expect(
      reducer(s, {
        type: "complete",
        revision: 0,
        params: { ...s.p, date: "2026-12-10" },
      }).status,
    ).toBe("baddate");
    expect(
      reducer(s, { type: "complete", revision: 0, params: { ...s.p, t: 80 } })
        .status,
    ).toBe("demoonly");
  });
  it("switches cities and modes without retaining previous scenario", () => {
    let s = initialState();
    s = { ...s, scenario: "done", scnView: "scn" };
    const changed = reducer(s, { type: "city", city: "kgd" });
    expect(changed.status).toBe("partial");
    expect(changed.scenario).toBe("none");
    expect(reducer(changed, { type: "mode", mode: "cargo" }).applied.mode).toBe(
      "cargo",
    );
  });
  it("resets to the deterministic baseline", () => {
    const s = reducer(initialState(), {
      type: "params",
      patch: { t: 0, date: "2026-11-21" },
    });
    const next = reducer(s, { type: "reset" });
    expect(next.p.t).toBe(34);
    expect(next.applied.date).toBe("2026-11-10");
  });
  it("formats all 96 slots including day boundaries", () => {
    expect(timeLabel(0)).toBe("00:00");
    expect(timeLabel(95)).toBe("23:45");
    expect(
      new Set(Array.from({ length: 96 }, (_, i) => timeLabel(i))).size,
    ).toBe(96);
  });
});
describe("mock gateway cancellation", () => {
  it("does not publish a cancelled computation", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const controller = new AbortController();
    const promise = mockGateway.calculate(initialState().p, controller.signal);
    const assertion = expect(promise).rejects.toMatchObject({
      name: "AbortError",
    });
    controller.abort();
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  it("captures conditions before asynchronous work", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const p = initialState().p;
    const promise = mockGateway.calculate(p, new AbortController().signal);
    p.t = 80;
    await vi.runAllTimersAsync();
    expect((await promise).t).toBe(34);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
