import raw from "./catalog.json";
import type { Fixtures } from "./model";
export const catalog = raw as unknown as Omit<Fixtures, "cities">;
