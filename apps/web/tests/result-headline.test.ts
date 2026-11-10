import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ResultHeadline } from "../src/widgets/ResultHeadline";
import { legGeometryFixture } from "./fixtures/leg-geometry";
afterEach(cleanup);
describe("saved result visual headline", () => {
  it("uses only saved full time and actual leg durations while keeping an unknown index neutral", () => {
    const result = legGeometryFixture(null), journey = result.journeys[0];
    const score = result.scores.find((value) => value.score_id === "synthetic-total-2")!;
    const view = render(createElement(ResultHeadline, { score, journey }));
    expect(view.container.querySelector(".bignum")?.textContent).toBe("10мин");
    expect(screen.getByText("Нет данных").getAttribute("data-score-band")).toBe("unknown");
    expect(screen.getByRole("img").children).toHaveLength(5);
    expect(journey.total_seconds).toBe(600.123457);
  });
  it("keeps zero numerical and creates no timeline for an unknown journey", () => {
    const result = legGeometryFixture(0), journey = result.journeys[0];
    journey.status = "unknown";
    render(createElement(ResultHeadline, { score: result.scores.find((value) => value.score_id === "synthetic-total-2"), journey }));
    expect(screen.getByText("0 из 100").getAttribute("data-score-band")).toBe("low");
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("prefers the named minimum operand over a different route time and never uses H as primary time", () => {
    const result = legGeometryFixture(0), journey = result.journeys[0];
    const metric = { ...result.metrics.find((value) => value.name === "minimum_journey_seconds" && value.status === "available")!, value: 180 };
    const view = render(createElement(ResultHeadline, { metric, journey }));
    expect(view.container.querySelector(".bignum")?.textContent).toBe("3мин");
    view.rerender(createElement(ResultHeadline, { metric: { ...metric, name: "urban_h_seconds" }, journey }));
    expect(view.container.querySelector(".bignum")).toBeNull();
  });
});
