import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { journeyLines, segmentGeometryNotice } from "../src/api/adapters/journeyGeometry";
import { JourneyDetails } from "../src/widgets/JourneyDetails";
import { legGeometryFixture } from "./fixtures/leg-geometry";
import { partialWalkGeometryFixture } from "./fixtures/partial-walk-geometry";

afterEach(cleanup);
describe("independent movement-leg geometry", () => {
  it("retains two disjoint lines without manufacturing the missing approach or connecting gap", () => {
    const journey = legGeometryFixture().journeys[0];
    expect(journey.geometry).toBeNull();
    expect(journeyLines(journey).map((part) => part.geometry)).toEqual([journey.legs[1].geometry, journey.legs[3].geometry]);
    expect(journeyLines(journey).every((part) => part.segment && !part.schematic)).toBe(true);
    render(createElement(JourneyDetails, { journey, features: [] }));
    expect(screen.getByText(/геометрия участков/)).toBeTruthy();
    expect(screen.getByText(`${segmentGeometryNotice}.`)).toBeTruthy();
  });
  it("prefers the full journey geometry and avoids duplicate segments or partial-geometry claims", () => {
    const journey = legGeometryFixture().journeys[0];
    journey.geometry = partialWalkGeometryFixture().journeys[0].geometry;
    journey.geometry_kind = "actual";
    expect(journeyLines(journey)).toEqual([{ geometry: journey.geometry, segment: false, schematic: false }]);
    render(createElement(JourneyDetails, { journey, features: [] }));
    expect(screen.queryByText(`${segmentGeometryNotice}.`)).toBeNull();
  });
  it("keeps legacy missing geometry absent and never treats an unknown journey as a drawable route", () => {
    const journey = legGeometryFixture().journeys[0];
    for (const leg of journey.legs) delete leg.geometry;
    expect(journeyLines(journey)).toEqual([]);
    render(createElement(JourneyDetails, { journey, features: [] }));
    expect(screen.getByText(/геометрия отсутствует/)).toBeTruthy();
    const unknown = legGeometryFixture().journeys[0];
    unknown.status = "unknown";
    expect(journeyLines(unknown)).toEqual([]);
  });
  it("ignores geometry attached to a non-movement leg and preserves exact operands and null indices", () => {
    const result = legGeometryFixture(null), journey = result.journeys[0];
    journey.legs[1].kind = "wait_transfer";
    expect(journeyLines(journey).map((part) => part.geometry)).toEqual([journey.legs[3].geometry]);
    expect(result.metrics.find((metric) => metric.name === "minimum_journey_seconds" && metric.value !== null)?.value).toBe(600.123456789);
    expect(result.scores.filter((score) => score.subject.point_id === journey.point_id).every((score) => score.value === null)).toBe(true);
  });
});
