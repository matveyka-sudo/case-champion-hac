import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { AnalysisResult, Schemas } from "../src/api/types";
import { CompactResultSummary } from "../src/widgets/CompactResultSummary";
import fixture from "./fixtures/api/urban_cargo.json";

afterEach(cleanup);

it("shows separate bounded indices and their access times from API components", () => {
  const result = structuredClone(fixture) as AnalysisResult;
  const times: Schemas["Metric"][] = ["port", "terminal"].map((kind, i) => ({
    ...result.metrics[0],
    metric_id: `access-${kind}`,
    name: "minimum_truck_access_seconds",
    value: 600 * (i + 1),
  }));
  const sites: Schemas["Score"][] = times.map((metric, i) => ({
    ...result.scores[0],
    score_id: `site-${i}`,
    name: "cargo_site_index",
    completeness: "partial",
    value: 50 + 10 * i,
    components: [{ kind: "metric", id: metric.metric_id }],
  }));
  const groups: Schemas["Score"][] = ["port", "terminal"].map((kind, i) => ({
    ...sites[i],
    score_id: `cargo-territory-${kind}`,
    name: "cargo_access_index",
    subject: { kind: "territory" },
    components: [{ kind: "score", id: sites[i].score_id }],
  }));
  result.scores = [...sites, ...groups];

  render(createElement(CompactResultSummary, {
    result, selection: null, scores: result.scores, metrics: times,
  }));
  expect(screen.getByText("Порты").parentElement?.textContent).toContain("50 из 100");
  expect(screen.getByText("Терминалы").parentElement?.textContent).toContain("60 из 100");
  expect(screen.getByText("10 мин")).toBeTruthy();
  expect(screen.getByText("20 мин")).toBeTruthy();
  expect(screen.queryByText("Время движения грузовика")).toBeNull();
});

it("puts the missing index first and keeps observed movement separate", () => {
  const result = structuredClone(fixture) as AnalysisResult;
  result.scores = [];
  result.limitations = ["CARGO_COMPATIBILITY_UNKNOWN", "TRUCK_ACCESS_UNKNOWN"];
  const metric: Schemas["Metric"] = {
    ...result.metrics[0], name: "observed_truck_network_seconds", status: "available", value: 600,
  };
  render(createElement(CompactResultSummary, {
    result, selection: null, scores: [], metrics: [metric],
  }));
  expect(screen.getByText("Индекс грузовой доступности")).toBeTruthy();
  expect(screen.getByText("Не рассчитан")).toBeTruthy();
  expect(screen.getByText("Время движения грузовика").parentElement?.textContent).toContain("10 мин");
  expect(screen.getByText(/Не хватает данных, чтобы подтвердить/)).toBeTruthy();
});
