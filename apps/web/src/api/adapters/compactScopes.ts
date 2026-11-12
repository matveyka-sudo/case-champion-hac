import { Temporal } from "@js-temporal/polyfill";
import type { Schemas, Territory } from "../types";
import { categoryIds, departureAt, passengerModes, pipelineProducts, type ApiDraft } from "./request";

export const compactSlots = [
  { slot: 32, label: "Утро" },
  { slot: 52, label: "День" },
  { slot: 72, label: "Вечер" },
  { slot: 92, label: "Ночь" },
] as const;

export const intercityModes: Record<string, string> = {
  air: "air", rail: "rail", bus: "road", car: "private_car", sea: "water", mixed: "mixed",
};

type Scope = Schemas["CompactAnalysisScope"];

export function compactAvailability(
  draft: ApiDraft, catalog: Territory[], scopes: Scope[], profileId: string,
) {
  const urban = draft.kind.startsWith("urban_");
  const origin = catalog.find((t) => t.territory_id === (urban ? draft.cityId : draft.originId));
  const matching = scopes.filter((scope) => scope.analysis_kind === draft.kind &&
    (urban ? scope.city_id === draft.cityId && !draft.areaId :
      scope.territory_level === draft.level && scope.origin_id === draft.originId && scope.destination_id === draft.destinationId));
  const requestedModes = draft.kind === "urban_passenger" ? (draft.travel ? [passengerModes[draft.travel]] : []) :
    draft.kind === "intercity_passenger" ? draft.paxModes.map((mode) => intercityModes[mode]) :
    draft.kind === "intercity_cargo" ? (draft.pipeline ? ["pipeline"] : draft.cargoModes) : [];
  const categories = Object.keys(draft.cats).map((id) => categoryIds[id as keyof typeof categoryIds]);
  const selected = matching.filter((scope) =>
    requestedModes.every((mode) => scope.modes?.includes(mode)) &&
    (draft.kind !== "urban_passenger" || (!!requestedModes.length && categories.every((c) => scope.categories?.includes(c)))) &&
    (draft.kind !== "intercity_passenger" && draft.kind !== "intercity_cargo" || requestedModes.length > 0) &&
    (!draft.kind.endsWith("cargo") || scope.cargo_profile_id === profileId) &&
    (draft.kind !== "urban_cargo" || scope.direction === (draft.direction === "out" ? "outbound" : "inbound")) &&
    (draft.kind !== "intercity_cargo" || (scope.cargo_transport === (draft.pipeline ? "pipeline" : "multimodal") &&
      (!draft.pipeline || (!!draft.pipelineProduct && scope.pipeline_product === pipelineProducts[draft.pipelineProduct])))));
  const dates = new Map<string, Set<string>>();
  if (origin && draft.traffic === "static" && compactSlots.some(({ slot }) => slot === draft.slot)) {
    for (const scope of selected) {
      const from = Temporal.Instant.from(scope.valid_from);
      const to = Temporal.Instant.from(scope.valid_to);
      const endDay = to.toZonedDateTimeISO(origin.timezone).toPlainDate();
      for (let day = from.toZonedDateTimeISO(origin.timezone).toPlainDate();
        Temporal.PlainDate.compare(day, endDay) <= 0; day = day.add({ days: 1 })) {
        const key = day.toString();
        const start = Temporal.Instant.from(departureAt(key, draft.slot, origin.timezone));
        if (Temporal.Instant.compare(start, from) >= 0 && Temporal.Instant.compare(start, to) < 0) {
          const ids = dates.get(key) || new Set<string>();
          ids.add(scope.snapshot_id);
          dates.set(key, ids);
        }
      }
    }
  }
  const snapshots = dates.get(draft.date);
  return {
    dates: [...dates.keys()].sort(),
    snapshotId: snapshots?.size === 1 ? [...snapshots][0] : "",
    hasMatchingTerritory: matching.length > 0,
    hasMatchingConditions: selected.length > 0,
  };
}
