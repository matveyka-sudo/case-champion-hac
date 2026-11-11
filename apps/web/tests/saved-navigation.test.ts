import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackendGateway } from "../src/api/gateway";
import { decode } from "../src/api/validation";
import { useAnalysisSession } from "../src/features/analysis/useAnalysisSession";
import { savedPointerWriter } from "../src/features/analysis/savedPointer";
import resultRaw from "./fixtures/api/urban_passenger.json";
const baseline = decode("AnalysisResult", resultRaw);
afterEach(() => { cleanup(); history.replaceState(null, "", "/"); });
describe("navigation before the restore effect", () => {
  it.each(["accepted", "result"] as const)("rejects a late %s callback before React can invalidate the previous revision", async (stage) => {
    history.replaceState(null, "", "#atlas?analysis=old");
    const pending = Promise.withResolvers<void>();
    let signal!: AbortSignal;
    const g = Object.assign(new BackendGateway(), { cancel: vi.fn(), proposals: vi.fn().mockResolvedValue([]),
      analyze: vi.fn(async (_request, _key, currentSignal: AbortSignal, onJob) => {
        signal = currentSignal;
        if (stage === "accepted") await pending.promise;
        onJob({ job_id: "new-job", status: "queued", request_id: "trace", poll_after_seconds: 1 });
        if (stage === "result") await pending.promise;
        return baseline;
      }) });
    const { result } = renderHook(() => useAnalysisSession(g));
    const written = vi.fn();
    let operation!: Promise<void>;
    act(() => { operation = result.current.run(baseline.request, savedPointerWriter(written)); });
    if (stage === "result") expect(location.hash).toBe("#atlas?job=new-job");
    // Intentionally no hash event, changed(), restore(), or timer: this is the
    // exact gap before the URL-driven effect can increment the hook revision.
    history.replaceState(null, "", "#atlas?analysis=another");
    await act(async () => { pending.resolve(); await operation; });
    expect(location.hash).toBe("#atlas?analysis=another");
    expect(signal.aborted).toBe(true);
    expect(result.current.result).toBeNull();
    expect(result.current.error).toBeNull();
    expect(g.cancel).not.toHaveBeenCalled();
    expect(written).toHaveBeenCalledTimes(stage === "accepted" ? 0 : 1);
  });
});
