import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { noticeTexts, reasonText } from "../src/api/adapters/result";
import { CalculatedPoints } from "../src/widgets/CalculatedPoints";
import { ResultHeadline } from "../src/widgets/ResultHeadline";
import { RouteBreakdown } from "../src/widgets/ResultCardView";
import { calculatedPoints } from "../src/api/adapters/pointOverview";
import { partialWalkGeometryFixture } from "./fixtures/partial-walk-geometry";

afterEach(cleanup);

describe("saved result clarity", () => {
  it("explains accepted scenario assumptions and the canonical proven-category reason without inferring proof", () => {
    const codes = ["SAME_EXACT_STOP_TRANSFER_180S_IS_CONSUMER_MODEL", "WHOLE_EXECUTION_HYPOTHETICAL_SHIFT_MINUS360", "RESOURCE_FEASIBILITY_UNVERIFIED", "NO_OBSERVED_PASSENGER_DEMAND", "GLOBAL_MINIMUM_AND_CITY_INDEX_UNPROVEN", "INFRASTRUCTURE_AND_CALENDAR_UNVERIFIED"];
    for (const code of codes) expect(reasonText(code)).not.toBe("");
    expect(noticeTexts([...codes, codes[0], "FUTURE_ASSUMPTION_ONE", "FUTURE_ASSUMPTION_TWO"])).toHaveLength(codes.length + 1);
    expect(reasonText("USE_PROVEN_CATEGORY_INDEX")).toContain("подтверждённый индекс категории");
    expect(reasonText("USE_UNREVIEWED_CATEGORY_INDEX")).toBe("");
  });
  it("explains the live walk limitations and combines only identical presentation text", () => {
    const codes = ["APPROVED_PARTIAL_OD_EXECUTION", "CONNECTOR_UNCONFIRMED", "COORDINATE_QUANTIZATION_UNCERTAINTY", "NOT_A_CITY_RANKING", "OBSERVED_ROUTE_ESTIMATE", "PHYSICAL_CORRELATION_OFFSET_UNMEASURED", "REPORTED_DURATION_NOT_CERTIFIED_UPPER_BOUND", "SINGLE_FOUND_OBJECT_NOT_CATEGORY_MINIMUM", "VALHALLA_CORRELATION_UNAVAILABLE", "WALK_CONNECTOR_TIME_0_1_SECOND_PRECISION"];
    for (const code of codes) expect(reasonText(code)).not.toBe("");
    const values = [...codes, codes[0], "FUTURE_REASON_ONE", "FUTURE_REASON_TWO"], before = JSON.stringify(values);
    expect(noticeTexts(values)).toHaveLength(codes.length + 1);
    expect(noticeTexts(values).filter(text => text === "Для результата указано дополнительное ограничение.")).toHaveLength(1);
    expect(JSON.stringify(values)).toBe(before);
  });

  it("shows city context once across the canonical and estimated point lists", () => {
    const overview = calculatedPoints(partialWalkGeometryFixture());
    render(createElement("div", {},
      createElement(CalculatedPoints, { overview, selection: null, areaSelected: false, onSelect: vi.fn() }),
      createElement(CalculatedPoints, { overview, selection: null, areaSelected: false, onSelect: vi.fn(), estimate: true }),
    ));
    expect(screen.getAllByText(/Индекс города:/)).toHaveLength(1);
  });

  it("keeps the supplied destination visible and gives API time-bar numbers units", () => {
    const data = partialWalkGeometryFixture(), journey = data.journeys[0], before = JSON.stringify(data);
    render(createElement(ResultHeadline, { journey, destination: "Синтетическая школа" }));
    expect(screen.getByText("До объекта: Синтетическая школа")).toBeTruthy();
    expect(screen.getByRole("img").textContent).toBe("10 мин");
    expect(JSON.stringify(data)).toBe(before);
  });

  it("preserves the original demo time bar text", () => {
    render(createElement(RouteBreakdown, { segments: [{ kind: "walk", minutes: 9.7, valueText: "9,7", label: "Пешком" }] }));
    expect(screen.getByRole("img").textContent).toBe("9,7");
  });
});
