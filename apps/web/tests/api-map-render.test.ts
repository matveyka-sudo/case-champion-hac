import { createElement } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiMap } from "../src/widgets/ApiMap";
import { legGeometryFixture } from "./fixtures/leg-geometry";
import { pointOverviewFixture } from "./fixtures/point-overview";

const fake = vi.hoisted(() => {
  const bounds = { extend: vi.fn(), isValid: () => true };
  const map = { fitBounds: vi.fn(), setView: vi.fn(), invalidateSize: vi.fn(), remove: vi.fn(), getZoom: () => 10, panInside: vi.fn(), setZoomAround: vi.fn() };
  map.fitBounds.mockReturnValue(map);
  const group = { addTo: vi.fn(), clearLayers: vi.fn() };
  group.addTo.mockReturnValue(group);
  const geoJSON = vi.fn((_data: unknown, options: { pointToLayer?: (data: unknown, position: unknown) => unknown; style?: object }) => {
    const marker = (_data as { geometry?: { type: string } }).geometry?.type === "Point" ? options.pointToLayer?.({}, {}) : undefined;
    const layer = { bindTooltip: vi.fn(), on: vi.fn(), addTo: vi.fn(), getBounds: () => bounds, setStyle: vi.fn(), marker };
    layer.bindTooltip.mockReturnValue(layer);
    layer.on.mockReturnValue(layer);
    layer.addTo.mockReturnValue(layer);
    return layer;
  });
  return { map, group, bounds, geoJSON, circleMarker: vi.fn(() => ({ setRadius: vi.fn() })) };
});
vi.mock("leaflet", () => ({ default: {
  map: () => fake.map, layerGroup: () => fake.group, latLngBounds: () => fake.bounds,
  geoJSON: fake.geoJSON, circleMarker: fake.circleMarker,
  tileLayer: () => { const tile = { on: () => tile, addTo: () => tile }; return tile; },
} }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("saved map rendering", () => {
  it("retains every feature and disjoint route geometry when selection changes without rebuilding layers", () => {
    const result = legGeometryFixture(), onSelect = vi.fn(), zoomAction = { id: 0, kind: "home" };
    const props = { result, scores: result.scores, onSelect, zoomAction, selection: null };
    const view = render(createElement(ApiMap, props));
    const featureCount = result.map_data!.features!.length, count = featureCount + 2;
    expect(fake.geoJSON).toHaveBeenCalledTimes(count);
    result.map_data!.features!.forEach((feature, index) => {
      expect(fake.geoJSON.mock.calls[index][0]).toMatchObject({ type: "Feature", geometry: feature.geometry });
      expect((fake.geoJSON.mock.calls[index][0] as { geometry: unknown }).geometry).toBe(feature.geometry);
    });
    const journey = result.journeys[0];
    expect(fake.geoJSON.mock.calls[featureCount][0]).toBe(journey.legs[1].geometry);
    expect(fake.geoJSON.mock.calls[featureCount + 1][0]).toBe(journey.legs[3].geometry);
    const pointIndex = result.map_data!.features!.findIndex((feature) => feature.entity_kind === "sample_point" && feature.entity_id === journey.point_id);
    const point = fake.geoJSON.mock.results[pointIndex].value;
    const route = fake.geoJSON.mock.results[featureCount].value;
    route.on.mock.calls[0][1]();
    expect(onSelect).toHaveBeenCalledWith({ entity_kind: "sample_point", entity_id: journey.point_id });
    view.rerender(createElement(ApiMap, { ...props, selection: { entity_kind: "sample_point", entity_id: journey.point_id! } }));
    expect(fake.geoJSON).toHaveBeenCalledTimes(count);
    expect(point.setStyle).toHaveBeenLastCalledWith(expect.objectContaining({ color: "#2f5bff", weight: 3 }));
    expect(point.marker.setRadius).toHaveBeenLastCalledWith(10);
    expect(route.setStyle).not.toHaveBeenCalled();
    view.rerender(createElement(ApiMap, props));
    expect(fake.geoJSON).toHaveBeenCalledTimes(count);
    expect(point.setStyle).toHaveBeenLastCalledWith(fake.geoJSON.mock.calls[pointIndex][1].style);
    expect(point.marker.setRadius).toHaveBeenLastCalledWith(7);
  });

  it("preserves the existing zero, middle, high, unknown and partial styles through linked cells", () => {
    const result = pointOverviewFixture(3, { 0: 0, 1: 70, 2: 90 });
    result.map_data!.features![0].point_id = null;
    render(createElement(ApiMap, { result, scores: result.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } }));
    const styles = fake.geoJSON.mock.calls.map(([, options]) => options.style);
    expect(styles.map((style) => (style as { fillColor: string }).fillColor)).toEqual([
      "#c72d44", "#c72d44", "#d49520", "#d49520", "#0b8a5c", "#0b8a5c", "#8990a7",
    ]);
    expect(styles[0]).toMatchObject({ color: "#59647e", weight: 2, fillOpacity: 0.3, dashArray: "1 4", lineCap: "round" });
    expect(styles[6]).toMatchObject({ color: "white", weight: 1, fillOpacity: 0.62 });
  });

  it("indexes saved cell links and score subjects instead of scanning the full inventory for each feature", () => {
    const result = pointOverviewFixture(60);
    let subjectReads = 0, cellReads = 0;
    for (const score of result.scores) {
      const subject = score.subject;
      Object.defineProperty(score, "subject", { get() { subjectReads++; return subject; } });
    }
    for (const feature of result.map_data!.features!) {
      const cell = feature.cell_id;
      Object.defineProperty(feature, "cell_id", { get() { cellReads++; return cell; } });
    }
    render(createElement(ApiMap, { result, scores: result.scores, selection: null, onSelect: vi.fn(), zoomAction: { id: 0, kind: "home" } }));
    expect(fake.geoJSON).toHaveBeenCalledTimes(result.map_data!.features!.length);
    expect(subjectReads).toBeLessThan(2500);
    expect(cellReads).toBeLessThan(1000);
  });
});
