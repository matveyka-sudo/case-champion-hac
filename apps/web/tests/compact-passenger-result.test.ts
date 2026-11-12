import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { AnalysisResult } from "../src/api/types";
import { CompactResultSummary } from "../src/widgets/CompactResultSummary";
import fixture from "./fixtures/api/intercity_passenger.json";

afterEach(cleanup);

it("displays a saved rail index, travel time and initial wait for the same mode", () => {
  const result = structuredClone(fixture) as AnalysisResult;
  if (result.request.analysis_kind !== "intercity_passenger") throw new Error("Unexpected fixture");
  result.request.passenger_modes = ["rail"];
  result.scores = [{ ...result.scores[0], name: "passenger_index" }];
  const metric = result.metrics[0];
  result.metrics = [
    { ...metric, name: "path_seconds", value: 5400 },
    { ...metric, metric_id: "wait", name: "initial_wait_seconds", value: 900 },
    { ...metric, metric_id: "car", name: "private_car_path_seconds", value: 60,
      subject: { ...metric.subject, mode: "private_car" } },
  ];
  render(createElement(CompactResultSummary, {
    result, selection: null, metrics: result.metrics,
  }));
  expect(screen.getByText("Железнодорожный транспорт")).toBeTruthy();
  expect(screen.getByText("50")).toBeTruthy();
  expect(screen.getByText("из 100")).toBeTruthy();
  expect(screen.getByText("Время в пути").parentElement?.textContent).toContain("1 ч 30 мин");
  expect(screen.getByText("До первого отправления").parentElement?.textContent).toContain("15 мин");
  expect(screen.queryByText("Личный автомобиль")).toBeNull();
});
