import { describe, expect, it } from "vitest";
import {
  eligibleSnapshots,
  initialDraft,
  isUrbanCity,
  resolvedSnapshotId,
  toRequest,
} from "../src/api/adapters/request";
import { lastReturnsForSelection, snapshotLabel } from "../src/api/adapters/result";
import { decode } from "../src/api/validation";
import type { Schemas } from "../src/api/types";
import capabilities from "./fixtures/api/capabilities.json";
import territoriesRaw from "./fixtures/api/territories.json";
const territories = territoriesRaw.map((t) => decode("TerritoryOption", t));
const caps = () => decode("Capabilities", structuredClone(capabilities));
const draft = () => ({ ...initialDraft(), cityId: "city-1", date: "2026-11-09" });
const snapshot = (id = "snapshot-a"): Schemas["SnapshotAvailability"] => ({
  snapshot_id: id,
  territory_ids: ["city-1", "city-2"],
  valid_from: "2026-11-09",
  valid_to: "2026-11-09",
  graph_version: "graph-v1",
  calendar_version: "calendar-v1",
});
describe("published city and snapshot scope", () => {
  it("restricts urban cities without removing other cities from intercity analysis", () => {
    const other = { ...territories[0], territory_id: "city-3", name: "Казань" };
    const catalog = [...territories, other];
    expect(isUrbanCity(other, caps())).toBe(false);
    for (const kind of ["urban_passenger", "urban_cargo"] as const)
      expect(() => toRequest({ ...draft(), kind, cityId: other.territory_id }, catalog, caps()))
        .toThrow("Город не поддерживает городской анализ");
    const c = caps();
    c.snapshots = [{ ...snapshot(), territory_ids: ["city-1", "city-3"] }];
    expect(toRequest({ ...draft(), kind: "intercity_passenger", originId: "city-1", destinationId: "city-3" }, catalog, c))
      .toMatchObject({ destination_id: "city-3", snapshot_id: "snapshot-a" });
  });
  it("submits the sole eligible snapshot and requires an explicit choice for overlapping coverage", () => {
    const c = caps();
    c.snapshots = [snapshot("older"), snapshot("newer")];
    expect(resolvedSnapshotId(draft(), eligibleSnapshots(draft(), territories, c))).toBe("");
    expect(() => toRequest(draft(), territories, c)).toThrow("Выберите один");
    expect(toRequest({ ...draft(), snapshotId: "older" }, territories, c)).toMatchObject({ snapshot_id: "older" });
    c.snapshots = [snapshot("only")];
    expect(toRequest(draft(), territories, c)).toMatchObject({ snapshot_id: "only" });
    expect(() => toRequest({ ...draft(), snapshotId: "stale" }, territories, c)).toThrow("не покрывает");
  });
  it("rejects unknown boundaries, wrong geography and dates outside inclusive coverage", () => {
    const c = caps();
    c.snapshots = [
      { ...snapshot("no-start"), valid_from: null },
      { ...snapshot("no-end"), valid_to: null },
      { ...snapshot("wrong-city"), territory_ids: ["city-2"] },
      { ...snapshot("past"), valid_from: "2026-11-01", valid_to: "2026-11-08" },
    ];
    expect(eligibleSnapshots(draft(), territories, c)).toEqual([]);
    expect(() => toRequest(draft(), territories, c)).toThrow("Нет подтверждённого набора");
    c.snapshots = [];
    expect(() => toRequest(draft(), territories, c)).toThrow("Нет подтверждённого набора");
    delete c.snapshots;
    expect(toRequest(draft(), territories, c)).not.toHaveProperty("snapshot_id");
  });
  it("checks the departure instant in both endpoint time zones, including midnight and reversed direction", () => {
    const c = caps();
    c.snapshots = [snapshot()];
    const pair = { ...draft(), kind: "intercity_passenger" as const, originId: "city-1", destinationId: "city-2", slot: 0 };
    // Midnight Moscow is still the previous calendar day in Kaliningrad.
    expect(eligibleSnapshots(pair, territories, c)).toEqual([]);
    expect(eligibleSnapshots({ ...pair, slot: 4 }, territories, c)).toHaveLength(1);
    expect(eligibleSnapshots({ ...pair, originId: "city-2", destinationId: "city-1", slot: 95 }, territories, c)).toEqual([]);
    const regions = { ...pair, level: "region" as const, originId: "region-1", destinationId: "region-2", slot: 4 };
    expect(eligibleSnapshots(regions, territories, c)).toEqual([]);
    c.snapshots = [{ ...snapshot(), territory_ids: ["region-1", "region-2"] }];
    expect(eligibleSnapshots(regions, territories, c)).toHaveLength(1);
  });
  it("keeps overlapping snapshot identities distinct without using their order as preference", () => {
    const a = snapshot("version-A-same-tail"), b = snapshot("version-B-same-tail");
    const labelA = snapshotLabel(a, territories, [a, b]);
    const labelB = snapshotLabel(b, territories, [a, b]);
    expect(labelA).toContain("Москва, Калининград");
    expect(labelA).toContain("09.11.2026–09.11.2026");
    expect(labelA).not.toBe(labelB);
    expect(snapshotLabel(a, territories, [b, a])).toBe(labelA);
  });
  it("refuses a pipeline profile for another product and preserves canonical products", () => {
    const c = caps();
    c.cargo_profiles.push({ kind: "pipeline", profile_id: "pipe-gas", product: "gas" });
    const pipeline = { ...draft(), kind: "intercity_cargo" as const, originId: "city-1", destinationId: "city-2", pipeline: true, pipelineProduct: "oil" as const, profileId: "pipe-gas" };
    expect(() => toRequest(pipeline, territories, c)).toThrow("нужного продукта");
    expect(toRequest({ ...pipeline, pipelineProduct: "gas" }, territories, c)).toMatchObject({ cargo_profile_id: "pipe-gas", pipeline_product: "gas", cargo_modes: ["pipeline"] });
    expect(() => toRequest({ ...pipeline, pipelineProduct: "" }, territories, c)).toThrow("Выберите доступный продукт");
  });
});
describe("last return context", () => {
  const returns = [
    ["point-a", "school-a"], ["point-b", "school-a"], ["point-a", "school-b"],
  ].map(([point, destination]): Schemas["LastReturn"] => ({
    point_id: point, destination_object_id: destination,
    arrival_at: null, first_departure_at: null, leave_at: null,
    evidence_refs: [], journey_id: null, reason_code: null,
    service_date: "2026-11-09", status: "unknown", timezone: "Europe/Moscow",
  }));
  const features: Schemas["MapFeature"][] = [{
    entity_kind: "sample_point", entity_id: "point-a", cell_id: "cell-a", territory_id: "city-1",
    geometry: null, geometry_kind: "none", source_refs: [],
  }];
  it("filters by canonical point and destination fields and resolves cells through sample points", () => {
    expect(lastReturnsForSelection(returns, null, features)).toHaveLength(3);
    expect(lastReturnsForSelection(returns, { entity_kind: "sample_point", entity_id: "point-a" }, features)).toEqual([returns[0], returns[2]]);
    expect(lastReturnsForSelection(returns, { entity_kind: "cell", entity_id: "cell-a" }, features)).toEqual([returns[0], returns[2]]);
    expect(lastReturnsForSelection(returns, { entity_kind: "destination", entity_id: "school-a" }, features)).toEqual([returns[0], returns[1]]);
    expect(lastReturnsForSelection(returns, { entity_kind: "freight_site", entity_id: "unknown-site" }, features)).toEqual([]);
    expect(returns[0].leave_at).toBeNull();
  });
});
