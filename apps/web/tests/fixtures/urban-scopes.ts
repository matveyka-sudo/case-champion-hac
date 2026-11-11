import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import capabilityRows from "./api/capabilities.json" with { type: "json" };

export const airportScope = (): Schemas["UrbanPassengerScope"] => ({
  scope_id: "airport-walk", snapshot_id: "demo-snapshot", city_id: "city-2",
  passenger_modes: ["walk"], categories: ["airport"], traffic_modes: ["static"],
  valid_from: "2026-11-09T22:00:00Z", valid_to: "2026-11-10T22:00:00Z", whole_city: true, area_ids: [],
});
export function scopedCapabilities(scopes: Schemas["UrbanPassengerScope"][] | null = [airportScope()]) {
  return decode("Capabilities", { ...structuredClone(capabilityRows), urban_passenger_scopes: scopes });
}
