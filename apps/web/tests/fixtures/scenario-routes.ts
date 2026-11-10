// Synthetic protocol fixtures; these do not calculate or prove production routes.
import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import urbanRaw from "./api/urban_passenger.json";
import intercityRaw from "./api/intercity_passenger.json";
import scenarioRaw from "./api/scenario.json";

export function scenarioRouteFixture() {
  const baseline = decode("AnalysisResult", structuredClone(urbanRaw));
  const scenario = decode("ScenarioResult", structuredClone(scenarioRaw));
  function journey(id: string, destination: string, seconds: number, longitude: number): Schemas["JourneyResult"] {
    const departure = baseline.request.departure_at;
    const arrival = new Date(Date.parse(departure) + seconds * 1000).toISOString();
    return {
      ...decode("JourneyResult", structuredClone(intercityRaw.journeys[0])),
      journey_id: id, scope: "urban", point_id: "demo-point", reference_id: null,
      destination_object_id: destination, destination_node_id: destination,
      requested_departure_at: departure, departure_at: departure, arrival_at: arrival,
      total_seconds: seconds, path_seconds: seconds,
      legs: [{
        mode: "public_transport", kind: "ride", from_node_id: "demo-a", to_node_id: destination,
        start_at: departure, end_at: arrival, duration_seconds: seconds,
        service_id: "synthetic-service", evidence_refs: ["demo-evidence"],
      }],
      geometry: { type: "LineString", coordinates: [[37, 55], [longitude, 55.01]], evidence_refs: ["demo-evidence"] },
      geometry_kind: "actual",
    };
  }
  baseline.journeys = [
    journey("baseline-winner", "school-fast", 1800, 37.01),
    journey("baseline-other", "school-other", 2400, 37.02),
  ];
  baseline.map_data = { status: "available", completeness: "full", features: [
    { entity_kind: "sample_point", entity_id: "demo-point", name: "Точка анализа", territory_id: "city-1", geometry: { type: "Point", coordinates: [37, 55] }, geometry_kind: "actual", source_refs: ["demo-evidence"] },
    { entity_kind: "destination", entity_id: "school-fast", name: "Ближайшая школа", territory_id: "city-1", geometry: { type: "Point", coordinates: [37.01, 55.01] }, geometry_kind: "actual", source_refs: ["demo-evidence"] },
    { entity_kind: "destination", entity_id: "school-other", name: "Другая школа", territory_id: "city-1", geometry: { type: "Point", coordinates: [37.02, 55.01] }, geometry_kind: "actual", source_refs: ["demo-evidence"] },
  ] };
  baseline.metrics[0].journey_id = "baseline-winner";
  baseline.metrics.push({
    ...baseline.metrics[0], metric_id: "other-time", name: "Synthetic other object time", value: 2400,
    subject: { ...baseline.metrics[0].subject, destination_id: "school-other" }, journey_id: "baseline-other",
  });
  scenario.before = { metrics: structuredClone(baseline.metrics), scores: structuredClone(baseline.scores) };
  scenario.after = { metrics: structuredClone(baseline.metrics), scores: structuredClone(baseline.scores) };
  scenario.after.metrics[1] = { ...scenario.after.metrics[1], metric_id: "other-time-after", value: 2700, journey_id: "scenario-other" };
  scenario.after_journeys = [journey("scenario-other", "school-other", 2700, 37.025)];
  scenario.deltas = [{
    effect_id: "other-worsened", kind: "metric", before_id: "other-time", after_id: "other-time-after",
    unit: "seconds", delta: -300, sign_convention: "before_minus_after", outcome: "worsened", reason_code: null, scope: "transport",
  }];
  scenario.worsened_metric_ids = ["other-time-after"];
  return { baseline, scenario };
}
