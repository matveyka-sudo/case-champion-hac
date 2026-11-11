import { pointScorePresentation, observedScoreName } from "./scorePresentation";
import type { AnalysisResult, Schemas } from "../types";

export function pointDisplayName(pointId: string, feature?: Schemas["MapFeature"], ordinal?: number) {
  const name = feature?.name?.trim();
  if (name && ![pointId, feature?.entity_id, feature?.cell_id].includes(name)) return name;
  if (feature?.geometry?.type === "Point") {
    const [longitude, latitude] = feature.geometry.coordinates;
    return `Точка · ${latitude.toFixed(5)}, ${longitude.toFixed(5)}${feature.geometry_kind === "schematic" ? " (схема)" : ""}`;
  }
  return `Точка${ordinal === undefined ? "" : ` ${ordinal}`} · координаты не предоставлены`;
}

export function calculatedPoints(result: AnalysisResult, scores = result.scores) {
  const inventory = new Map<string, Schemas["MapFeature"]>();
  for (const feature of result.map_data?.features || []) {
    if (feature.entity_kind !== "sample_point") continue;
    const id = feature.point_id || feature.entity_id;
    if (!inventory.has(id)) inventory.set(id, feature);
  }
  const totals = new Map<string, Schemas["Score"][]>();
  for (const score of scores) {
    const id = score.subject.point_id;
    if (score.subject.kind !== "sample_point" || !id || score.subject.category != null) continue;
    totals.set(id, [...(totals.get(id) || []), score]);
  }
  const points = [...totals].flatMap(([pointId, values]) => {
    if (values.length !== 1) return [];
    const score = values[0];
    if (score.status !== "available" || score.value === null || !Number.isFinite(score.value)) return [];
    return [{ pointId, score, feature: inventory.get(pointId) }];
  }).map((point, i) => ({ ...point, name: pointDisplayName(point.pointId, point.feature, i + 1) }));
  const inventoryCount = inventory.size || null;
  const denominator = inventoryCount !== null && [...totals.keys()].every((id) => inventory.has(id)) ? inventoryCount : null;
  const territoryId = "city_id" in result.request ? result.request.area_id || result.request.city_id : null;
  const territory = scores.filter((score) => score.subject.kind === "territory" && score.subject.category == null &&
    territoryId !== null && score.subject.territory_id === territoryId);
  return { points, denominator, inventoryCount, territoryScore: territory.length === 1 ? territory[0] : null };
}
export type CalculatedPointOverview = ReturnType<typeof calculatedPoints>;

// A separate list: observed-route estimates never become canonical point totals.
export function routeEstimatePoints(result: AnalysisResult, scores = result.scores) {
  const canonical = calculatedPoints(result, scores);
  const inventory = new Map((result.map_data?.features || []).filter((feature) => feature.entity_kind === "sample_point")
    .map((feature) => [feature.point_id || feature.entity_id, feature]));
  const tasks = new Set(scores.filter((score) => score.name === observedScoreName && score.subject.kind === "sample_point" && score.subject.point_id).map((score) => score.subject.point_id!));
  const points = [...tasks].flatMap((pointId) => {
    const candidates = scores.filter((score) => score.subject.kind === "sample_point" && score.subject.point_id === pointId);
    const totals = candidates.filter((score) => score.subject.category == null);
    const presentation = pointScorePresentation(result, candidates, totals.length === 1 ? totals[0] : undefined);
    if (!presentation.observed || !presentation.score) return [];
    const feature = inventory.get(pointId);
    return [{ pointId, score: presentation.score, feature, name: pointDisplayName(pointId, feature) }];
  });
  return { ...canonical, points, denominator: canonical.denominator !== null && [...tasks].every((id) => inventory.has(id)) ? canonical.denominator : null };
}
