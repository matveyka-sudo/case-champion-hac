// Synthetic production-shaped protocol fixture; no real route or model proof.
import { decode } from "../../src/api/validation";
import { legGeometryFixture } from "./leg-geometry";
export function observedRouteFixture() {
  const result = legGeometryFixture(null);
  const journey = result.journeys[0];
  const metric = result.metrics.find((value) => value.name === "minimum_journey_seconds" && value.subject.point_id === journey.point_id)!;
  metric.name = "best_observed_time";
  metric.time_kind = "complete_chain";
  metric.journey_id = journey.journey_id;
  for (const point of result.scores.filter((score) => score.name === "point_index")) {
    result.scores.push({ ...point, score_id: `synthetic-observed-${point.subject.point_id}`, name: "observed_route_accessibility",
      subject: { ...point.subject, category: "airport", mode: "walk" }, reason_code: "MINIMUM_NOT_PROVEN", ranking_eligible: false,
      completeness: "partial", value: point.subject.point_id === journey.point_id ? 0 : null,
      status: point.subject.point_id === journey.point_id ? "available" : "unknown" });
  }
  return decode("AnalysisResult", result);
}
