import { Temporal } from "@js-temporal/polyfill";
import type { Category, Schemas, Territory } from "../types";

export type UrbanScope = Schemas["UrbanPassengerScope"];
export interface UrbanSelection {
  cityId: string;
  snapshotId: string;
  departureAt: string | null;
  areaId: string;
  mode: Schemas["UrbanPassengerRequest"]["passenger_mode"] | "";
  traffic: Schemas["UrbanPassengerRequest"]["traffic_mode"];
  categories: Category[];
}
export function scopeContainsTime(scope: UrbanScope, departure: string | null) {
  if (!departure) return false;
  try {
    return Temporal.Instant.compare(departure, scope.valid_from) >= 0 &&
      Temporal.Instant.compare(departure, scope.valid_to) < 0;
  } catch { return false; }
}
export function scopeAllowsArea(scope: UrbanScope, areaId: string) {
  return areaId ? !!scope.area_ids?.includes(areaId) : scope.whole_city;
}
export function urbanScopeAvailability(selection: UrbanSelection, scopes?: UrbanScope[] | null) {
  const published = scopes != null;
  const city = (scopes || []).filter((s) => s.city_id === selection.cityId);
  const time = city.filter((s) => scopeContainsTime(s, selection.departureAt));
  const snapshot = time.filter((s) => s.snapshot_id === selection.snapshotId);
  const area = snapshot.filter((s) => scopeAllowsArea(s, selection.areaId));
  const selectedMode = selection.mode;
  const mode = area.filter((s) => selectedMode && s.passenger_modes.includes(selectedMode));
  const traffic = mode.filter((s) => s.traffic_modes.includes(selection.traffic));
  const matching = traffic.filter((s) => selection.categories.length > 0 &&
    selection.categories.every((category) => s.categories.includes(category)));
  let problem: { field: string; message: string } | null = null;
  if (published) {
    if (!scopes.length) problem = { field: "scope", message: "Для городского расчёта нет зарегистрированных наборов параметров." };
    else if (!selection.cityId) problem = { field: "cityId", message: "Выберите город для проверки доступных параметров." };
    else if (!city.length) problem = { field: "cityId", message: "Для выбранного города нет зарегистрированного расчёта." };
    else if (!selection.departureAt) problem = { field: "date", message: "Проверьте дату и время в часовом поясе города." };
    else if (!time.length) problem = { field: "date", message: "Для этой даты и времени нет доступного набора расчёта." };
    else if (!snapshot.length) problem = { field: "snapshotId", message: "Для выбранного набора данных расчёт не зарегистрирован." };
    else if (!area.length) problem = { field: "areaId", message: selection.areaId ? "Для выбранного района расчёт не зарегистрирован." : "Расчёт всего города недоступен. Выберите поддерживаемый район." };
    else if (!selection.mode) problem = { field: "travel", message: "Выберите способ передвижения для выбранного города." };
    else if (!mode.length) problem = { field: "travel", message: "Для выбранных условий этот способ передвижения недоступен." };
    else if (!traffic.length) problem = { field: "traffic", message: "Для выбранного способа передвижения этот режим трафика недоступен." };
    else if (!selection.categories.length) problem = { field: "cats", message: "Выберите хотя бы одну доступную категорию." };
    else if (!matching.length) problem = { field: "cats", message: "Нет одного набора расчёта для всех выбранных категорий." };
    else if (matching.length !== 1) problem = { field: "scope", message: "Для этих параметров подходят несколько наборов расчёта. Уточните параметры." };
  }
  return { published, city, time, snapshot, area, mode, traffic, matching, problem };
}
export type UrbanScopeAvailability = ReturnType<typeof urbanScopeAvailability>;

export function scopePreset(scope: UrbanScope, current: UrbanSelection, scopes: UrbanScope[], territories: Territory[], areas: Schemas["AreaOption"][]) {
  const city = territories.find((t) => t.territory_id === scope.city_id && t.level === "city");
  if (!city) return null;
  try {
    let instant = current.departureAt && scopeContainsTime(scope, current.departureAt)
      ? Temporal.Instant.from(current.departureAt) : Temporal.Instant.from(scope.valid_from);
    const local = instant.toZonedDateTimeISO(city.timezone)
      .round({ smallestUnit: "minute", roundingIncrement: 15, roundingMode: "ceil" });
    instant = local.toInstant();
    if (!scopeContainsTime(scope, instant.toString())) return null;
    const areaId = current.areaId && scope.area_ids?.includes(current.areaId)
      ? current.areaId : scope.whole_city ? "" : scope.area_ids?.find((id) => areas.some((a) => a.city_id === scope.city_id && a.area_id === id));
    if (areaId === undefined) return null;
    const modes = [...new Set([...(current.mode ? [current.mode] : []), ...scope.passenger_modes])].filter((m) => scope.passenger_modes.includes(m));
    const trafficModes = [...new Set([current.traffic, ...scope.traffic_modes])].filter((m) => scope.traffic_modes.includes(m));
    const selected = current.categories.filter((c) => scope.categories.includes(c));
    for (const categories of [selected, scope.categories]) {
      if (!categories.length) continue;
      for (const mode of modes) for (const traffic of trafficModes) {
        const selection: UrbanSelection = { cityId: scope.city_id, snapshotId: scope.snapshot_id, departureAt: instant.toString(), areaId, mode, traffic, categories };
        const matching = urbanScopeAvailability(selection, scopes).matching;
        if (matching.length === 1 && matching[0].scope_id === scope.scope_id)
          return { selection: { ...selection, mode }, date: local.toPlainDate().toString(), slot: local.hour * 4 + local.minute / 15 };
      }
    }
  } catch { /* An invalid or ambiguous interval is not an available preset. */ }
  return null;
}
