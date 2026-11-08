import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import manifest from "../public/data/demo/index.json";
import type { Fixtures, Cell } from "../src/mocks/model";
const cities = Object.fromEntries(
  Object.entries(manifest.cities).map(([key, descriptor]) => {
    const { parts, canonCellId, ...metadata } = descriptor;
    const cells = parts.flatMap(
      (name) =>
        JSON.parse(
          readFileSync(
            resolve(process.cwd(), "public/data/demo", name),
            "utf8",
          ),
        ) as Cell[],
    );
    return [
      key,
      {
        ...metadata,
        cells,
        canonCell: cells.find((c) => c.id === canonCellId) || null,
      },
    ];
  }),
);
const data = { ...manifest.catalog, cities } as unknown as Fixtures;
it("preserves the approved chain and scenario values without runtime score formulas", () => {
  const c = data.cities.msk!.canonCell!;
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
    data.cities.kgd!.cells.some((c) => "ndPartial" in c && c.ndPartial),
  ).toBe(true);
  expect(Object.keys(data.cities)).not.toContain("nvr");
});
it("shares a consistent set of nine categories and unique cells", () => {
  expect(data.categories).toHaveLength(9);
  for (const model of Object.values(data.cities)) {
    expect(new Set(model!.cells.map((c) => c.id)).size).toBe(
      model!.cells.length,
    );
  }
});
