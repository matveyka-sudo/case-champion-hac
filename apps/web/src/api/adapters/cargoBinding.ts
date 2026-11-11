import type { Schemas } from "../types";
import type { CargoProfileChoice } from "../../widgets/FilterControlViews";

export function matchingCargoProfiles(profiles: Schemas["Capabilities"]["cargo_profiles"], choice: CargoProfileChoice) {
  // These independent source choices currently have no canonical profile fields.
  if (choice.group === "raw" || choice.limits.perish) return [];
  return profiles.filter((profile) => profile.kind === "delivery" && profile.food_class === choice.group &&
    profile.handling_requirements.length === 1 && profile.handling_requirements[0] === choice.handling &&
    profile.temperature_requirements.controlled === !!choice.limits.temp && profile.urgency === (choice.limits.urgent ? "urgent" : "ordinary"));
}
