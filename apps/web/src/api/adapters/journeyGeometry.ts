import type { Schemas } from "../types";

export const segmentGeometryNotice = "Показан участок маршрута; геометрия подходов не подтверждена";

export function journeyLines(journey: Schemas["JourneyResult"]) {
  if (journey.status !== "available") return [];
  if (journey.geometry) return [{ geometry: journey.geometry, segment: false, schematic: journey.geometry_kind === "schematic" }];
  return journey.legs.flatMap((leg) =>
    (leg.kind === "walk" || leg.kind === "ride") && leg.geometry
      ? [{ geometry: leg.geometry, segment: true, schematic: false }] : [],
  );
}
