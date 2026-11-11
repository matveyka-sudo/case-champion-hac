import type { Schemas, Territory } from "../types";
import { categoryIds, departureAt, eligibleSnapshots, passengerModes, resolvedSnapshotId, type ApiDraft } from "./request";
import { scopeContainsTime, scopePreset, urbanScopeAvailability, type UrbanSelection } from "./urbanScopes";

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
  return city ? clinicPreset(draft, city, catalog, caps, "walk", draft.date, draft.slot) || draft : draft;
}

const pilotDate = "2026-11-11";
const pilotSlot = 34;
const transitScenarioSlot = 35; // The accepted Moscow scenario is bound to 08:45 local time.

export function clinicPreset(draft: ApiDraft, city: Territory, catalog: Territory[], caps: Schemas["Capabilities"], travel: "walk" | "pt", date = pilotDate, slot = travel === "pt" ? transitScenarioSlot : pilotSlot): ApiDraft | null {
  if (!caps.categories.includes("adult_polyclinic") || !caps.traffic_modes.includes("static")) return null;
  const mode = passengerModes[travel];
  const scopes = caps.urban_passenger_scopes || [];
  const candidates = scopes.filter((scope) => scope.city_id === city.territory_id && scope.passenger_modes.includes(mode) &&
    scope.categories.includes("adult_polyclinic") && scope.whole_city && scope.traffic_modes.includes("static"));
  if (!candidates.length) return null;
  let departure: string;
  try { departure = departureAt(date, slot, city.timezone); } catch { return null; }
  const atRequestedTime = candidates.filter((scope) => scopeContainsTime(scope, departure));
  const scope = atRequestedTime.length === 1 ? atRequestedTime[0] : candidates.length === 1 ? candidates[0] : null;
  if (!scope) return null;
  const selection: UrbanSelection = { cityId: city.territory_id, snapshotId: scope.snapshot_id,
    departureAt: departure, areaId: "", mode, traffic: "static", categories: ["adult_polyclinic"] };
  const preset = scopePreset(scope, selection, scopes, catalog, caps.areas || []);
  if (!preset || preset.selection.mode !== mode || preset.selection.traffic !== "static" ||
    preset.selection.categories.length !== 1 || preset.selection.categories[0] !== "adult_polyclinic") return null;
  const next: ApiDraft = { ...draft, cityId: city.territory_id, snapshotId: preset.selection.snapshotId,
    date: preset.date, slot: preset.slot, travel, traffic: "static", areaId: "", cats: { c01: 3 } };
  return eligibleSnapshots(next, catalog, caps).some((snapshot) => snapshot.snapshot_id === next.snapshotId) ? next : null;
}

export function staticPilotPreset(draft: ApiDraft, catalog: Territory[], caps: Schemas["Capabilities"]): ApiDraft | null {
  if (!caps.traffic_modes.includes("static")) return null;
  const next: ApiDraft = { ...draft, date: pilotDate, slot: pilotSlot, traffic: "static", snapshotId: "",
    ...(draft.kind === "intercity_passenger" ? { paxModes: ["car"] } : {}) };
  const [snapshot, ...others] = eligibleSnapshots(next, catalog, caps);
  return snapshot && !others.length ? { ...next, snapshotId: snapshot.snapshot_id } : null;
}
