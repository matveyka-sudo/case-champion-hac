// Generated synthetic protocol shape: no real dataset or calculated production values.
import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import urban from "./api/urban_passenger.json" with { type: "json" };

export function pointOverviewFixture(count = 216, values: Record<number, number | null> = { 215: 0.497 }) {
  const data = decode("AnalysisResult", structuredClone(urban));
  const metricTemplate = data.metrics[0], scoreTemplate = data.scores[0];
  const city = "city-2";
  data.status = "partial";
  data.request = { ...data.request, analysis_kind: "urban_passenger", city_id: city, passenger_mode: "walk", categories: ["airport"], priorities: { airport: "medium" } } as Schemas["UrbanPassengerRequest"];
  data.metrics = [{ ...metricTemplate, metric_id: "synthetic-h", name: "urban_h_seconds", value: 1200,
    subject: { kind: "territory", territory_id: city, mode: "walk" } }];
  data.scores = [];
  data.map_data = { status: "available", completeness: "partial", features: [] };
  for (let i = 0; i < count; i++) {
    const point = `synthetic-point-${i}`, cell = `synthetic-cell-${i}`;
    const longitude = 20.3 + i * 0.001, latitude = 54.5 + i * 0.001;
    const value = values[i] ?? null;
    const subject: Schemas["SubjectRef"] = { kind: "sample_point", point_id: point, territory_id: city, mode: "walk", category: "airport" };
    data.metrics.push({ ...metricTemplate, metric_id: `synthetic-time-${i}`, name: "minimum_journey_seconds",
      subject, value: value === null ? null : 13776, status: value === null ? "unknown" : "available",
      reason_code: value === null ? "UNCOMPUTED_CATEGORY" : "CANONICAL_ACCESS_MODEL", completeness: "partial" });
    data.scores.push({ ...scoreTemplate, score_id: `synthetic-category-${i}`, name: "category_index", subject,
      value, status: value === null ? "unknown" : "available", completeness: "partial", ranking_eligible: false,
      reason_code: value === null ? "UNCOMPUTED_CATEGORY" : "CANONICAL_ACCESS_MODEL",
      components: [{ kind: "metric", id: `synthetic-time-${i}` }, { kind: "metric", id: "synthetic-h" }] });
    data.scores.push({ ...scoreTemplate, score_id: `synthetic-total-${i}`, name: "point_index", subject: { ...subject, category: null },
      value, status: value === null ? "unknown" : "available", completeness: "partial", ranking_eligible: false,
      reason_code: value === null ? "UNCOMPUTED_CATEGORY" : "CANONICAL_ACCESS_MODEL",
      components: [{ kind: "score", id: `synthetic-category-${i}` }] });
    data.map_data.features!.push({ entity_kind: "cell", entity_id: cell, cell_id: cell, point_id: point, territory_id: city,
      geometry: { type: "Polygon", coordinates: [[[longitude - .0002, latitude - .0002], [longitude + .0002, latitude - .0002], [longitude + .0002, latitude + .0002], [longitude - .0002, latitude - .0002]]] }, geometry_kind: "actual", source_refs: ["demo-evidence"] });
    data.map_data.features!.push({ entity_kind: "sample_point", entity_id: point, point_id: point, cell_id: cell, territory_id: city,
      name: null, geometry: { type: "Point", coordinates: [longitude, latitude] }, geometry_kind: "actual", source_refs: ["demo-evidence"] });
  }
  data.map_data.features!.push({ entity_kind: "destination", entity_id: "synthetic-airport", territory_id: city, name: "Синтетический аэропорт",
    geometry: { type: "Point", coordinates: [20.6, 54.8] }, geometry_kind: "actual", source_refs: ["demo-evidence"] });
  data.scores.push({ ...scoreTemplate, score_id: "synthetic-city-total", name: "territory_index",
    subject: { kind: "territory", territory_id: city, mode: "walk" }, status: "unknown", value: null, completeness: "partial", ranking_eligible: false,
    reason_code: "UNCOMPUTED_TERRITORY_POINTS", components: Array.from({ length: count }, (_, i) => ({ kind: "score", id: `synthetic-total-${i}` })) });
  data.limitations = ["SYNTHETIC_ONLY", "POINT_ONLY_SCOPE", "UNCOMPUTED_TERRITORY_POINTS"];
  return decode("AnalysisResult", data);
}
