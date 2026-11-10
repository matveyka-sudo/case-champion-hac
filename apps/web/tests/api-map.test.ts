import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiMap } from "../src/widgets/ApiMap";
import { decode } from "../src/api/validation";
import resultRaw from "./fixtures/api/urban_passenger.json";
import { scenarioJourneys } from "../src/api/adapters/scenario";
import { scenarioRouteFixture } from "./fixtures/scenario-routes";
const drawn = vi.hoisted(() => ({
  layers: [] as { style?: Record<string, unknown>; marker?: Record<string, unknown>; tooltip: string; geometry?: unknown }[],
  pans: [] as unknown[], zooms: [] as unknown[],
  commands: [] as string[],
}));
vi.mock("leaflet", () => ({
  default: {
    map: () => {
      const map = { fitBounds: () => { drawn.commands.push("home"); return map; }, invalidateSize: vi.fn(), remove: vi.fn(), getZoom: () => 10,
        zoomIn: () => drawn.commands.push("in"), zoomOut: () => drawn.commands.push("out"),
        setZoomAround: (location: unknown, zoom: number) => drawn.zooms.push({ location, zoom }),
        panInside: (location: unknown) => drawn.pans.push(location) };
      return map;
    },
    tileLayer: () => ({ on: () => ({ addTo: vi.fn() }) }),
    layerGroup: () => ({ addTo: () => ({ clearLayers: vi.fn() }) }),
    latLngBounds: () => ({ extend: vi.fn(), isValid: () => false }),
    circleMarker: (_position: unknown, style: Record<string, unknown>) => {
      drawn.layers.at(-1)!.marker = style;
      return {};
    },
    geoJSON: (feature: { type: string; geometry?: unknown }, options: {
      style?: Record<string, unknown>;
      pointToLayer?: (feature: unknown, point: unknown) => unknown;
    }) => {
      const item = { style: options.style, tooltip: "", geometry: feature.type === "Feature" ? feature.geometry : feature };
      drawn.layers.push(item);
      options.pointToLayer?.({}, {});
      const layer = {
        bindTooltip: (tip: HTMLElement) => { item.tooltip = tip.textContent || ""; return layer; },
        on: () => layer,
        addTo: () => layer,
        getBounds: () => ({}),
      };
      return layer;
    },
  },
}));
beforeEach(() => {
  drawn.layers.length = 0;
  drawn.pans.length = 0;
  drawn.zooms.length = 0;
  drawn.commands.length = 0;
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
describe("explicit calculated-point focus", () => {
  it.each(["in", "out", "home"])("consumes the %s command across later selection/result renders", (kind) => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const props = { result, scores: result.scores, onSelect: vi.fn() };
    const view = render(createElement(ApiMap, { ...props, selection: null, zoomAction: { id: 0, kind: "home" } }));
    view.rerender(createElement(ApiMap, { ...props, selection: null, zoomAction: { id: 1, kind } }));
    const commands = [...drawn.commands];
    expect(commands.at(-1)).toBe(kind);
    view.rerender(createElement(ApiMap, { ...props, result: structuredClone(result), selection: { entity_kind: "sample_point", entity_id: "demo-point" }, zoomAction: { id: 1, kind } }));
    expect(drawn.commands).toEqual(commands);
    expect(drawn.pans).toEqual([]);
    expect(drawn.zooms).toEqual([]);
  });
  it("highlights and reveals a selected point only after a focus action", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: "demo-point", territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }] };
    const props = { result, scores: result.scores, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, { ...props, selection: null }));
    expect(drawn.pans).toEqual([]);
    const selection = { entity_kind: "sample_point" as const, entity_id: "demo-point" };
    view.rerender(createElement(ApiMap, { ...props, selection }));
    expect(drawn.pans).toEqual([]);
    expect(drawn.layers.at(-1)?.marker?.color).toBe("#2f5bff");
    view.rerender(createElement(ApiMap, { ...props, selection, zoomAction: { id: 1, kind: "selection" } }));
    expect(drawn.layers.at(-1)?.marker?.color).toBe("#2f5bff");
    expect(drawn.pans).toEqual([[55, 37]]);
    expect(drawn.zooms).toEqual([{ location: [55, 37], zoom: 12 }]);
  });
  it("does not invent a position when the selected point geometry is absent", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const props = { result, scores: result.scores, onSelect: vi.fn(), selection: { entity_kind: "sample_point" as const, entity_id: "missing-point" } };
    const view = render(createElement(ApiMap, { ...props, zoomAction: { id: 0, kind: "home" } }));
    view.rerender(createElement(ApiMap, { ...props, zoomAction: { id: 1, kind: "selection" } }));
    expect(drawn.pans).toEqual([]);
    expect(drawn.zooms).toEqual([]);
  });
});
describe("scenario map paths", () => {
  it("draws the changed nonwinner and reused route, then restores only baseline lines", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    const routes = scenarioJourneys(baseline, scenario).journeys;
    const props = { result: baseline, scores: scenario.after.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, { ...props, journeys: routes, scenarioView: true, viewId: scenario.run_id }));
    const lines = () => drawn.layers.filter((l) => (l.geometry as { type?: string })?.type === "LineString");
    expect(lines().map((l) => l.geometry)).toEqual(routes.map((j) => j.geometry));
    expect(lines().every((l) => l.tooltip.includes("сценария"))).toBe(true);
    expect(lines().map((l) => l.geometry)).not.toContainEqual(baseline.journeys[1].geometry);
    drawn.layers.length = 0;
    view.rerender(createElement(ApiMap, { ...props, journeys: baseline.journeys, scenarioView: false }));
    expect(lines().map((l) => l.geometry)).toEqual(baseline.journeys.map((j) => j.geometry));
    expect(lines().every((l) => !l.tooltip.includes("сценария"))).toBe(true);
  });
  it("does not draw a baseline polyline when the scenario chain has no geometry", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    scenario.after.metrics = [scenario.after.metrics[1]];
    scenario.after_journeys![0].geometry = null;
    scenario.after_journeys![0].geometry_kind = "none";
    render(createElement(ApiMap, {
      result: baseline, scores: scenario.after.scores, selection: null, onSelect: vi.fn(),
      zoomAction: { id: 0, kind: "home" }, scenarioView: true,
      journeys: scenarioJourneys(baseline, scenario).journeys,
    }));
    expect(drawn.layers.some((l) => (l.geometry as { type?: string })?.type === "LineString")).toBe(false);
    expect(baseline.journeys.every((j) => j.geometry !== null)).toBe(true);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("map score completeness", () => {
  it.each([false, true])("prefers the canonical category-free point total in either candidate order (%s)", (reverse) => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const category = { ...result.scores[0], name: "category_index", value: 0.497, calculation_kind: null, subject: { ...result.scores[0].subject, territory_id: "city-1" } };
    const point = {
      ...category, name: "point_index", score_id: "point-total",
      subject: { ...category.subject, category: null },
    };
    const city = { ...point, name: "territory_index", score_id: "city-total", value: null, status: "unknown" as const, subject: { kind: "territory" as const, territory_id: "city-1" } };
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: point.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }, {
      entity_kind: "territory", entity_id: "city-1", territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37.1, 55.1] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }] };
    const props = { result, scores: [city, ...(reverse ? [point, category] : [category, point])], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, props));
    expect(drawn.layers[0].marker?.fillColor).toBe("#c72d44");
    expect(drawn.layers[0].tooltip).toContain("0.5");
    expect(drawn.layers[1].marker?.fillColor).toBe("#8990a7");
    expect(drawn.layers[1].tooltip).toContain("нет индекса");
    // A conflicting category makes accidental category-first selection observable.
    drawn.layers.length = 0;
    view.rerender(createElement(ApiMap, { ...props, scores: [city, { ...category, value: 90 }, point] }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#c72d44");
    expect(drawn.layers[0].tooltip).toContain("0.5");
  });
  it("does not replace an unknown point total with its known category", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const category = { ...result.scores[0], name: "category_index", value: 90 };
    const point = { ...category, name: "point_index", score_id: "point-total", status: "unknown" as const, value: null, subject: { ...category.subject, category: null } };
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: point.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }] };
    render(createElement(ApiMap, {
      result, scores: [category, point], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#8990a7");
    expect(drawn.layers[0].tooltip).toContain("нет индекса");
    expect(drawn.layers[0].tooltip).not.toContain("90.0");
  });
  it("leaves multiple categories neutral when no total exists", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const category = { ...result.scores[0], name: "category_index", value: 25 };
    const other = { ...category, score_id: "other-category", value: 90, subject: { ...category.subject, category: "adult_polyclinic" as const } };
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: category.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }] };
    render(createElement(ApiMap, {
      result, scores: [category, other], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#8990a7");
    expect(drawn.layers[0].tooltip).toContain("нет индекса");
  });
  it("retains explicit aggregate selection for intercity connections", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const mode = { ...result.scores[0], value: 10, subject: { kind: "city_pair" as const, origin_id: "city-1", destination_id: "city-2", mode: "rail" as const } };
    const total = { ...mode, score_id: "connection-total", value: 80, calculation_kind: "aggregate" as const };
    result.map_data = { status: "available", features: [{
      entity_kind: "territory", entity_id: "city-1", territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"],
    }] };
    render(createElement(ApiMap, {
      result, scores: [mode, total], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#0b8a5c");
    expect(drawn.layers[0].tooltip).toContain("80.0");
  });
  it("keeps a partial numeric point colored when the city aggregate is unknown", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const point = { ...result.scores[0], value: 72.5, completeness: "partial" as const };
    const city = { ...point, score_id: "city-total", value: null, status: "unknown" as const, subject: { kind: "territory" as const, territory_id: "city-1" } };
    result.map_data = { status: "available", completeness: "partial", features: [{
      entity_kind: "sample_point", entity_id: point.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: [],
    }] };
    render(createElement(ApiMap, {
      result, scores: [city, point], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#d49520");
    expect(drawn.layers[0].tooltip).toContain("72.5 · частичная оценка");
  });
  it.each([
    [0, "#c72d44"], [0.497, "#c72d44"], [49.9, "#c72d44"], [50, "#d49520"],
    [74.9, "#d49520"], [75, "#0b8a5c"], [100, "#0b8a5c"],
  ] as const)("colors the saved numeric index %s using the approved band", (value, color) => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const score = { ...result.scores[0], value };
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: score.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: [],
    }] };
    render(createElement(ApiMap, {
      result, scores: [score], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe(color);
    expect(drawn.layers[0].style?.fillColor).toBe(color);
    expect(drawn.layers[0].tooltip).toContain(value.toFixed(1));
  });
  it.each(["full", "partial"] as const)("keeps zero numeric and distinguishes %s geometry and tooltips", (completeness) => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const score = { ...result.scores[0], completeness, value: 0 };
    result.map_data = { status: "available", completeness: "full", features: [{
      entity_kind: "sample_point", entity_id: score.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: [],
    }] };
    render(createElement(ApiMap, {
      result, scores: [score], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    const feature = drawn.layers[0];
    expect(feature.tooltip).toContain("0.0");
    expect(feature.marker?.fillColor).toBe("#c72d44");
    if (completeness === "partial") {
      expect(feature.tooltip).toContain("частичная оценка");
      expect(feature.marker?.dashArray).toBe("1 4");
      expect(feature.style?.dashArray).toBe("1 4");
      expect(feature.marker?.fillOpacity).toBeLessThan(0.9);
      expect(screen.getByLabelText("Условные обозначения карты").textContent).toContain("частичная оценка");
    } else {
      expect(feature.tooltip).not.toContain("частичная оценка");
      expect(feature.marker?.dashArray).toBeUndefined();
      expect(screen.queryByLabelText("Условные обозначения карты")).toBeNull();
    }
  });
  it("keeps an unknown score gray without fabricating zero", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const score = { ...result.scores[0], status: "unknown" as const, value: null };
    result.map_data = { status: "available", features: [{
      entity_kind: "sample_point", entity_id: score.subject.point_id!, territory_id: "city-1",
      geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: [],
    }] };
    render(createElement(ApiMap, {
      result, scores: [score], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#8990a7");
    expect(drawn.layers[0].tooltip).toContain("нет индекса");
    expect(drawn.layers[0].tooltip).not.toContain("0.0");
  });
});
