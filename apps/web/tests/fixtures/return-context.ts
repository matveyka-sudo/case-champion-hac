// Synthetic, source-linked return chains for the public context-selection test.
import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import urban from "./api/urban_passenger.json" with { type: "json" };
import intercity from "./api/intercity_passenger.json" with { type: "json" };

export function returnContextFixture() {
  const data = decode("AnalysisResult", structuredClone(urban));
  data.status = "partial";
  if (data.request.analysis_kind === "urban_passenger") data.request.passenger_mode = "public_transport";
  data.metrics[0].subject.mode = "public_transport";
  data.metrics.push({
    ...data.metrics[0], metric_id: "destination-time", name: "Synthetic destination time",
    subject: { kind: "category", destination_id: "school-a", category: "school", mode: "public_transport" },
  });
  data.scores[0].completeness = "partial";
  data.scores[0].subject.mode = "public_transport";
  data.map_data = { status: "available", completeness: "full", features: [
    ["sample_point", "demo-point", "Выбранная точка", 37],
    ["sample_point", "other-point", "Другая точка", 37.01],
    ["destination", "school-a", "Школа А", 37.02],
    ["destination", "school-b", "Школа Б", 37.03],
  ].map(([kind, id, name, longitude]) => ({
    entity_kind: kind as "sample_point" | "destination", entity_id: id as string,
    name: name as string, territory_id: "city-1",
    geometry: { type: "Point", coordinates: [longitude as number, 55] },
    geometry_kind: "actual", source_refs: ["demo-evidence"],
  })) };
  data.last_returns = [
    ["demo-point", "school-a", "18:00"],
    ["other-point", "school-a", "19:00"],
    ["demo-point", "school-b", "20:00"],
  ].map(([point, destination, time], i): Schemas["LastReturn"] => {
    const departure = `2026-11-09T${time}:00+03:00`;
    const arrival = new Date(Date.parse(departure) + 1800 * 1000).toISOString();
    const journeyId = `return-${i}`;
    const from = `return-from-${destination}`, to = `return-to-${point}`;
    data.journeys.push({
      ...decode("JourneyResult", structuredClone(intercity.journeys[0])),
      journey_id: journeyId, scope: "urban", point_id: point, destination_object_id: destination,
      origin_node_id: from, destination_node_id: to, reference_id: null,
      requested_departure_at: departure, departure_at: departure, arrival_at: arrival,
      legs: [{
        kind: "ride", mode: "public_transport", from_node_id: from, to_node_id: to,
        start_at: departure, end_at: arrival, duration_seconds: 1800,
        service_id: `synthetic-return-${i}`, evidence_refs: ["demo-evidence"],
      }],
    });
    return {
      point_id: point, destination_object_id: destination, journey_id: journeyId,
      leave_at: departure, first_departure_at: departure, arrival_at: arrival,
      service_date: "2026-11-09", timezone: "Europe/Moscow", status: "available",
      reason_code: null, evidence_refs: ["demo-evidence"],
    };
  });
  return decode("AnalysisResult", data);
}
