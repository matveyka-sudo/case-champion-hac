import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiMap } from "../src/widgets/ApiMap";
import { decode } from "../src/api/validation";
import resultRaw from "./fixtures/api/urban_passenger.json";
const drawn = vi.hoisted(() => ({
  layers: [] as { style?: Record<string, unknown>; marker?: Record<string, unknown>; tooltip: string }[],
}));
vi.mock("leaflet", () => ({
  default: {
    map: () => {
      const map = { fitBounds: () => map, invalidateSize: vi.fn(), remove: vi.fn() };
      return map;
    },
    tileLayer: () => ({ on: () => ({ addTo: vi.fn() }) }),
    layerGroup: () => ({ addTo: () => ({ clearLayers: vi.fn() }) }),
    latLngBounds: () => ({ extend: vi.fn(), isValid: () => false }),
    circleMarker: (_position: unknown, style: Record<string, unknown>) => {
      drawn.layers.at(-1)!.marker = style;
      return {};
    },
    geoJSON: (_feature: unknown, options: {
      style?: Record<string, unknown>;
      pointToLayer?: (feature: unknown, point: unknown) => unknown;
    }) => {
      const item = { style: options.style, tooltip: "" };
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
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("map score completeness", () => {
  it.each([
    [0, "#c72d44"], [49.9, "#c72d44"], [50, "#d49520"],
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
