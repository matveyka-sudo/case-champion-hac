// Synthetic disjoint network segments; no real route or numerical proof.
import type { Schemas } from "../../src/api/types";
import { decode } from "../../src/api/validation";
import { partialWalkGeometryFixture } from "./partial-walk-geometry";

export function legGeometryFixture(index: number | null = .497) {
  const result = partialWalkGeometryFixture();
  const journey = result.journeys[0];
  journey.geometry = null;
  journey.geometry_kind = "none";
  const times = ["08:30:00", "08:31:00", "08:34:00", "08:36:00", "08:39:00", "08:40:00.123457"];
  const durations = [60, 180, 120, 180, 60.123457];
  const nodes = [journey.origin_node_id, "synthetic-network-a", "synthetic-network-b", "synthetic-network-c", "synthetic-network-d", journey.destination_node_id];
  journey.legs = durations.map((duration_seconds, i): Schemas["Leg"] => ({
    kind: "walk", mode: "walk", from_node_id: nodes[i], to_node_id: nodes[i + 1],
    start_at: `2026-11-10T${times[i]}+02:00`, end_at: `2026-11-10T${times[i + 1]}+02:00`,
    duration_seconds, service_id: null, evidence_refs: ["demo-evidence"],
    ...(i === 1 ? { geometry: { type: "LineString", coordinates: [[20.303, 54.5022], [20.305, 54.5024]], evidence_refs: ["demo-evidence"] } } : {}),
    ...(i === 3 ? { geometry: { type: "LineString", coordinates: [[20.308, 54.5026], [20.31, 54.5028]], evidence_refs: ["demo-evidence"] } } : {}),
  }));
  for (const score of result.scores.filter((score) => score.subject.point_id === journey.point_id)) {
    score.value = index;
    score.status = index === null ? "unknown" : "available";
    score.reason_code = index === null ? "MINIMUM_TIME_UNPROVEN" : "CANONICAL_ACCESS_MODEL";
  }
  return decode("AnalysisResult", result);
}
