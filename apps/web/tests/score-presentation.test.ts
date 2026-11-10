import { describe, expect, it } from "vitest";
import { decode } from "../src/api/validation";
import { pointScorePresentation } from "../src/api/adapters/scorePresentation";
import { calculatedPoints, routeEstimatePoints } from "../src/api/adapters/pointOverview";
import resultRaw from "./fixtures/api/urban_passenger.json";

describe("explicit observed-route presentation", () => {
  const fixture = () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const category = { ...result.scores[0], name: "category_index", value: null, status: "unknown" as const };
    const point = { ...category, name: "point_index", score_id: "point-total", subject: { ...category.subject, category: null } };
    const observed = { ...category, name: "observed_route_accessibility", score_id: "observed", value: 0, status: "available" as const, completeness: "partial" as const, ranking_eligible: false, reason_code: "MINIMUM_NOT_PROVEN" };
    result.scores = [category, point, observed];
    return { result, category, point, observed };
  };
  it("shows zero as a separately labelled estimate while canonical point and city totals stay unknown", () => {
    const { result, point, observed } = fixture();
    expect(pointScorePresentation(result, result.scores, point)).toEqual({ score: observed, observed: true });
    expect(calculatedPoints(result).points).toHaveLength(0);
    expect(routeEstimatePoints(result).points.map((value) => value.score.value)).toEqual([0]);
    expect(point.value).toBeNull();
  });
  it("prefers an available exact score and never estimates across categories, modes, or unknown connectors", () => {
    const { result, point, observed } = fixture();
    const known = { ...point, value: 65, status: "available" as const };
    expect(pointScorePresentation(result, [known, observed], known)).toEqual({ score: known, observed: false });
    expect(pointScorePresentation(result, [point, { ...observed, value: null, status: "unknown" }], point).score).toBe(point);
    expect(pointScorePresentation(result, [point, { ...observed, subject: { ...observed.subject, mode: "private_car" } }], point).score).toBe(point);
    if (result.request.analysis_kind !== "urban_passenger") throw Error("fixture");
    result.request.categories.push("airport");
    expect(pointScorePresentation(result, result.scores, point)).toEqual({ score: point, observed: false });
    expect(pointScorePresentation(result, result.scores, point, "school")).toEqual({ score: observed, observed: true });
  });
  it("does not borrow a known category as an unknown general total", () => {
    const { result, point, category, observed } = fixture();
    expect(pointScorePresentation(result, [point, { ...category, status: "available", value: 80 }, observed], point)).toEqual({ score: point, observed: false });
  });
});
