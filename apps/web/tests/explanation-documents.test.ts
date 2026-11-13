import { describe, expect, it } from "vitest";
import { explanationSources } from "../src/api/adapters/evidenceGroups";
import type { Schemas } from "../src/api/types";
// Written by packages/assistant/tests/test_web_citation_fixture.py from real backend answers.
import fixture from "./fixtures/api/explanation-documents.json";

const result = fixture.result as unknown as Schemas["AnalysisResult"];
const fresh = fixture.new_document as unknown as Schemas["Explanation"];
const reused = fixture.saved_document as unknown as Schemas["Explanation"];
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

describe("backend → browser citation roundtrip", () => {
  it("shows a new multi-section document once, beside the saved source", () => {
    const shown = explanationSources(result, fresh);
    expect(shown.map((item) => item.evidence_id).sort()).toEqual(["demo-evidence", "strategy-document"]);
    expect(shown.find((item) => item.evidence_id === "strategy-document")?.section).toBe("Раздел A; Раздел B; Раздел C");
    expect(fresh.findings[0].evidence_refs).toContain("strategy-document");
  });

  it("accepts a found section of a saved source because the saved record comes back unchanged", () => {
    const shown = explanationSources(result, reused);
    expect(shown).toEqual([result.evidence_refs[0]]);
  });

  it("rejects a retrieved record that changes a saved source", () => {
    const forged = clone(reused);
    forged.retrieved_evidence![0] = { ...forged.retrieved_evidence![0], section: "Другой раздел" };
    expect(() => explanationSources(result, forged)).toThrow();
    const moved = clone(reused);
    moved.retrieved_evidence![0] = { ...moved.retrieved_evidence![0], url: "https://example.org/forged" };
    expect(() => explanationSources(result, moved)).toThrow();
  });

  it("rejects a duplicated document and a citation of a document that was not returned", () => {
    const twice = clone(fresh);
    twice.retrieved_evidence = [...twice.retrieved_evidence!, twice.retrieved_evidence![0]];
    expect(() => explanationSources(result, twice)).toThrow();
    const foreign = clone(fresh);
    foreign.findings[0].evidence_refs = [...foreign.findings[0].evidence_refs, "foreign-document"];
    expect(() => explanationSources(result, foreign)).toThrow();
  });

  it("rejects an answer bound to another saved result", () => {
    expect(() => explanationSources(result, { ...clone(fresh), analysis_result_hash: "other" })).toThrow();
  });
});
