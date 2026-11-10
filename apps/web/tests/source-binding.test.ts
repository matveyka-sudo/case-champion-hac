import type { CargoProfileChoice } from "../src/widgets/FilterControlViews";
import { describe, expect, it } from "vitest";
import { decode } from "../src/api/validation";
import { boundSnapshotId, initialSourceDraft } from "../src/api/adapters/sourceBinding";
import { matchingCargoProfiles } from "../src/api/adapters/cargoBinding";
import { initialDraft } from "../src/api/adapters/request";
import capabilities from "./fixtures/api/capabilities.json";
import territoryRows from "./fixtures/api/territories.json";
import { airportScope } from "./fixtures/urban-scopes";
describe("source binding behind the original controls", () => {
  const catalog = territoryRows.map((row) => decode("TerritoryOption", row));
  it("binds only one complete matching scope and never picks the latest overlapping snapshot", () => {
    const caps = decode("Capabilities", structuredClone(capabilities));
    caps.snapshots!.push({ ...caps.snapshots![0], snapshot_id: "another-version" });
    caps.urban_passenger_scopes = [airportScope()];
    const draft = { ...initialDraft(), cityId: "city-2", date: "2026-11-10", travel: "walk" as const, cats: { c08: 3 } };
    expect(boundSnapshotId(draft, catalog, caps)).toBe("demo-snapshot");
    caps.urban_passenger_scopes.push({ ...airportScope(), scope_id: "ambiguous", snapshot_id: "another-version" });
    expect(boundSnapshotId(draft, catalog, caps)).toBe("");
    expect(draft.snapshotId).toBe("");
  });
  it("initializes only the real source-backed Moscow walking clinic scope and leaves saved or unavailable choices alone", () => {
    const caps = decode("Capabilities", structuredClone(capabilities));
    const cities = catalog.map((city) => city.territory_id === "city-1" ? { ...city, territory_id: "moscow" } : city);
    caps.snapshots![0].territory_ids.push("moscow");
    caps.urban_passenger_scopes = [{ ...airportScope(), city_id: "moscow", categories: ["adult_polyclinic"] }];
    const draft = { ...initialDraft(), date: "2026-11-10" };
    const next = initialSourceDraft(draft, cities, caps);
    expect(next).toMatchObject({ cityId: "moscow", travel: "walk", cats: { c01: 3 }, date: draft.date, slot: draft.slot });
    expect(draft.cityId).toBe("");
    expect(initialSourceDraft({ ...draft, cityId: "city-2" }, cities, caps).cityId).toBe("city-2");
    caps.urban_passenger_scopes.push({ ...caps.urban_passenger_scopes[0], scope_id: "ambiguous" });
    expect(initialSourceDraft(draft, cities, caps)).toBe(draft);
  });
  it("matches cargo choices exactly without conflating raw material, perishable goods, or temperature", () => {
    const profiles = decode("Capabilities", capabilities).cargo_profiles;
    const choice = { group: "nonfood", handling: "container", limits: {} };
    expect(matchingCargoProfiles(profiles, choice).map((profile) => profile.profile_id)).toEqual(["demo-general"]);
    for (const patch of ([{ group: "raw" }, { limits: { perish: true } }, { limits: { temp: true } }, { limits: { urgent: true } }] as Partial<CargoProfileChoice>[]))
      expect(matchingCargoProfiles(profiles, { ...choice, ...patch })).toHaveLength(0);
  });
});
