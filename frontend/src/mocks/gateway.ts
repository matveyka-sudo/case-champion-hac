import type { Params } from "../features/state";
export function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
export const mockGateway = {
  async calculate(params: Params, signal: AbortSignal) {
    const snapshot = structuredClone(params);
    await wait(
      matchMedia("(prefers-reduced-motion: reduce)").matches ? 50 : 1600,
      signal,
    );
    return snapshot;
  },
  async analyze(signal: AbortSignal) {
    await wait(
      matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 900,
      signal,
    );
  },
  async scenario(signal: AbortSignal) {
    await wait(
      matchMedia("(prefers-reduced-motion: reduce)").matches ? 50 : 2200,
      signal,
    );
    return { before: 44, after: 36, categoryBefore: 36.2, categoryAfter: 43.5 };
  },
};
