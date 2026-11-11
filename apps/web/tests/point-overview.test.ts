import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { calculatedPoints, pointDisplayName } from "../src/api/adapters/pointOverview";
import { CalculatedPoints } from "../src/widgets/CalculatedPoints";
import { pointOverviewFixture } from "./fixtures/point-overview";
afterEach(cleanup);
describe("calculated point overview", () => {
  it("counts distinct overall point indices against sample inventory, not categories or cell features", () => {
    const result = pointOverviewFixture();
    expect(result.scores).toHaveLength(433);
    expect(result.metrics).toHaveLength(217);
    result.map_data!.features!.push({ ...result.map_data!.features![0], entity_id: "duplicate-cell", cell_id: "duplicate-cell" });
    const overview = calculatedPoints(result);
    expect(overview.points).toHaveLength(1);
    expect(overview.denominator).toBe(216);
    expect(overview.points[0].score.name).toBe("point_index");
    expect(overview.points[0].name).toBe("Точка · 54.71500, 20.51500");
    expect(overview.territoryScore?.value).toBeNull();
  });
  it("includes zero, excludes null and never substitutes category scores for an unknown total", () => {
    const result = pointOverviewFixture(3, { 0: 0, 1: null, 2: .497 });
    expect(calculatedPoints(result).points.map((p) => p.pointId)).toEqual(["synthetic-point-0", "synthetic-point-2"]);
    result.scores.find((s) => s.score_id === "synthetic-total-2")!.value = null;
    result.scores.find((s) => s.score_id === "synthetic-total-2")!.status = "unknown";
    expect(calculatedPoints(result).points.map((p) => p.pointId)).toEqual(["synthetic-point-0"]);
    result.scores = result.scores.filter((s) => s.subject.category != null);
    result.scores.push({ ...result.scores[0], score_id: "another-category", value: 90, status: "available", subject: { ...result.scores[0].subject, category: "school" } });
    expect(calculatedPoints(result).points).toEqual([]);
  });
  it("requires the exact requested area total and never relabels the city or another area's score", () => {
    const result = pointOverviewFixture(1, { 0: 0 });
    if ("city_id" in result.request) result.request.area_id = "synthetic-area";
    const city = result.scores.find((score) => score.name === "territory_index")!;
    city.value = 85; city.status = "available";
    result.scores.push({ ...city, score_id: "other-area-total", subject: { ...city.subject, territory_id: "other-area" } });
    expect(calculatedPoints(result).territoryScore).toBeNull();
    const area: typeof city = { ...city, score_id: "requested-area-total", value: null, status: "unknown",
      subject: { ...city.subject, territory_id: "synthetic-area" } };
    result.scores.push(area);
    expect(calculatedPoints(result).territoryScore).toBe(area);
    expect(calculatedPoints(result).territoryScore?.value).toBeNull();
    area.value = 0; area.status = "available";
    expect(calculatedPoints(result).territoryScore?.value).toBe(0);
    result.scores.push({ ...area, score_id: "duplicate-area-total" });
    expect(calculatedPoints(result).territoryScore).toBeNull();
  });
  it("does not invent an inventory denominator or coordinates from cells alone", () => {
    const result = pointOverviewFixture(2, { 1: .497 });
    result.map_data!.features = result.map_data!.features!.filter((f) => f.entity_kind === "cell");
    const overview = calculatedPoints(result);
    expect(overview.denominator).toBeNull();
    expect(overview.inventoryCount).toBeNull();
    expect(overview.points[0].name).toContain("координаты не предоставлены");
    expect(overview.points[0].name).not.toContain("synthetic-point");
  });
  it("requires inventory for unknown and duplicate point-total tasks as well as numeric points", () => {
    const result = pointOverviewFixture(2, { 1: .497 });
    result.map_data!.features = result.map_data!.features!.filter((f) => f.entity_kind === "sample_point" && f.entity_id === "synthetic-point-1");
    const unknown = result.scores.find((s) => s.score_id === "synthetic-total-0")!;
    result.scores.push({ ...unknown, score_id: "duplicate-unknown-total" });
    const overview = calculatedPoints(result);
    expect(overview.points).toHaveLength(1);
    expect(overview.inventoryCount).toBe(1);
    expect(overview.denominator).toBeNull();
    render(createElement(CalculatedPoints, { overview, selection: null, areaSelected: false, onSelect: vi.fn() }));
    expect(screen.getByText("Точки с общим индексом (1)")).toBeTruthy();
    expect(screen.getByText("Переданная карта содержит не все точки из результата. Соотношение не определено.")).toBeTruthy();
  });
  it("uses accepted names and coordinates instead of technical identifiers", () => {
    const result = pointOverviewFixture(1, { 0: 0 });
    const feature = result.map_data!.features!.find((f) => f.entity_kind === "sample_point")!;
    expect(pointDisplayName(feature.entity_id, { ...feature, name: "Северная точка" })).toBe("Северная точка");
    expect(pointDisplayName(feature.entity_id, { ...feature, name: feature.entity_id })).toBe("Точка · 54.50000, 20.30000");
  });
  it("keeps city null visible and closes the native overview after explicit point selection", async () => {
    const user = userEvent.setup();
    const overview = calculatedPoints(pointOverviewFixture(2, { 1: 0 }));
    const onSelect = vi.fn();
    const view = render(createElement(CalculatedPoints, { overview, selection: null, areaSelected: false, onSelect }));
    expect(screen.getByText(/Индекс города:/).textContent).toContain("Нет данных");
    expect(onSelect).not.toHaveBeenCalled();
    const detail = view.container.querySelector("details")!;
    await user.click(screen.getByText("Точки с общим индексом (1 из 2)"));
    expect(detail.open).toBe(true);
    expect(onSelect).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Точка · 54.50100, 20.30100/ }));
    expect(onSelect).toHaveBeenCalledWith({ entity_kind: "sample_point", entity_id: "synthetic-point-1" });
    expect(screen.getByText(/Индекс города:/).textContent).toContain("Нет данных");
    expect(detail.open).toBe(false);
    view.rerender(createElement(CalculatedPoints, { overview, selection: { entity_kind: "sample_point", entity_id: "synthetic-point-1" }, areaSelected: false, onSelect }));
    expect(detail.open).toBe(false);
  });
});
