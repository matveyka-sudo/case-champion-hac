import { useEffect, useRef, useState } from "react";
import { ApiClient } from "../api/client";
import type { Schemas } from "../api/types";

export const api = new ApiClient();
export type Category = Schemas["AddressRequest"]["categories"][number];
export type AddressMode = "walk" | "car" | "pt";
export type Priority = "low" | "medium" | "high";
type Point = Schemas["Point"];
export type AddressPlace = { lat: number; lon: number; label: string; addressId?: string };
export const labels: Record<Category, string> = {
  hospital: "Больницы", school: "Школы", kindergarten: "Детские сады", college: "Колледжи",
  university: "Вузы", metro: "Метро", mcd: "МЦД", mck: "МЦК", mall: "Торговые центры", bus_stop: "Автобусные остановки",
};
export const categories = Object.keys(labels) as Category[];
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Запрос не выполнен. Попробуйте ещё раз.";

export function useAddressAccess() {
  const [departure, setDeparture] = useState("2026-11-13T08:30");
  const [mode, setMode] = useState<AddressMode>("walk");
  const [query, setQuery] = useState("");
  const [place, setPlace] = useState<AddressPlace | null>(null);
  const [picking, setPicking] = useState(false);
  const [matches, setMatches] = useState<Schemas["Address"][]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [selected, setSelected] = useState<Partial<Record<Category, Priority>>>({
    hospital: "medium", school: "medium", kindergarten: "medium",
  });
  const [result, setResult] = useState<{ data: Schemas["AddressResult"]; revision: number } | null>(null);
  const [comparison, setComparison] = useState<{ data: Schemas["AddressResult"]; filters: string }[]>([]);
  const [revision, setRevision] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const search = useRef<AbortController | null>(null);
  const calculation = useRef<AbortController | null>(null);

  useEffect(() => () => {
    search.current?.abort();
    calculation.current?.abort();
  }, []);

  const invalidate = () => {
    calculation.current?.abort();
    calculation.current = null;
    setRunning(false);
    setError("");
    setRevision((value) => value + 1);
  };
  const clearSearch = () => {
    search.current?.abort();
    setSearching(false);
    setMatches([]);
    setSearchError("");
  };
  const editQuery = (value: string) => {
    clearSearch();
    setQuery(value);
    setPlace(null);
    setPicking(false);
    invalidate();
  };
  const find = async () => {
    if (query.trim().length < 3) return;
    clearSearch();
    const controller = new AbortController();
    search.current = controller;
    setSearching(true);
    try {
      const found = await api.list("/address-access/addresses", "Address", controller.signal, { query: query.trim() });
      if (controller.signal.aborted) return;
      setMatches(found);
      if (!found.length) setSearchError("Адрес не найден. Уточните населённый пункт, улицу и номер дома.");
    } catch (failure) {
      if (!controller.signal.aborted) setSearchError(errorMessage(failure));
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  };
  const choosePlace = (next: AddressPlace) => {
    clearSearch();
    invalidate();
    setQuery(next.addressId ? next.label : "");
    setPlace(next);
    setPicking(false);
  };
  const choosePoint = (point: Point) => choosePlace({ ...point, label: "Выбранная точка" });
  const changeCategory = (category: Category, priority?: Priority) => {
    invalidate();
    setSelected((current) => ({ ...current, [category]: priority }));
  };
  const changeMode = (next: AddressMode) => {
    if (next === mode) return;
    invalidate();
    setMode(next);
  };
  const changeDeparture = (value: string) => { invalidate(); setDeparture(value); };
  const enabled = categories.filter((category) => selected[category] !== undefined);
  const filters = [mode, mode === "pt" ? departure : "", ...enabled.map((category) => category + ":" + selected[category])].join("|");
  const compareCompatible = !comparison.length || (comparison[0].filters === filters && comparison[0].data.source === result?.data.source);
  const compareAdded = comparison.some((entry) => entry.data.address.id === result?.data.address.id);
  const compare = () => {
    if (!result || result.revision !== revision || !compareCompatible || compareAdded || comparison.length >= 3) return;
    setComparison((entries) => [...entries, { data: result.data, filters }]);
  };
  const removeCompared = (id: string) => setComparison((entries) => entries.filter(({ data }) => data.address.id !== id));
  const calculate = async () => {
    if (!place || !enabled.length || (mode === "pt" && !departure) || calculation.current) return;
    const controller = new AbortController();
    calculation.current = controller;
    setPicking(false);
    setRunning(true);
    setError("");
    try {
      const location = place.addressId ? { address_id: place.addressId } : { lat: place.lat, lon: place.lon };
      const data = await api.request("/address-access/calculate", "AddressResult", {
        body: { ...location, mode, ...(mode === "pt" ? { departure_at: departure } : {}), categories: enabled, priorities: Object.fromEntries(enabled.map((category) => [category, selected[category]])) },
        signal: controller.signal, timeoutMs: 90000,
      });
      if (!controller.signal.aborted) setResult({ data, revision });
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    } finally {
      if (calculation.current === controller) {
        calculation.current = null;
        setRunning(false);
      }
    }
  };
  return {
    query, editQuery, find, matches, searching, searchError, place, choosePlace, choosePoint, picking, setPicking,
    comparison, compare, compareAdded, compareCompatible, removeCompared,
    mode, changeMode, departure, changeDeparture, selected, changeCategory, calculate, running, error, canRun: !!place && !!enabled.length && (mode !== "pt" || !!departure) && !running,
    result: result?.data ?? null, stale: !!result && result.revision !== revision,
  };
}
