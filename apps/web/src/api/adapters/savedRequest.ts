import { Temporal } from "@js-temporal/polyfill";
import type { AnalysisResult, Schemas, Territory } from "../types";
import { categoryIds, initialDraft, passengerModes, pipelineProducts, type ApiDraft } from "./request";
import type { CargoProfileChoice } from "../../widgets/FilterControlViews";

// Saved request values are historical facts, independent of current readiness/defaults.
export function savedRequestDraft(result: AnalysisResult, catalog: Territory[], caps: Schemas["Capabilities"]): {
  draft: ApiDraft; cargoProfile: CargoProfileChoice | null; warning: string;
} {
  const r = result.request;
  const urban = "city_id" in r;
  const origin = catalog.find((place) => place.territory_id === (urban ? r.city_id : r.origin_id) && place.level === (urban ? "city" : r.territory_level));
  const time = origin ? Temporal.Instant.from(r.departure_at).toZonedDateTimeISO(origin.timezone) : Temporal.ZonedDateTime.from(`${r.departure_at}[${r.departure_at.endsWith("Z") ? "UTC" : r.departure_at.slice(-6)}]`);
  const exactSlot = time.minute % 15 === 0 && time.second === 0 && time.millisecond === 0 && time.microsecond === 0 && time.nanosecond === 0;
  const draft: ApiDraft = { ...initialDraft(), kind: r.analysis_kind, level: urban ? "city" : r.territory_level,
    cityId: urban ? r.city_id : "", originId: urban ? "" : r.origin_id, destinationId: urban ? "" : r.destination_id,
    areaId: urban ? r.area_id || "" : "", snapshotId: result.snapshot_id,
    date: time.toPlainDate().toString(), slot: exactSlot ? time.hour * 4 + time.minute / 15 : -1, traffic: r.traffic_mode,
    cats: {}, travel: "", paxModes: [], cargoModes: [], profileId: "", pipeline: false, pipelineProduct: "" };
  if (r.analysis_kind === "urban_passenger") {
    draft.travel = (Object.entries(passengerModes).find(([, value]) => value === r.passenger_mode)?.[0] || "") as ApiDraft["travel"];
    for (const [key, category] of Object.entries(categoryIds)) if (r.categories.includes(category))
      draft.cats[key as keyof typeof categoryIds] = ({ low: 1, medium: 2, high: 3 } as const)[r.priorities[category] || "medium"];
  } else if (r.analysis_kind === "intercity_passenger") {
    const modes: Record<string, string> = { air: "air", rail: "rail", road: "bus", private_car: "car", water: "sea", mixed: "mixed" };
    draft.paxModes = (r.passenger_modes || []).map((mode) => modes[mode]);
  } else {
    draft.profileId = r.cargo_profile_id;
    if (r.analysis_kind === "urban_cargo") draft.direction = r.direction === "outbound" ? "out" : "in";
    else {
      draft.cargoModes = [...r.cargo_modes || []];
      draft.pipeline = r.cargo_transport === "pipeline";
      draft.pipelineProduct = (Object.entries(pipelineProducts).find(([, value]) => value === r.pipeline_product)?.[0] || "") as ApiDraft["pipelineProduct"];
    }
  }
  const profile = caps.cargo_profiles.find((value) => value.profile_id === draft.profileId);
  const cargoProfile: CargoProfileChoice | null = profile?.kind === "delivery" && profile.handling_requirements.length === 1 &&
    (profile.urgency !== "nonurgent" || (!!caps.compact_scopes && r.analysis_kind === "intercity_cargo"))
    ? { group: profile.food_class, handling: profile.handling_requirements[0], limits: { perish: !!profile.perishable, temp: profile.temperature_requirements.controlled, urgent: profile.urgency === "urgent", ...(profile.urgency === "nonurgent" ? { nonurgent: true } : {}) } }
    : null;
  return { draft, cargoProfile, warning: !origin || !exactSlot || ("cargo_profile_id" in r && (!profile || (profile.kind === "delivery" && !cargoProfile))) || (r.analysis_kind === "intercity_passenger" && !draft.paxModes.length)
    ? "Сохранённый результат открыт. Некоторые исходные условия нельзя точно представить текущими фильтрами; перед новым расчётом проверьте параметры."
    : "" };
}
