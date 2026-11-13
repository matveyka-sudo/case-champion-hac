import type { Schemas, Territory } from "../types";
import type { CargoProfileChoice } from "../../widgets/FilterControlViews";
import { timeLabel } from "../../shared/format";
import { compactAvailability, compactSlots } from "./compactScopes";
import type { ApiDraft } from "./request";

export const cargoHandling = [["container", "Контейнер"], ["general", "Генеральный"], ["bulk", "Навалочный"], ["liquid", "Наливной"]] as const;

export function matchingCargoProfiles(profiles: Schemas["Capabilities"]["cargo_profiles"], choice: CargoProfileChoice) {
  return profiles.filter((profile) => profile.kind === "delivery" && profile.food_class === choice.group &&
    !!profile.perishable === !!choice.limits.perish &&
    profile.handling_requirements.length === 1 && profile.handling_requirements[0] === choice.handling &&
    profile.temperature_requirements.controlled === !!choice.limits.temp && profile.urgency === (choice.limits.urgent ? "urgent" : choice.limits.nonurgent ? "nonurgent" : "ordinary"));
}

export function compactCargoOptions(draft: ApiDraft, catalog: Territory[], caps: Schemas["Capabilities"], choice: CargoProfileChoice) {
  if (draft.kind !== "urban_cargo" || !caps.compact_scopes || !choice.group) return null;
  const scopes = caps.compact_scopes;
  return cargoHandling.filter(([handling]) => handling === "container" || handling === "general").flatMap(([handling, label]) => {
    const profiles = matchingCargoProfiles(caps.cargo_profiles, { ...choice, handling });
    const times = compactSlots.flatMap(({ slot }) => {
      const dates = profiles.flatMap(({ profile_id }) =>
        compactAvailability({ ...draft, slot }, catalog, scopes, profile_id).dates);
      if (!dates.length) return [];
      return [{ time: timeLabel(slot), onSelectedDate: dates.includes(draft.date) }];
    });
    return times.length ? [{ handling, label, times }] : [];
  });
}
