import { createContext, useContext } from "react";
import type { CatId, CityId, Travel } from "../mocks/model";
export type Mode = "city" | "pax" | "cargo";
export type Status =
  | "idle"
  | "stale"
  | "run"
  | "ok"
  | "partial"
  | "nd"
  | "noroute"
  | "srcerr"
  | "baddate"
  | "pending"
  | "agentoff"
  | "demoonly";
export interface Params {
  mode: Mode;
  city: CityId | null;
  travel: Travel;
  cats: Partial<Record<CatId, number>>;
  paxScale: string;
  paxA: string;
  paxB: string;
  paxModes: Record<string, boolean>;
  cargoScale: string;
  cargoDir: string;
  cargoA: string;
  cargoB: string;
  cargoModes: Record<string, boolean>;
  profile: { group: string; handling: string; limits: Record<string, boolean> };
  pipeProduct: string;
  date: string;
  t: number;
}
export interface State {
  p: Params;
  applied: Params;
  status: Status;
  demoState: Status | null;
  metric: string;
  layers: Record<string, boolean>;
  cLayers: Record<string, boolean>;
  selection: {
    type: "cell" | "obj" | "wh" | "node";
    id: string;
    cat?: CatId;
  } | null;
  cardHidden: boolean;
  chatOpen: boolean;
  collapsed: boolean;
  mobileFilters: boolean;
  legendOpen: boolean;
  themeMode: "auto" | "light" | "dark";
  scenario: "none" | "run" | "done";
  scnView: "base" | "scn";
  paxTab: string;
  cargoTab: string;
  revision: number;
}
export type Action =
  | { type: "params"; patch: Partial<Params> }
  | { type: "ui"; patch: Partial<Omit<State, "p" | "applied" | "revision">> }
  | { type: "mode"; mode: Mode }
  | { type: "city"; city: CityId }
  | { type: "run" }
  | { type: "complete"; revision: number; params: Params }
  | { type: "reset" };
export function initialState(): State {
  const p: Params = {
    mode: "city",
    city: null,
    travel: "pt",
    cats: { c01: 3, c04: 2, c09: 1 },
    paxScale: "city",
    paxA: "msk",
    paxB: "kgd",
    paxModes: { air: true, rail: true, car: true, bus: true, sea: true },
    cargoScale: "city",
    cargoDir: "out",
    cargoA: "msk",
    cargoB: "nvr",
    cargoModes: { road: true, rail: true, air: true, water: true, pipe: true },
    profile: { group: "nonfood", handling: "container", limits: {} },
    pipeProduct: "oilprod",
    date: "2026-11-10",
    t: 34,
  };
  let themeMode: State["themeMode"] = "auto";
  try {
    const v = JSON.parse(localStorage.getItem("waydex:theme") || '"auto"');
    if (["auto", "light", "dark"].includes(v)) themeMode = v;
  } catch {
    /* storage unavailable */
  }
  return {
    p,
    applied: p,
    status: "ok",
    demoState: null,
    metric: "c01",
    layers: { index: true, objects: true, pop: false },
    cLayers: { ent: true, wh: true, port: true, term: true, rail: true },
    selection: null,
    cardHidden: false,
    chatOpen: window.innerWidth >= 1600,
    collapsed: window.innerWidth < 1100,
    mobileFilters: false,
    legendOpen: false,
    themeMode,
    scenario: "none",
    scnView: "base",
    paxTab: "air",
    cargoTab: "road",
    revision: 0,
  };
}
export function resultStatus(p: Params): Status {
  if (p.date < "2026-11-01" || p.date > "2026-11-30") return "baddate";
  if (p.date !== "2026-11-10" || p.t !== 34) return "demoonly";
  return p.mode === "city" && p.city === "kgd" ? "partial" : "ok";
}
export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "params": {
      const p = { ...s.p, ...a.patch };
      return {
        ...s,
        p,
        status:
          JSON.stringify(p) === JSON.stringify(s.applied) ? resultStatus(p) : "stale",
        demoState: null,
        revision: s.revision + 1,
        scenario: "none",
        scnView: "base",
      };
    }
    case "ui":
      return { ...s, ...a.patch };
    case "mode": {
      const p = { ...s.p, mode: a.mode };
      return {
        ...s,
        p,
        applied: p,
        status: resultStatus(p),
        demoState: null,
        selection: null,
        cardHidden: false,
        scenario: "none",
        scnView: "base",
        revision: s.revision + 1,
      };
    }
    case "city": {
      const p = { ...s.p, city: a.city };
      return {
        ...s,
        p,
        applied: p,
        status: resultStatus(p),
        demoState: null,
        selection: null,
        cardHidden: false,
        scenario: "none",
        scnView: "base",
        revision: s.revision + 1,
      };
    }
    case "run":
      return { ...s, status: "run", demoState: null };
    case "complete":
      return s.revision !== a.revision
        ? s
        : {
            ...s,
            applied: a.params,
            status: resultStatus(a.params),
          };
    case "reset": {
      const p = { ...s.p, date: "2026-11-10", t: 34 };
      return {
        ...s,
        p,
        applied: p,
        status: resultStatus(p),
        demoState: null,
        revision: s.revision + 1,
        scenario: "none",
        scnView: "base",
      };
    }
  }
}
export const Context = createContext<{
  state: State;
  dispatch: React.Dispatch<Action>;
} | null>(null);
export function useAtlas() {
  const c = useContext(Context);
  if (!c) throw new Error("AtlasProvider missing");
  return c;
}
export const currentStatus = (s: State) => s.demoState || s.status;
