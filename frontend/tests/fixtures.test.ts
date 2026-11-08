import { expect, it } from "vitest";
import data from "../public/data/demo.json";
it("preserves the approved chain and scenario values without runtime score formulas", () => {
  const c = data.cities.msk.canonCell;
  const v = c.values.pt.c01;
  expect(v.legs.reduce((sum, leg) => sum + Number(leg[1]), 0)).toBe(44);
  expect(v.scenarioLegs.reduce((sum, leg) => sum + Number(leg[1]), 0)).toBe(36);
  expect(v.score).toBeCloseTo(36.18, 2);
  expect(v.scenarioScore).toBeCloseTo(43.53, 2);
  expect(c.values.pt.c04.time).toBe(12);
  expect(c.values.pt.c09.time).toBe(25);
});
it("keeps unknown cells separate from confirmed results", () => {
  expect(
    data.cities.kgd.cells.some((c) => "ndPartial" in c && c.ndPartial),
  ).toBe(true);
  expect(Object.keys(data.cities)).not.toContain("nvr");
});
it("shares a consistent set of nine categories and unique cells", () => {
  expect(data.categories).toHaveLength(9);
  for (const model of Object.values(data.cities)) {
    expect(new Set(model.cells.map((c) => c.id)).size).toBe(model.cells.length);
  }
});
