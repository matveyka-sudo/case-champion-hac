import { describe, expect, it } from "vitest";
import { partialWalkGeometryFixture } from "./fixtures/partial-walk-geometry";
import { measureText } from "../src/api/adapters/result";

describe("partial walk journey display contract", () => {
  it("keeps the exact index operand separate from the rounded linked display timeline", () => {
    const result = partialWalkGeometryFixture();
    const exact = result.metrics.find((m) => m.name === "minimum_journey_seconds" && m.value !== null)!;
    const display = result.metrics.find((m) => m.name === "total_seconds")!;
    const journey = result.journeys[0];
    expect(exact.journey_id).toBeNull();
    expect(exact.value).toBe(600.123456789);
    expect(display.journey_id).toBe(journey.journey_id);
    expect(display.value).toBe(journey.total_seconds);
    expect(journey).toMatchObject({ completeness: "partial", geometry_kind: "actual", legs: [{ kind: "walk", mode: "walk" }] });
    const category = result.scores.find((s) => s.score_id === "synthetic-category-2")!;
    expect(category.components).toContainEqual({ kind: "metric", id: exact.metric_id });
    expect(category.components).not.toContainEqual({ kind: "metric", id: display.metric_id });
    measureText(exact);
    measureText(display);
    expect(exact.value).toBe(600.123456789);
  });
});
