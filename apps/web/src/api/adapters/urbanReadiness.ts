import { Temporal } from "@js-temporal/polyfill";
import type { AnalysisResult, Schemas, Territory } from "../types";
import { categoryIds, departureAt, eligibleSnapshots, passengerModes, resolvedSnapshotId, toRequest, type ApiDraft } from "./request";
import { calculatedPoints } from "./pointOverview";
import { urbanScopeAvailability } from "./urbanScopes";

export function sameUrbanRequest(result: AnalysisResult, request: Schemas["UrbanPassengerRequest"]) {
  const saved = result.request;
  if (result.analysis_kind !== "urban_passenger" || saved.analysis_kind !== "urban_passenger") return false;
  try {
    return saved.city_id === request.city_id &&
      saved.passenger_mode === request.passenger_mode && saved.traffic_mode === request.traffic_mode &&
      (saved.area_id || null) === (request.area_id || null) && !!request.snapshot_id &&
      result.snapshot_id === request.snapshot_id && (saved.snapshot_id || result.snapshot_id) === request.snapshot_id &&
      Temporal.Instant.compare(saved.departure_at, request.departure_at) === 0 &&
      JSON.stringify([...saved.categories].sort()) === JSON.stringify([...request.categories].sort()) &&
      JSON.stringify(Object.entries(saved.priorities).sort()) === JSON.stringify(Object.entries(request.priorities).sort());
  } catch { return false; }
}

export function urbanReadiness(draft: ApiDraft, catalog: Territory[], caps: Schemas["Capabilities"], result: AnalysisResult | null, dirty: boolean) {
  const city = catalog.find((territory) => territory.territory_id === draft.cityId && territory.level === "city");
  const snapshots = eligibleSnapshots(draft, catalog, caps), snapshotId = resolvedSnapshotId(draft, snapshots);
  const snapshot = snapshots.find((item) => item.snapshot_id === snapshotId);
  let request: Schemas["UrbanPassengerRequest"] | null = null;
  try {
    const candidate = toRequest(draft, catalog, caps);
    if (candidate.analysis_kind === "urban_passenger") request = candidate;
  } catch { /* Unsupported current conditions do not inherit saved result facts. */ }
  let departure: string | null = null;
  try { if (city) departure = departureAt(draft.date, draft.slot, city.timezone); } catch { /* Invalid local time has no registered context. */ }
  const matching = urbanScopeAvailability({ cityId: draft.cityId, snapshotId, departureAt: departure,
    areaId: draft.areaId, mode: draft.travel ? passengerModes[draft.travel] : "", traffic: draft.traffic,
    categories: Object.keys(draft.cats).flatMap((id) => categoryIds[id as keyof typeof categoryIds] || []) }, caps.urban_passenger_scopes).matching;
  const saved = result && !dirty && request && sameUrbanRequest(result, request) ? result : null;
  const overview = saved ? calculatedPoints(saved) : null;
  const basis = overview?.territoryScore?.aggregation_basis;
  return { city, snapshot, snapshotChoice: snapshots.length > 1 && !snapshotId,
    publishedScopes: caps.urban_passenger_scopes != null, matchingScopes: matching.length,
    serviceAvailable: !!caps.analyses.find((kind) => kind.analysis_kind === "urban_passenger")?.calculation_available,
    saved, overview, populationBasis: basis && "population" in basis ? basis : null, areaSelected: !!draft.areaId };
}
export type UrbanReadiness = ReturnType<typeof urbanReadiness>;
