import type { AnalysisResult, Schemas } from "../types";

export function scenarioJourneys(
  baseline: AnalysisResult,
  scenario: Schemas["ScenarioResult"],
) {
  const inventory = new Map(
    [...baseline.journeys, ...(scenario.after_journeys || [])]
      .map((journey) => [journey.journey_id, journey]),
  );
  const ids = [...new Set(scenario.after.metrics.flatMap((metric) =>
    metric.journey_id ? [metric.journey_id] : [],
  ))];
  return {
    journeys: ids.flatMap((id) => inventory.has(id) ? [inventory.get(id)!] : []),
    missingJourneyIds: ids.filter((id) => !inventory.has(id)),
  };
}
