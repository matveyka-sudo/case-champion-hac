import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { scenarioJourneys } from "../src/api/adapters/scenario";
import { deltaText } from "../src/api/adapters/result";
import { JourneyDetails } from "../src/widgets/JourneyDetails";
import { decode } from "../src/api/validation";
import { scenarioRouteFixture } from "./fixtures/scenario-routes";
afterEach(cleanup);
describe("scenario journey publication", () => {
  it("resolves changed nonwinner routes and explicitly reused baseline routes without mutating the baseline", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    const before = structuredClone(baseline);
    const resolved = scenarioJourneys(baseline, scenario);
    expect(resolved.journeys.map((j) => j.journey_id)).toEqual(["baseline-winner", "scenario-other"]);
    expect(resolved.journeys[1].total_seconds).toBe(2700);
    expect(resolved.journeys).not.toContain(baseline.journeys[1]);
    expect(resolved.missingJourneyIds).toEqual([]);
    expect(baseline).toEqual(before);
    expect(decode("ScenarioResult", scenario).after_journeys).toHaveLength(1);
  });
  it("does not interpret omitted additions as a complete scenario inventory or restore a lost route", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    delete scenario.after_journeys;
    scenario.after.metrics = [scenario.after.metrics[0]];
    expect(scenarioJourneys(baseline, scenario).journeys.map((j) => j.journey_id)).toEqual(["baseline-winner"]);
    scenario.after.metrics = [];
    expect(scenarioJourneys(baseline, scenario).journeys).toEqual([]);
    expect(baseline.journeys).toHaveLength(2);
  });
  it("reports unresolved references instead of substituting another baseline route", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    scenario.after_journeys = [];
    const resolved = scenarioJourneys(baseline, scenario);
    expect(resolved.journeys.map((j) => j.journey_id)).toEqual(["baseline-winner"]);
    expect(resolved.missingJourneyIds).toEqual(["scenario-other"]);
  });
  it("keeps scenario time, destination and chain visible when geometry is absent", () => {
    const { baseline, scenario } = scenarioRouteFixture();
    const journey = { ...scenario.after_journeys![0], geometry: null, geometry_kind: "none" as const };
    render(createElement(JourneyDetails, {
      journey, scenario: true, features: baseline.map_data!.features!,
      originTimezone: "Europe/Moscow", destinationTimezone: "Europe/Moscow",
    }));
    expect(screen.getByText(/Путь сценария/).textContent).toContain("геометрия отсутствует");
    expect(screen.getByText(/Другая школа/).textContent).toContain("45 мин");
    expect(screen.getByText(/Движение/).textContent).toContain("45 мин");
  });
  it("preserves negative and new/lost connection effects without fabricating time savings", () => {
    const { scenario } = scenarioRouteFixture();
    expect(deltaText(scenario.deltas[0])).toContain("Ухудшение · -5 мин");
    for (const [outcome, label] of [["new_connection", "Связь появилась"], ["lost_connection", "Связь потеряна"]] as const) {
      const text = deltaText({ ...scenario.deltas[0], outcome, delta: null });
      expect(text).toContain(`${label} · Без численной разности`);
      expect(text).not.toContain("-5 мин");
    }
  });
});
