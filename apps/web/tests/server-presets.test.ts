import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { decode } from "../src/api/validation";
import { initialDraft, presetDraft, toPresetRequest, toRequest } from "../src/api/adapters/request";
import { CargoHandlingChanges } from "../src/widgets/CargoHandlingChanges";
import { ScenarioComparison } from "../src/widgets/ScenarioComparison";
import capabilities from "./fixtures/api/capabilities.json" with { type: "json" };
import territories from "./fixtures/api/territories.json" with { type: "json" };
import passenger from "./fixtures/api/intercity_passenger.json" with { type: "json" };
import urbanCargo from "./fixtures/api/urban_cargo.json" with { type: "json" };
import urban from "./fixtures/api/urban_passenger.json" with { type: "json" };
// Captured from TEST_ONLY backend PostgreSQL → HTTP cargo regression, not real observations.
import payloads from "./fixtures/api/backend-cargo-handling.json" with { type: "json" };

afterEach(cleanup);
const catalog = territories.map((item) => decode("TerritoryOption", item));
const analysis = decode("AnalysisResult", payloads.analysis);
const preset = { preset_id: "TEST_ONLY-cargo", label: "Грузовой контроль", request: analysis.request, scenario_operations: ["reduce_handling_wait"] };
const caps = decode("Capabilities", { ...capabilities, analysis_presets: [preset] });

describe("published requests and backend cargo DTOs", () => {
  it("decodes capabilities with empty and nonempty presets and the actual four backend payloads", () => {
    expect(decode("Capabilities", { ...capabilities, analysis_presets: [] }).analysis_presets).toEqual([]);
    expect(caps.analysis_presets).toEqual([preset]);
    expect(analysis.analysis_kind).toBe("intercity_cargo");
    expect(decode("ScenarioProposal", payloads.proposal).operation_type).toBe("reduce_handling_wait");
    expect(decode("ScenarioResult", payloads.scenario).service_changes).toEqual([]);
    expect(() => decode("Capabilities", { ...caps, invented: true })).toThrow();
    expect(() => decode("ScenarioResult", { ...payloads.scenario, invented: true })).toThrow();
  });
  it("submits an independent exact request, preserving explicit nulls, modes, timestamp and methodology", () => {
    const request = toPresetRequest(preset.preset_id, caps);
    expect(request).toEqual(preset.request);
    expect(request).not.toBe(caps.analysis_presets![0].request);
    expect(request.methodology).toBeTruthy();
    request.methodology!.config_hash = "changed-client-copy";
    expect(toPresetRequest(preset.preset_id, caps)).toEqual(preset.request);
  });
  it.each([urban, passenger, urbanCargo, payloads.analysis])("preserves the complete $analysis_kind published request", (fixture) => {
    const result = decode("AnalysisResult", fixture);
    const published = { ...preset, request: result.request };
    const available = decode("Capabilities", { ...capabilities, analysis_presets: [published] });
    expect(toPresetRequest(published.preset_id, available)).toEqual(result.request);
    const display = presetDraft(result.request, catalog);
    expect(display.kind).toBe(result.analysis_kind);
    expect(result.request).toEqual(fixture.request);
  });
  it("does not substitute another request for a missing or ambiguous preset", () => {
    expect(() => toPresetRequest("missing", caps)).toThrow(/больше недоступен/);
    expect(() => toPresetRequest(preset.preset_id, { ...caps, analysis_presets: [] })).toThrow();
    expect(() => toPresetRequest(preset.preset_id, { ...caps, analysis_presets: [preset, preset] })).toThrow();
  });
  it("keeps ordinary editable requests working without presets", () => {
    const request = decode("UrbanPassengerRequest", { ...urban.request, city_id: "city-1" });
    const draft = { ...initialDraft(), ...presetDraft(request, catalog) };
    expect(toRequest(draft, catalog, decode("Capabilities", { ...capabilities, analysis_presets: [] }))).toMatchObject({ analysis_kind: "urban_passenger", city_id: request.city_id, categories: request.categories });
  });
  it("renders actual handling durations and resources without a timetable shift", () => {
    const proposal = decode("ScenarioProposal", payloads.proposal);
    render(createElement(CargoHandlingChanges, { changes: proposal.fixed_parameters.handling_changes! }));
    expect(screen.getByText("120 мин")).toBeTruthy();
    expect(screen.getByText("60 мин")).toBeTruthy();
    expect(screen.getByText("Ресурс обработки: 1 → 2 ед.")).toBeTruthy();
    expect(screen.queryByText(/Дополнительное отправление|Сдвиг расписания|TEST_ONLY-crane/)).toBeNull();
  });
  it("keeps handling evidence visible in the before/after comparison", () => {
    const scenario = decode("ScenarioResult", payloads.scenario);
    render(createElement(ScenarioComparison, { scenario, metrics: analysis.metrics, scores: analysis.scores, selectionActive: false, scenarioView: true, onViewChange: () => {} }));
    expect(screen.getByLabelText("Изменения обработки груза")).toBeTruthy();
    expect(screen.getByText("Ресурс обработки: 1 → 2 ед.")).toBeTruthy();
  });
});
