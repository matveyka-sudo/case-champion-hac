import { createElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialDraft, toRequest } from "../src/api/adapters/request";
import { scopePreset, urbanScopeAvailability, type UrbanSelection } from "../src/api/adapters/urbanScopes";
import { UrbanScopeHelp } from "../src/widgets/UrbanScopeHelp";
import { decode } from "../src/api/validation";
import capabilities from "./fixtures/api/capabilities.json";
import territoryRows from "./fixtures/api/territories.json";
import { airportScope } from "./fixtures/urban-scopes";
const catalog = territoryRows.map((t) => decode("TerritoryOption", t));
const selection = (): UrbanSelection => ({
  cityId: "city-2", snapshotId: "demo-snapshot", departureAt: "2026-11-10T08:30:00+02:00",
  areaId: "", mode: "walk", traffic: "static", categories: ["airport"],
});
const draft = () => ({ ...initialDraft(), cityId: "city-2", date: "2026-11-10", travel: "walk" as const, cats: { c08: 2 } });
const caps = () => ({ ...decode("Capabilities", structuredClone(capabilities)), urban_passenger_scopes: [airportScope()] });
afterEach(cleanup);
describe("registered urban passenger scopes", () => {
  it("permits only the registered airport walking request without promising a result index", () => {
    expect(toRequest(draft(), catalog, caps())).toMatchObject({ passenger_mode: "walk", categories: ["airport"] });
    expect(() => toRequest({ ...draft(), travel: "pt" }, catalog, caps())).toThrow("способ передвижения недоступен");
    expect(() => toRequest({ ...draft(), cats: { c04: 2 } }, catalog, caps())).toThrow("всех выбранных категорий");
    expect(() => toRequest({ ...draft(), cityId: "city-1" }, catalog, caps())).toThrow("города нет зарегистрированного");
    expect(() => toRequest({ ...draft(), travel: "" }, catalog, caps())).toThrow("Выберите способ передвижения");
    expect(urbanScopeAvailability({ ...selection(), mode: "" }, [airportScope()]).problem?.field).toBe("travel");
  });
  it("cannot union categories from separate profiles or pick an ambiguous profile", () => {
    const c = caps();
    c.urban_passenger_scopes.push({ ...airportScope(), scope_id: "school-walk", categories: ["school"] });
    expect(() => toRequest({ ...draft(), cats: { c08: 2, c04: 2 } }, catalog, c)).toThrow("Нет одного набора");
    expect(toRequest({ ...draft(), cats: { c04: 2 } }, catalog, c)).toMatchObject({ categories: ["school"] });
    c.urban_passenger_scopes.push({ ...airportScope(), scope_id: "airport-overlap" });
    expect(() => toRequest(draft(), catalog, c)).toThrow("несколько наборов");
    expect(scopePreset(airportScope(), selection(), c.urban_passenger_scopes, catalog, [])).toBeNull();
  });
  it("keeps null/omitted legacy detail compatible and treats an empty list as explicitly unavailable", () => {
    for (const scopes of [null, undefined]) {
      const c = { ...caps(), urban_passenger_scopes: scopes };
      expect(toRequest({ ...draft(), travel: "pt", cats: { c04: 2 } }, catalog, c)).toMatchObject({ passenger_mode: "public_transport" });
      expect(urbanScopeAvailability(selection(), scopes).published).toBe(false);
    }
    expect(() => toRequest(draft(), catalog, { ...caps(), urban_passenger_scopes: [] })).toThrow("нет зарегистрированных наборов");
  });
  it("preserves an explicitly selected registered snapshot when legacy snapshot detail is omitted", () => {
    const c = caps();
    delete c.snapshots;
    expect(toRequest({ ...draft(), snapshotId: "demo-snapshot" }, catalog, c)).toMatchObject({ snapshot_id: "demo-snapshot" });
  });
  it("checks inclusive/exclusive instants independently of UTC and local date labels", () => {
    const scopes = [airportScope()];
    expect(urbanScopeAvailability({ ...selection(), departureAt: "2026-11-10T00:00:00+02:00" }, scopes).matching).toHaveLength(1);
    expect(urbanScopeAvailability({ ...selection(), departureAt: "2026-11-11T00:00:00+02:00" }, scopes).matching).toHaveLength(0);
    expect(toRequest({ ...draft(), slot: 0 }, catalog, caps())).toMatchObject({ departure_at: "2026-11-10T00:00:00+02:00" });
    expect(() => toRequest({ ...draft(), date: "2026-11-11", slot: 0 }, catalog, caps())).toThrow("даты и времени");
    expect(() => toRequest({ ...draft(), snapshotId: "stale" }, catalog, caps())).toThrow();
  });
  it("does not interpret whole_city as permission for arbitrary areas", () => {
    expect(urbanScopeAvailability({ ...selection(), areaId: "district" }, [airportScope()]).matching).toHaveLength(0);
    const scope = { ...airportScope(), whole_city: false, area_ids: ["district"] };
    expect(urbanScopeAvailability(selection(), [scope]).matching).toHaveLength(0);
    expect(urbanScopeAvailability({ ...selection(), areaId: "district" }, [scope]).matching).toHaveLength(1);
  });
  it("rechecks supported snapshot and traffic choices against the same registered profile", () => {
    const c = caps();
    c.snapshots!.push({ ...c.snapshots![0], snapshot_id: "other-snapshot" });
    c.traffic_modes.push("typical");
    expect(() => toRequest({ ...draft(), snapshotId: "other-snapshot" }, catalog, c)).toThrow("набора данных расчёт не зарегистрирован");
    expect(() => toRequest({ ...draft(), snapshotId: "demo-snapshot", traffic: "typical" }, catalog, c)).toThrow("режим трафика недоступен");
    c.areas = [{ area_id: "district", city_id: "city-2", name: "Район" }];
    c.urban_passenger_scopes[0] = { ...airportScope(), whole_city: false, area_ids: ["district"] };
    expect(() => toRequest({ ...draft(), snapshotId: "demo-snapshot" }, catalog, c)).toThrow("Расчёт всего города недоступен");
    expect(toRequest({ ...draft(), snapshotId: "demo-snapshot", areaId: "district" }, catalog, c)).toMatchObject({ area_id: "district" });
  });
  it("previews an available time without mutating the draft and avoids intervals with no 15-minute slot", () => {
    const current = { ...selection(), departureAt: "2026-11-12T08:30:00+02:00", mode: "public_transport" as const, categories: ["school" as const] };
    const before = structuredClone(current);
    const preset = scopePreset(airportScope(), current, [airportScope()], catalog, []);
    expect(preset).toMatchObject({ date: "2026-11-10", slot: 0, selection: { mode: "walk", categories: ["airport"] } });
    expect(current).toEqual(before);
    const tooShort = { ...airportScope(), valid_from: "2026-11-10T06:31:00Z", valid_to: "2026-11-10T06:40:00Z" };
    expect(scopePreset(tooShort, current, [tooShort], catalog, [])).toBeNull();
  });
  it("applies a profile only after the explicit action and states the result limits", () => {
    const c = caps();
    const d = { ...draft(), date: "2026-11-12", travel: "pt" as const, cats: { c04: 3 } };
    const current = { ...selection(), departureAt: "2026-11-12T08:30:00+02:00", mode: "public_transport" as const, categories: ["school" as const] };
    const apply = vi.fn();
    render(createElement(UrbanScopeHelp, { state: urbanScopeAvailability(current, c.urban_passenger_scopes), selection: current, draft: d, caps: c, catalog, onApply: apply }));
    expect(apply).not.toHaveBeenCalled();
    expect(screen.getByText("Доступный запуск не гарантирует полноту данных или наличие индекса.")).toBeTruthy();
    expect(screen.getByText(/Будет выбрано/).textContent).toContain("2026-11-10");
    fireEvent.click(screen.getByRole("button", { name: "Использовать доступные параметры" }));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-11-10", slot: 0, travel: "walk", cityId: "city-2", snapshotId: "demo-snapshot", cats: { c08: 2 } }));
    expect(d.travel).toBe("pt");
    expect(d.date).toBe("2026-11-12");
  });
  it("does not offer another city's registered preset while inspecting an unavailable city", () => {
    const c = caps();
    const current = { ...selection(), cityId: "city-1", mode: "" as const };
    render(createElement(UrbanScopeHelp, { state: urbanScopeAvailability(current, c.urban_passenger_scopes),
      selection: current, draft: { ...draft(), cityId: "city-1", travel: "" }, caps: c, catalog, onApply: vi.fn() }));
    expect(screen.getByText("Для выбранного города нет зарегистрированного расчёта.")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Доступный набор параметров" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Использовать доступные параметры" })).toBeNull();
  });
});
