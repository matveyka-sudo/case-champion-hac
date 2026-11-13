import { describe, expect, it } from "vitest";
import { explanationLabel } from "../src/api/adapters/explanationStatus";
import { explanationSources } from "../src/api/adapters/evidenceGroups";
import type { Schemas } from "../src/api/types";
import urban from "./fixtures/api/urban_passenger.json";

const data = urban as Schemas["AnalysisResult"];
const answer = (extra: Partial<Schemas["Explanation"]>): Schemas["Explanation"] => ({
  schema_version: "2", analysis_id: data.analysis_id, analysis_result_hash: data.result_hash, request_id: "synthetic-explanation",
  status: "generated", model_id: "synthetic-model", reason_code: null, proposals: [], limitations: [], evidence_refs: [], retrieved_evidence: [],
  findings: [{ text: "Synthetic finding", metric_ids: [data.metrics[0].metric_id], claims: [], evidence_refs: [] }], ...extra,
});

describe("explanation status shown to the user", () => {
  it("names a checked model answer and an honest template with its reason", () => {
    expect(explanationLabel({ status: "generated", reason_code: null })).toBe("Объяснение модели");
    expect(explanationLabel({ status: "template", reason_code: "TIMEOUT" })).toBe("Шаблонное объяснение: модель не ответила вовремя");
    expect(explanationLabel({ status: "template", reason_code: "INVALID_EXPLANATION" })).toBe("Шаблонное объяснение: ответ модели не прошёл проверку");
    expect(explanationLabel({ status: "template", reason_code: "BUDGET_EXCEEDED" })).toBe("Шаблонное объяснение: лимит расходов на модель исчерпан");
    expect(explanationLabel({ status: "template", reason_code: "PROVIDER_UNAVAILABLE" })).toBe("Шаблонное объяснение: сервис модели недоступен");
  });

  it("never invents a reason for an unknown code", () => {
    expect(explanationLabel({ status: "template", reason_code: "FUTURE_CODE" })).toBe("Шаблонное объяснение");
  });
});

describe("document citations of a generated answer", () => {
  const saved = data.evidence_refs[0];

  it("accepts a retrieved document that is the saved leaf itself and shows it once", () => {
    // The server sends the saved leaf unchanged when a found document is already cited by the result.
    const cited = answer({ retrieved_evidence: [saved], findings: [{ text: "Synthetic finding", metric_ids: [data.metrics[0].metric_id], claims: [], evidence_refs: [saved.evidence_id] }], evidence_refs: [saved.evidence_id] });
    const shown = explanationSources(data, cited);
    expect(shown.filter((item) => item.evidence_id === saved.evidence_id)).toEqual([saved]);
  });

  it("accepts a new document with several sections in one reference", () => {
    const document = { ...saved, evidence_id: "retrieved-document", section: "Раздел A; Раздел B", observed_at: null };
    const cited = answer({ retrieved_evidence: [document], findings: [{ text: "Synthetic finding", metric_ids: [data.metrics[0].metric_id], claims: [], evidence_refs: ["retrieved-document"] }] });
    expect(explanationSources(data, cited).map((item) => item.evidence_id)).toContain("retrieved-document");
  });

  it("still rejects a changed section, a duplicate document and a citation without a source", () => {
    expect(() => explanationSources(data, answer({ retrieved_evidence: [{ ...saved, section: "Другой раздел" }] }))).toThrow();
    const document = { ...saved, evidence_id: "retrieved-document" };
    expect(() => explanationSources(data, answer({ retrieved_evidence: [document, document] }))).toThrow();
    expect(() => explanationSources(data, answer({ findings: [{ text: "Synthetic finding", metric_ids: [data.metrics[0].metric_id], claims: [], evidence_refs: ["missing-document"] }] }))).toThrow();
  });
});
