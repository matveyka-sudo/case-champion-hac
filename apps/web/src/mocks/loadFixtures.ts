import type { Fixtures, CityModel, Cell, CityId } from "./model";
interface Descriptor extends Omit<CityModel, "cells" | "canonCell"> {
  parts: string[];
  canonCellId: string | null;
}
interface Manifest {
  catalog: Omit<Fixtures, "cities">;
  cities: Partial<Record<CityId, Descriptor>>;
}
async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok)
    throw new Error("Не удалось загрузить демонстрационные данные");
  return response.json() as Promise<T>;
}
export async function loadFixtures(): Promise<Fixtures> {
  const manifest = await readJson<Manifest>("/data/demo/index.json");
  const entries = await Promise.all(
    Object.entries(manifest.cities).map(async ([key, descriptor]) => {
      const { parts, canonCellId, ...metadata } = descriptor;
      const cells = (
        await Promise.all(
          parts.map((name) => readJson<Cell[]>(`/data/demo/${name}`)),
        )
      ).flat();
      return [
        key,
        {
          ...metadata,
          cells,
          canonCell: cells.find((c) => c.id === canonCellId) || null,
        },
      ] as const;
    }),
  );
  return { ...manifest.catalog, cities: Object.fromEntries(entries) };
}
