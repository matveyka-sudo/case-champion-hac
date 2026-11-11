import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiMap } from "../src/widgets/ApiMap";
import { decode } from "../src/api/validation";
import resultRaw from "./fixtures/api/urban_passenger.json";
import intercityRaw from "./fixtures/api/intercity_passenger.json";
import { scenarioJourneys } from "../src/api/adapters/scenario";
import { scenarioRouteFixture } from "./fixtures/scenario-routes";
import { partialWalkGeometryFixture } from "./fixtures/partial-walk-geometry";
import { legGeometryFixture } from "./fixtures/leg-geometry";
const drawn = vi.hoisted(() => ({
  layers: [] as { style?: Record<string, unknown>; marker?: Record<string, unknown>; tooltip: string; geometry?: unknown; click?: () => void }[],
  pans: [] as unknown[], zooms: [] as unknown[],
  commands: [] as string[],
  clears: 0,
  views: [] as { center: unknown; zoom: number }[],
  boundsInputs: [] as unknown[][],
  order: [] as { geometry?: unknown }[],
}));
vi.mock("leaflet", () => ({
  default: {
    map: () => {
      const map = { createPane: () => ({ style: {} }), fitBounds: () => { drawn.commands.push("home"); return map; }, invalidateSize: vi.fn(), remove: vi.fn(), getZoom: () => 10,
        zoomIn: () => drawn.commands.push("in"), zoomOut: () => drawn.commands.push("out"),
        setZoomAround: (location: unknown, zoom: number) => drawn.zooms.push({ location, zoom }),
        setView: (center: unknown, zoom: number) => { drawn.views.push({ center, zoom }); drawn.commands.push("city"); return map; },
        panInside: (location: unknown) => drawn.pans.push(location) };
      return map;
    },
    tileLayer: () => ({ on: () => ({ addTo: vi.fn() }) }),
    layerGroup: () => {
      const members = new Set<typeof drawn.order[number]>();
      const group = { members, addTo: () => group, clearLayers: () => {
        drawn.clears++;
        drawn.order = drawn.order.filter(item => !members.has(item));
        members.clear();
      } };
      return group;
    },
    latLngBounds: (positions: unknown[]) => { drawn.boundsInputs.push(positions); return { extend: vi.fn(), isValid: () => positions.length > 0 }; },
    circleMarker: (_position: unknown, style: Record<string, unknown>) => {
      const item = drawn.layers.at(-1)!;
      item.marker = style;
      return { setRadius: (radius: number) => { item.marker!.radius = radius; } };
    },
    geoJSON: (feature: { type: string; geometry?: unknown }, options: {
      style?: Record<string, unknown>;
      pointToLayer?: (feature: unknown, point: unknown) => unknown;
    }) => {
      const item: typeof drawn.layers[number] = { style: options.style, tooltip: "", geometry: feature.type === "Feature" ? feature.geometry : feature };
      drawn.layers.push(item);
      options.pointToLayer?.({}, {});
      const layer = {
        bindTooltip: (tip: HTMLElement) => { item.tooltip = tip.textContent || ""; return layer; },
        on: (event: string, handler: () => void) => { if (event === "click") item.click = handler; return layer; },
        addTo: (group: { members: Set<typeof drawn.order[number]> }) => { group.members.add(item); drawn.order.push(item); return layer; },
        bringToBack: () => { drawn.order = [item, ...drawn.order.filter(value => value !== item)]; return layer; },
        bringToFront: () => { drawn.order = [...drawn.order.filter(value => value !== item), item]; return layer; },
        getBounds: () => ({}),
        setStyle: (style: Record<string, unknown>) => {
          item.style = { ...item.style, ...style };
          if (item.marker) Object.assign(item.marker, style);
          return layer;
        },
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
  drawn.clears = 0;
  drawn.views.length = 0;
  drawn.boundsInputs.length = 0;
  drawn.order.length = 0;
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
});
describe("explicit calculated-point focus", () => {
  it("focuses a source-backed Moscow selection before analysis once and never initiates a calculation", () => {
    const props = { result: null, scores: [], onSelect: vi.fn(), selection: null, zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, { ...props, cityId: "" }));
    view.rerender(createElement(ApiMap, { ...props, cityId: "moscow" }));
    expect(drawn.views).toEqual([{ center: [55.75, 37.62], zoom: 10 }]);
    view.rerender(createElement(ApiMap, { ...props, cityId: "moscow", selection: { entity_kind: "sample_point", entity_id: "unused" } }));
    expect(drawn.views).toHaveLength(1);
    view.rerender(createElement(ApiMap, { ...props, cityId: "moscow", zoomAction: { id: 1, kind: "home" } }));
    expect(drawn.views).toHaveLength(2);
    expect(drawn.layers).toHaveLength(0);
  });

  it("opens only new DEMO analyses centrally, retains New Moscow, and preserves camera on before/after toggles", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    result.snapshot_id = "demo-moscow-synthetic-camera";
    result.map_data = { status: "available", features: [
      { entity_kind: "sample_point", entity_id: "central-point", territory_id: "city-1", geometry: { type: "Point", coordinates: [37.62, 55.75] }, geometry_kind: "actual", source_refs: [] },
      { entity_kind: "sample_point", entity_id: "new-moscow-point", territory_id: "city-1", geometry: { type: "Point", coordinates: [37.19, 55.41] }, geometry_kind: "actual", source_refs: [] },
      { entity_kind: "destination", entity_id: "far-destination", territory_id: "city-1", geometry: { type: "Point", coordinates: [40, 57] }, geometry_kind: "actual", source_refs: [] },
    ] };
    const props = { result, scores: result.scores, onSelect: vi.fn(), selection: null };
    const view = render(createElement(ApiMap, { ...props, zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.views).toEqual([{ center: [55.75, 37.62], zoom: 10 }]);
    expect(drawn.layers.some((layer) => JSON.stringify(layer.geometry) === JSON.stringify(result.map_data!.features![1].geometry))).toBe(true);
    view.rerender(createElement(ApiMap, { ...props, scenarioView: true, viewId: "after", zoomAction: { id: 0, kind: "home" } }));
    view.rerender(createElement(ApiMap, { ...props, scenarioView: false, zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.views).toHaveLength(1);
    view.rerender(createElement(ApiMap, { ...props, zoomAction: { id: 1, kind: "all_points" } }));
    expect(drawn.boundsInputs.at(-1)).toEqual([[55.75, 37.62], [55.41, 37.19]]);
    const commands = [...drawn.commands];
    view.rerender(createElement(ApiMap, { ...props, selection: { entity_kind: "sample_point", entity_id: "new-moscow-point" }, zoomAction: { id: 1, kind: "all_points" } }));
    expect(drawn.commands).toEqual(commands);
    view.rerender(createElement(ApiMap, { ...props, selection: { entity_kind: "sample_point", entity_id: "new-moscow-point" }, zoomAction: { id: 2, kind: "selection" } }));
    expect(drawn.pans.at(-1)).toEqual([55.41, 37.19]);
    view.rerender(createElement(ApiMap, { ...props, zoomAction: { id: 3, kind: "home" } }));
    expect(drawn.views).toHaveLength(2);
    view.rerender(createElement(ApiMap, { ...props, result: { ...result, analysis_id: "new-analysis" }, zoomAction: { id: 3, kind: "home" } }));
    expect(drawn.views).toHaveLength(3);
  });
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
    const props = { result: baseline, scores: scenario.after.scores, selection: { entity_kind: "sample_point" as const, entity_id: "demo-point" }, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
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
describe("partial walk map path", () => {
  it("draws disjoint leg segments separately, links their parent point, and leaves unknown indices neutral", () => {
    const result = legGeometryFixture(null), choose = vi.fn();
    const props = { selection: { entity_kind: "sample_point" as const, entity_id: result.journeys[0].point_id! }, onSelect: choose };
    const view = render(createElement(ApiMap, { ...props, result, scores: result.scores, zoomAction: { id: 0, kind: "home" } }));
    const lines = drawn.layers.filter((layer) => (layer.geometry as { type?: string })?.type === "LineString");
    expect(lines.map((line) => line.geometry)).toEqual([result.journeys[0].legs[1].geometry, result.journeys[0].legs[3].geometry]);
    expect(lines.every((line) => line.tooltip.includes("геометрия подходов не подтверждена"))).toBe(true);
    const pointGeometry = result.map_data!.features!.find((feature) => feature.entity_id === result.journeys[0].point_id)!.geometry;
    expect(drawn.layers.find((layer) => JSON.stringify(layer.geometry) === JSON.stringify(pointGeometry))?.marker?.fillColor).toBe("#8990a7");
    lines[0].click!();
    expect(choose).toHaveBeenCalledWith({ entity_kind: "sample_point", entity_id: result.journeys[0].point_id });
    drawn.layers.length = 0;
    view.rerender(createElement(ApiMap, { ...props, result: null, scores: [], zoomAction: { id: 1, kind: "home" } }));
    expect(drawn.layers).toEqual([]);
  });
  it("draws the supplied actual route even when the exact score operand is not journey-linked", () => {
    const result = partialWalkGeometryFixture();
    render(createElement(ApiMap, {
      result, scores: result.scores,
      selection: { entity_kind: "sample_point", entity_id: "synthetic-point-2" },
      onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" },
    }));
    const lines = drawn.layers.filter((layer) => (layer.geometry as { type?: string })?.type === "LineString");
    expect(lines).toHaveLength(1);
    expect(lines[0].geometry).toEqual(result.journeys[0].geometry);
    expect(lines[0].tooltip).toBe("Маршрут");
    expect(lines[0].style?.dashArray).toBeUndefined();
  });
  it("clears the old city route and resets the viewport when its result context is removed", () => {
    const result = partialWalkGeometryFixture();
    const props = { selection: { entity_kind: "sample_point" as const, entity_id: result.journeys[0].point_id! }, onSelect: vi.fn() };
    const view = render(createElement(ApiMap, { ...props, result, scores: result.scores, zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.layers.some((layer) => (layer.geometry as { type?: string })?.type === "LineString")).toBe(true);
    const clears = drawn.clears;
    drawn.layers.length = 0;
    view.rerender(createElement(ApiMap, { ...props, result: null, scores: [], zoomAction: { id: 1, kind: "home" } }));
    expect(drawn.clears).toBe(clears + 2);
    expect(drawn.layers).toEqual([]);
    expect(drawn.commands.at(-1)).toBe("home");
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("map score completeness", () => {
  it("uses the real Moscow camera independently of data origin and preserves it on scenario changes", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    if (result.request.analysis_kind !== "urban_passenger") throw Error("fixture");
    result.request.city_id = "moscow";
    result.snapshot_id = "real-source-revision";
    const props = { result, scores: result.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, props));
    expect(drawn.views).toEqual([{ center: [55.75, 37.62], zoom: 10 }]);
    view.rerender(createElement(ApiMap, { ...props, scenarioView: true }));
    expect(drawn.views).toHaveLength(1);
  });
  it("colors an explicit observed estimate and keeps the unknown city neutral", () => {
    const result = decode("AnalysisResult", structuredClone(resultRaw));
    const category = { ...result.scores[0], name: "category_index", value: null, status: "unknown" as const };
    const point = { ...category, name: "point_index", score_id: "total", subject: { ...category.subject, category: null } };
    const observed = { ...category, name: "observed_route_accessibility", score_id: "observed", value: 0, status: "available" as const, completeness: "partial" as const, ranking_eligible: false };
    const city = { ...point, score_id: "city", subject: { kind: "territory" as const, territory_id: "city-1" } };
    result.map_data = { status: "available", features: [{ entity_kind: "sample_point", entity_id: point.subject.point_id!, territory_id: "city-1", geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: [] },
      { entity_kind: "territory", entity_id: "city-1", territory_id: "city-1", geometry: { type: "Point", coordinates: [37.1, 55.1] }, geometry_kind: "actual", source_refs: [] }] };
    render(createElement(ApiMap, { result, scores: [category, point, observed, city], selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.layers[0].marker?.fillColor).toBe("#c72d44");
    expect(drawn.layers[0].tooltip).toContain("Предварительная оценка по найденному маршруту");
    expect(drawn.layers[0].marker?.dashArray).toBe("1 4");
    expect(drawn.layers[1].marker?.fillColor).toBe("#8990a7");
  });

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

describe("urban overview route readability", () => {
  it("preserves intercity route display without a point selection or point_id", () => {
    const result = decode("AnalysisResult", structuredClone(intercityRaw));
    const geometry = scenarioRouteFixture().baseline.journeys[0].geometry!;
    result.journeys[0].geometry = geometry;
    result.journeys[0].geometry_kind = "actual";
    result.journeys[0].point_id = null;
    render(createElement(ApiMap, { result, scores: result.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.order.some(layer => layer.geometry === geometry)).toBe(true);
  });
  it("keeps territory fills behind every point and retains the full route inventory without drawing it at overview", () => {
    const { baseline } = scenarioRouteFixture();
    const point = baseline.map_data!.features![0];
    const unknown = { ...point, entity_id: "unknown-point", geometry: { type: "Point" as const, coordinates: [37.03, 55] as [number, number] } };
    const territory = { ...point, entity_kind: "territory" as const, entity_id: "city-1", geometry: { type: "Polygon" as const, coordinates: [[[37, 55], [37.1, 55], [37.1, 55.1], [37, 55]]] as [number, number][][] } };
    baseline.map_data!.features!.push(unknown, territory);
    const before = JSON.stringify(baseline);
    render(createElement(ApiMap, { result: baseline, scores: baseline.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } }));
    expect(drawn.order.filter(layer => (layer.geometry as { type: string }).type === "LineString")).toHaveLength(0);
    expect(drawn.order).toHaveLength(baseline.map_data!.features!.length);
    expect(drawn.order.findIndex(layer => layer.geometry === territory.geometry)).toBeLessThan(drawn.order.findIndex(layer => layer.geometry === point.geometry));
    expect(drawn.order.at(-1)?.geometry).toBe(unknown.geometry);
    expect(baseline.journeys).toHaveLength(2);
    expect(JSON.stringify(baseline)).toBe(before);
  });

  it("changes only the selected category's routes and clears them when selection is cleared", () => {
    const { baseline } = scenarioRouteFixture();
    if (baseline.request.analysis_kind !== "urban_passenger") throw new Error("fixture");
    baseline.request.categories = ["school", "airport"];
    baseline.metrics[0].subject.category = "school";
    baseline.metrics[1].subject.category = "airport";
    const selection = { entity_kind: "sample_point" as const, entity_id: "demo-point" };
    const props = { result: baseline, scores: baseline.scores, selection, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } };
    const view = render(createElement(ApiMap, { ...props, category: "school" }));
    const lines = () => drawn.order.filter(layer => (layer.geometry as { type: string }).type === "LineString").map(layer => layer.geometry);
    expect(lines()).toEqual([baseline.journeys[0].geometry]);
    view.rerender(createElement(ApiMap, { ...props, category: "airport" }));
    expect(lines()).toEqual([baseline.journeys[1].geometry]);
    const featureConstructions = drawn.layers.filter(layer => (layer.geometry as { type: string }).type !== "LineString").length;
    view.rerender(createElement(ApiMap, { ...props, category: "airport", selection: null }));
    expect(lines()).toEqual([]);
    expect(drawn.layers.filter(layer => (layer.geometry as { type: string }).type !== "LineString")).toHaveLength(featureConstructions);
    expect(drawn.order).toHaveLength(baseline.map_data!.features!.length);
  });
});
