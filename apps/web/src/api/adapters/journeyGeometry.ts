import type { Schemas } from "../types";

export const segmentGeometryNotice = "Показан участок маршрута; геометрия подходов не подтверждена";

const transitStyles: Record<string, { color: string; label: string }> = {
  walk: { color: "#475569", label: "Пешком" },
  bus: { color: "#1d4ed8", label: "Автобус" },
  tram: { color: "#c2410c", label: "Трамвай" },
  metro: { color: "#6d28d9", label: "Метро" },
  mcc: { color: "#be123c", label: "МЦК" },
};
export const transitLegend = Object.values(transitStyles);
export function transitLegStyle(serviceId: string | null) {
  return serviceId?.startsWith("cao:") ? transitStyles[serviceId.split(":")[1]] : undefined;
}

export function journeyLines(journey: Schemas["JourneyResult"]) {
  if (journey.status !== "available") return [];
  if (journey.geometry) return [{ geometry: journey.geometry, segment: false, schematic: journey.geometry_kind === "schematic", style: undefined }];
  return journey.legs.flatMap((leg) =>
    (leg.kind === "walk" || leg.kind === "ride") && leg.geometry
      ? [{ geometry: leg.geometry, segment: true, schematic: leg.service_id?.endsWith(":schematic") ?? false, style: transitLegStyle(leg.service_id) }] : [],
  );
}
