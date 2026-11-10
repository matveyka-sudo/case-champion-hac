import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { initialDraft, toRequest } from "../src/api/adapters/request";
import { sameUrbanRequest, urbanReadiness } from "../src/api/adapters/urbanReadiness";
import { UrbanReadiness } from "../src/widgets/UrbanReadiness";
import type { Schemas } from "../src/api/types";
import { decode } from "../src/api/validation";
import { legGeometryFixture } from "./fixtures/leg-geometry";
import { airportScope, scopedCapabilities } from "./fixtures/urban-scopes";
import territories from "./fixtures/api/territories.json";
const catalog = territories.map((territory) => decode("TerritoryOption", territory));
const draft = () => ({ ...initialDraft(), cityId: "city-2", date: "2026-11-10", travel: "walk" as const, cats: { c08: 2 } });
function context() {
  const current = draft(), caps = scopedCapabilities(), result = legGeometryFixture(0);
  const request = toRequest(current, catalog, caps) as Schemas["UrbanPassengerRequest"];
  result.request = request;
  return { current, caps, result, request };
}
afterEach(cleanup);
describe("contextual urban readiness facts", () => {
  it("separates published input metadata and registered launch from unknown output completeness", () => {
    const { current, caps } = context();
    const value = urbanReadiness(current, catalog, caps, null, false);
    expect(value).toMatchObject({ matchingScopes: 1, saved: null, overview: null, populationBasis: null });
    expect(value.snapshot?.snapshot_id).toBe("demo-snapshot");
    render(createElement(UrbanReadiness, { value }));
    expect(screen.getByText(/Параметры запуска зарегистрированы/)).toBeTruthy();
    expect(screen.getByText("Полнота данных о населении не подтверждена.")).toBeTruthy();
    expect(screen.getByText("Полнота каталога не подтверждена.")).toBeTruthy();
    expect(screen.getAllByText("Для этих условий результат ещё не получен.")).toHaveLength(2);
  });
  it("counts unique category-free numeric point totals, keeps zero, and preserves the unknown city", () => {
    const { current, caps, result } = context();
    result.map_data!.features!.push(structuredClone(result.map_data!.features![4]));
    const value = urbanReadiness(current, catalog, caps, result, false);
    expect(value.overview?.points).toHaveLength(1);
    expect(value.overview?.denominator).toBe(3);
    expect(value.overview?.points[0].score.value).toBe(0);
    expect(value.overview?.territoryScore?.value).toBeNull();
    render(createElement(UrbanReadiness, { value }));
    expect(screen.getByText(/Рассчитано точек: 1 из 3/)).toBeTruthy();
    expect(screen.getByText("0 из 100").getAttribute("data-score-band")).toBe("low");
    expect(screen.getByText("Нет данных").getAttribute("data-score-band")).toBe("unknown");
  });
  it.each([
    { city_id: "city-1" }, { passenger_mode: "public_transport" }, { traffic_mode: "typical" },
    { departure_at: "2026-11-10T08:45:00+02:00" }, { snapshot_id: "other-snapshot" },
    { area_id: "other-area" }, { categories: ["school"] }, { priorities: { airport: "high" } },
  ] as Partial<Schemas["UrbanPassengerRequest"]>[])("does not reuse saved output under a changed request tuple %j", (patch) => {
    const { result, request } = context();
    expect(sameUrbanRequest(result, { ...request, ...patch })).toBe(false);
  });
  it("compares departure instants and unordered categories/priorities, while dirty state still excludes output", () => {
    const { current, caps, result, request } = context();
    request.categories = ["airport", "school"];
    request.priorities = { airport: "medium", school: "high" };
    expect(sameUrbanRequest(result, { ...request, departure_at: "2026-11-10T06:30:00Z", categories: ["school", "airport"], priorities: { school: "high", airport: "medium" } })).toBe(true);
    expect(urbanReadiness(current, catalog, caps, result, true).saved).toBeNull();
    expect(urbanReadiness({ ...current, cityId: "city-1" }, catalog, caps, result, false).saved).toBeNull();
  });
  it("never substitutes category-only values for unknown overall totals or infers population from map samples", () => {
    const { current, caps, result } = context();
    const total = result.scores.find((score) => score.score_id === "synthetic-total-2")!;
    total.value = null; total.status = "unknown";
    result.map_data!.features![1].population = 123;
    const value = urbanReadiness(current, catalog, caps, result, false);
    expect(value.overview?.points).toEqual([]);
    expect(value.populationBasis).toBeNull();
  });
  it("exposes only explicitly supplied population basis including zero, not whole-city completeness", () => {
    const { current, caps, result } = context();
    result.scores.find((score) => score.name === "territory_index")!.aggregation_basis = {
      unit: "persons", population: 0, coverage: 0, year: 2025, population_method: "synthetic", unknown_positive_weight: true,
    };
    render(createElement(UrbanReadiness, { value: urbanReadiness(current, catalog, caps, result, false) }));
    expect(screen.getByText(/В основе индекса: 0 чел.; охват 0%; есть неизвестные веса/)).toBeTruthy();
    expect(screen.getByText("Полнота каталога не подтверждена.")).toBeTruthy();
  });
  it("keeps ambiguous and legacy scope detail distinct without publishing inferred launch readiness", () => {
    const { current, caps, result } = context();
    caps.urban_passenger_scopes!.push({ ...airportScope(), scope_id: "overlap" });
    expect(urbanReadiness(current, catalog, caps, result, false)).toMatchObject({ matchingScopes: 2, saved: null });
    caps.urban_passenger_scopes = null;
    const value = urbanReadiness(current, catalog, caps, null, false);
    render(createElement(UrbanReadiness, { value }));
    expect(screen.getByText("Детализация запуска сервером не предоставлена.")).toBeTruthy();
  });
});
