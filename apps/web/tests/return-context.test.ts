import { describe, expect, it } from "vitest";
import { returnContextFixture } from "./fixtures/return-context";
import { decode } from "../src/api/validation";

describe("public return context fixture", () => {
  it("preserves required map evidence and confirmed return chain operands", () => {
    const result = returnContextFixture();
    expect(decode("AnalysisResult", result)).toBe(result);
    expect(result.status).toBe("partial");
    for (const feature of result.map_data!.features!)
      expect(feature.source_refs).toEqual(["demo-evidence"]);
    for (const returned of result.last_returns) {
      const journey = result.journeys.find((j) => j.journey_id === returned.journey_id)!;
      expect(journey.point_id).toBe(returned.point_id);
      expect(journey.destination_object_id).toBe(returned.destination_object_id);
      expect(journey.requested_departure_at).toBe(returned.leave_at);
      expect(journey.legs[0].start_at).toBe(returned.first_departure_at);
      expect(journey.arrival_at).toBe(returned.arrival_at);
    }
  });
  it("continues rejecting the original source-less map fixture", () => {
    const result = returnContextFixture();
    result.map_data!.features![0].source_refs = [];
    expect(() => decode("AnalysisResult", result)).toThrow("API_CONTRACT_INVALID");
  });
});
