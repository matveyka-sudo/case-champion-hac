import { loadFixtures } from "./loadFixtures";
export type CityId = "msk" | "kgd" | "nvr";
export type Travel = "pt" | "walk" | "car";
export type CatId =
  "c01" | "c02" | "c03" | "c04" | "c05" | "c06" | "c07" | "c08" | "c09";
export type LL = [number, number];
export type Leg = [string, number, string];
export interface Values {
  time: number;
  score: number;
  scenarioTime: number;
  scenarioScore: number;
  legs: Leg[];
  scenarioLegs: Leg[];
}
export interface MapObject {
  id: string;
  ll: LL;
  cat: CatId;
  canon?: boolean;
}
export interface Cell {
  id: string;
  i: number;
  j: number;
  ll: LL;
  pop: number;
  ndPartial?: boolean;
  zone?: boolean;
  canon?: boolean;
  eff?: string;
  near: Record<CatId, { obj: MapObject; km: number }>;
  values: Record<Travel, Record<CatId, Values>>;
}
export interface CityModel {
  key: CityId;
  cells: Cell[];
  objs: Record<CatId, MapObject[]>;
  canonCell: Cell | null;
  dLat: number;
  dLon: number;
}
export interface Category {
  id: CatId;
  name: string;
  short: string;
  icon: string;
}
export interface Place {
  n: string;
  ll: [number, number];
  tz: string;
  subj: string;
  cap: string;
  capll?: [number, number];
}
export interface City {
  name: string;
  c: LL;
  r: number;
  tz: string;
  grid: boolean;
}
export interface Transport {
  k: string;
  icon: string;
  t: string;
  s: string;
  img?: string;
  w?: number;
  h?: number;
  alt?: string;
  pos?: string;
  ph?: string;
}
export interface CargoNode {
  id: string;
  name: string;
  ll: LL;
  type: string;
  icon: string;
  h: string[];
}
export interface CargoObject {
  id: string;
  name: string;
  ll: LL;
  type: string;
  t: Record<string, number>;
}
export interface Passenger {
  st: string;
  time?: string;
  dep?: string;
  depW?: string;
  gap?: string;
  direct?: string;
  xfer?: string;
  nodes?: string;
  ex?: string[];
  msg?: string;
}
export interface Fixtures {
  categories: Category[];
  city: Record<CityId, City>;
  places: Record<string, Place>;
  referenceCities: [string, number, number][];
  seas: [string, number, number][];
  transport: Record<string, Transport[]>;
  canon: { route: LL[] };
  cities: Partial<Record<CityId, CityModel>>;
  cargo: {
    objs: CargoObject[];
    nodes: CargoNode[];
    stats: { ok: number; no: number; unk: number };
  };
  passenger: Record<string, Record<string, Passenger>>;
  passengerMeta: Record<string, [string, string]>;
  cargoMeta: Record<string, [string, string]>;
}
// Structured, precomputed reference fixtures. No production index formula executes in the browser.
export const fixtures = await loadFixtures();
export const categories = fixtures.categories;
export const categoryById = Object.fromEntries(
  categories.map((c) => [c.id, c]),
) as Record<CatId, Category>;
export function cellScore(
  cell: Cell,
  metric: string,
  travel: Travel,
  weights: Partial<Record<CatId, number>>,
  scn: boolean,
) {
  if (metric !== "overall")
    return (
      cell.values[travel][metric as CatId]?.[scn ? "scenarioScore" : "score"] ??
      null
    );
  const entries = Object.entries(weights) as [CatId, number][];
  const sum = entries.reduce((a, [, w]) => a + w, 0);
  return sum
    ? entries.reduce(
        (a, [id, w]) =>
          a + cell.values[travel][id][scn ? "scenarioScore" : "score"] * w,
        0,
      ) / sum
    : null;
}
export function aggregate(
  cells: Cell[],
  metric: string,
  travel: Travel,
  weights: Partial<Record<CatId, number>>,
  scn: boolean,
) {
  let sum = 0,
    pop = 0;
  cells.forEach((c) => {
    const score = cellScore(c, metric, travel, weights, scn);
    if (score != null && !c.ndPartial) {
      sum += score * c.pop;
      pop += c.pop;
    }
  });
  return pop ? sum / pop : null;
}
