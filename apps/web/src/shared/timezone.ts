import type { Params } from "../features/state";
import { fixtures } from "../mocks/model";
export function timezone(p: Params) {
  if (p.mode === "city" && p.city)
    return `${fixtures.city[p.city].name}, ${fixtures.city[p.city].tz}`;
  if (p.mode === "pax")
    return `${fixtures.places[p.paxA].n}, ${fixtures.places[p.paxA].tz}`;
  if (p.mode === "cargo")
    return p.cargoScale === "city"
      ? "Новороссийск, UTC+3"
      : `${fixtures.places[p.cargoA].n}, ${fixtures.places[p.cargoA].tz}`;
  return "Москва, UTC+3";
}
