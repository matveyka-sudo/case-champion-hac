import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import training from "../src/mocks/training.json";

type CategoryReading = { s: number; index: number };
type Baseline = {
  id: string; mode?: string; input_sha256: string;
  total?: number; index: number;
  points?: Record<string, { categories: Record<string, CategoryReading> }>;
  sites?: { total_s: number; index: number }[];
};
type Matrix = {
  version: string; seed: number; baseline: Baseline[];
  comparisons: { id: string; operation: string; after: Baseline }[];
};
const matrix = JSON.parse(readFileSync(resolve("../../packages/engine/benchmarks/ml1_demo_matrix.json"), "utf8")) as Matrix;

describe("training displays frozen ML1 output without a client-side formula", () => {
  it("names the source version and keeps all 24 baselines", () => {
    expect(training.version).toBe(matrix.version);
    expect(training.seed).toBe(matrix.seed);
    expect(training.examples).toHaveLength(24);
  });

  it.each(training.examples)("preserves values and source hash for $id $mode", (row) => {
    const source = matrix.baseline.find((item) => item.id === row.id && item.mode === row.mode);
    expect(source).toBeDefined();
    expect(row.inputSha256).toBe(source!.input_sha256);
    if (row.categories) {
      // Urban examples show one selected point/category, never the city aggregate.
      expect(row.times).toEqual([]);
      for (const [category, reading] of Object.entries(row.categories)) {
        const expected = source!.points!.O0.categories[category];
        expect(reading).toEqual({ total: expected.s, index: expected.index });
      }
      if (row.scenario) {
        const scenario = matrix.comparisons.find((item) => item.id === row.id && item.operation === "coordinate_transfer");
        for (const [category, reading] of Object.entries(row.scenario)) {
          const expected = scenario!.after.points!.O0.categories[category];
          expect(reading).toEqual({ total: expected.s, index: expected.index });
        }
      }
    } else {
      const expected = row.id.startsWith("urban-cargo:")
        ? { total: source!.sites![0].total_s, index: source!.sites![0].index }
        : { total: source!.total, index: source!.index };
      expect({ total: row.total, index: row.index }).toEqual(expected);
      expect(row.times.reduce((sum, item) => sum + item.seconds, 0)).toBe(row.total);
    }
  });
});
