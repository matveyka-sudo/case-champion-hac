import type { Schemas, Territory } from "../types";
import { categoryIds, departureAt, eligibleSnapshots, passengerModes, resolvedSnapshotId, type ApiDraft } from "./request";
import { scopePreset, urbanScopeAvailability, type UrbanSelection } from "./urbanScopes";

export function boundSnapshotId(draft: ApiDraft, catalog: Territory[], caps: Schemas["Capabilities"]) {
  const snapshots = eligibleSnapshots(draft, catalog, caps);
  if (draft.snapshotId || draft.kind !== "urban_passenger" || caps.urban_passenger_scopes == null)
    return resolvedSnapshotId(draft, snapshots);
  const city = catalog.find((place) => place.territory_id === draft.cityId && place.level === "city");
  if (!city) return resolvedSnapshotId(draft, snapshots);
  try {
    const departure = departureAt(draft.date, draft.slot, city.timezone);
    const matches = snapshots.flatMap((snapshot) => urbanScopeAvailability({
      cityId: draft.cityId, snapshotId: snapshot.snapshot_id, areaId: draft.areaId, departureAt: departure,
      mode: draft.travel ? passengerModes[draft.travel] : "", traffic: draft.traffic,
      categories: Object.keys(draft.cats).map((key) => categoryIds[key as keyof typeof categoryIds]),
    }, caps.urban_passenger_scopes).matching);
    return matches.length === 1 ? matches[0].snapshot_id : resolvedSnapshotId(draft, snapshots);
  } catch { return resolvedSnapshotId(draft, snapshots); }
}

// Source-backed initial state only. The controller must not call this after any user edit.
export function initialSourceDraft(draft: ApiDraft, catalog: Territory[], caps: Schemas["Capabilities"]): ApiDraft {
  if (draft.cityId || draft.kind !== "urban_passenger") return draft;
  const city = catalog.find((place) => place.level === "city" && place.territory_id === "moscow");
  if (!city) return draft;
  const scopes = caps.urban_passenger_scopes || [];
  const candidates = scopes.filter((scope) => scope.city_id === city.territory_id && scope.passenger_modes.includes("walk") &&
    scope.categories.includes("adult_polyclinic") && scope.whole_city && scope.traffic_modes.includes(caps.default_traffic_mode));
  if (candidates.length !== 1) return draft;
  let departure: string | null = null;
  try { departure = departureAt(draft.date, draft.slot, city.timezone); } catch { /* invalid local draft remains source-checked */ }
  const selection: UrbanSelection = { cityId: city.territory_id, snapshotId: candidates[0].snapshot_id,
    departureAt: departure, areaId: "", mode: "walk", traffic: caps.default_traffic_mode, categories: ["adult_polyclinic"] };
  const preset = scopePreset(candidates[0], selection, scopes, catalog, caps.areas || []);
  if (!preset || preset.selection.mode !== "walk" || preset.selection.categories.length !== 1 || preset.selection.categories[0] !== "adult_polyclinic") return draft;
  const next: ApiDraft = { ...draft, cityId: city.territory_id, snapshotId: preset.selection.snapshotId,
    date: preset.date, slot: preset.slot, travel: "walk", traffic: preset.selection.traffic, areaId: "", cats: { c01: 3 } };
  return eligibleSnapshots(next, catalog, caps).some((snapshot) => snapshot.snapshot_id === next.snapshotId) ? next : draft;
}
