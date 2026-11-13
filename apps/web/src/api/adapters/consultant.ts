import type { ApiClient } from "../client";
import type { Schemas } from "../types";

export type ConsultantContext = Omit<Schemas["ConsultantQuestion"], "question">;

/** Questions without a saved analysis: the address page and the atlas before a run. */
export function askConsultant(client: ApiClient, question: string, context: ConsultantContext, signal?: AbortSignal) {
  return client.request("/consultant/ask", "ConsultantAnswer", {
    body: { question, ...context },
    signal,
    timeoutMs: 60000,
  });
}

/** Route geometry is not needed to answer and would only enlarge the request. */
export function compactAddress(result: Schemas["AddressResult"] | null) {
  return result ? { ...result, categories: result.categories.map((item) => ({ ...item, path: [] })) } : null;
}

export function compactDistrict(result: Schemas["DistrictResult"] | null) {
  return result ? { ...result, boundary: null } : null;
}
