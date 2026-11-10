import { createElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExplanationFinding } from "../src/widgets/ExplanationFinding";
import { decode } from "../src/api/validation";
import type { Schemas } from "../src/api/types";
import resultRaw from "./fixtures/api/urban_passenger.json";
afterEach(cleanup);
describe("saved score explanation references", () => {
  it.each([
    [0, "low"], [49.9, "low"], [50, "medium"], [74.9, "medium"], [75, "high"], [100, "high"], [null, "unknown"],
  ] as const)("renders score-only findings from saved value %s with band %s", (value, band) => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    result.scores[0] = {
      ...result.scores[0], name: "urban_accessibility_index", value,
      status: value === null ? "unknown" : "available",
    };
    const finding = decode("Finding", {
      text: "Сохранённый индекс.", metric_ids: [], claims: [], evidence_refs: [],
      score_ids: [result.scores[0].score_id],
      score_claims: [{ score_id: result.scores[0].score_id, value: 99 }],
    });
    render(createElement(ExplanationFinding, { finding, result, onSelect: vi.fn() }));
    const button = screen.getByRole("button");
    expect(button.getAttribute("data-score-band")).toBe(band);
    expect(button.textContent).toContain(value === null ? "Нет данных" : `${String(value).replace(".", ",")} из 100`);
    expect(button.textContent).not.toContain("99 из 100");
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });
  it("keeps partial numeric scores labeled and links to their saved map subject", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const score = result.scores[0];
    score.completeness = "partial";
    score.ranking_eligible = false;
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: score.subject.point_id!, territory_id: "city-1",
      geometry: null, geometry_kind: "none", source_refs: [],
    }] };
    const finding: Schemas["Finding"] = {
      text: "Частичный результат.", metric_ids: [], claims: [], evidence_refs: [],
      score_claims: [{ score_id: score.score_id, value: null }],
    };
    const onSelect = vi.fn();
    render(createElement(ExplanationFinding, { finding, result, onSelect }));
    expect(screen.getByRole("button").textContent).toContain("50 из 100 · частичная оценка");
    fireEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith({ entity_kind: "sample_point", entity_id: score.subject.point_id });
    expect(score.ranking_eligible).toBe(false);
  });
  it("does not invent missing scores and preserves existing metric references", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const finding: Schemas["Finding"] = {
      text: "Метрика и отсутствующий индекс.", metric_ids: [result.metrics[0].metric_id],
      claims: [{ metric_id: result.metrics[0].metric_id, value: 999, unit: "seconds" }],
      evidence_refs: [], score_ids: ["missing-score"], score_claims: [{ score_id: "missing-score", value: 100 }],
    };
    render(createElement(ExplanationFinding, { finding, result, onSelect: vi.fn() }));
    expect(screen.getByText("Индекс недоступен")).toBeTruthy();
    expect(screen.getByRole("button").textContent).toContain("30 мин");
    expect(screen.getByRole("button").getAttribute("data-score-band")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });
});
