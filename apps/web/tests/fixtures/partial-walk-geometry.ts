// Synthetic UI protocol fixture, not a real route or a numerical engine proof.
import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import { pointOverviewFixture } from "./point-overview";

export function partialWalkGeometryFixture() {
  const data = pointOverviewFixture(3, { 2: .497 });
  data.request.departure_at = "2026-11-10T08:30:00+02:00";
  const exact = data.metrics.find((m) => m.metric_id === "synthetic-time-2")!;
  exact.value = 600.123456789;
  exact.journey_id = null;
  const seconds = 600.123457;
  const departure = data.request.departure_at, arrival = "2026-11-10T08:40:00.123457+02:00";
  const airport = data.map_data!.features!.find((f) => f.entity_kind === "destination")!;
  airport.geometry = { type: "Point", coordinates: [20.312, 54.503] };
  const journey: Schemas["JourneyResult"] = {
    journey_id: "synthetic-partial-walk", scope: "urban", point_id: "synthetic-point-2",
    origin_node_id: "synthetic-origin", destination_node_id: "synthetic-destination", destination_object_id: airport.entity_id,
    requested_departure_at: departure, departure_at: departure, arrival_at: arrival,
    total_seconds: seconds, path_seconds: seconds, initial_wait_seconds: 0, total_wait_seconds: 0,
    legs: [{ kind: "walk", mode: "walk", from_node_id: "synthetic-origin", to_node_id: "synthetic-destination",
      start_at: departure, end_at: arrival, duration_seconds: seconds, service_id: null, evidence_refs: ["demo-evidence"] }],
    geometry: { type: "LineString", coordinates: [[20.302, 54.502], [20.307, 54.5025], [20.312, 54.503]], evidence_refs: ["demo-evidence"] },
    geometry_kind: "actual", status: "available", completeness: "partial", reason_code: "CANONICAL_ACCESS_MODEL",
    snapshot_id: data.snapshot_id, traffic_mode: "static", time_kind: "complete_chain", reference_id: null, evidence_refs: ["demo-evidence"],
  };
  data.journeys = [journey];
  data.metrics.push({ ...exact, metric_id: "synthetic-timeline", name: "total_seconds", value: seconds,
    journey_id: journey.journey_id, time_kind: "complete_chain" });
  data.limitations.push("MODEL_TIMELINE_MICROSECOND_PRECISION", "PINNED_TILE_SHAPE_6_DECIMAL_ENCODING");
  return decode("AnalysisResult", data);
}
