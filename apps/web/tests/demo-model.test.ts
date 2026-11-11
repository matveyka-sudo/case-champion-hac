import { createElement } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { demoModelMarker, demoModelSelection, demoModelWarning, isDemoModel } from "../src/api/adapters/demoModel";
import { errorText, noticeTexts } from "../src/api/adapters/result";
import { decode } from "../src/api/validation";
import type { Schemas } from "../src/api/types";
import { DemoModelBanner } from "../src/widgets/DemoModelBanner";
import resultRaw from "./fixtures/api/urban_passenger.json";

afterEach(cleanup);
const limitations = [demoModelMarker, "NOT_A_REAL_CITY_RANKING"];
const preset = (snapshotId: string, labels = limitations): Schemas["AnalysisPreset"] => ({
  preset_id: snapshotId, label: "Server preset", scenario_operations: [], limitations: labels,
  request: { ...decode("AnalysisResult", structuredClone(resultRaw)).request, snapshot_id: snapshotId },
});

describe("server-declared synthetic transport provenance", () => {
  it.each(["moscow", "kaliningrad", "novorossiysk", "intercity", "pipeline"])("marks published %s data without a Moscow-specific warning", (scope) => {
    const snapshot = JSON.parse(readFileSync(resolve("../../data/demo/waydex-demo-2026-11-11.2/snapshots", `demo-${scope}-2026-11-11.2.json`), "utf8")) as { snapshot_id: string; source_versions: Record<string, string> };
    expect(snapshot.source_versions).toHaveProperty(demoModelMarker);
    expect(demoModelSelection(snapshot.snapshot_id, [preset(snapshot.snapshot_id)])).toBe(true);
    render(createElement(DemoModelBanner, { selectedLimitations: limitations, savedLimitations: limitations, hasSavedResult: true }));
    expect(screen.getByText(demoModelWarning)).toBeTruthy();
    expect(demoModelWarning).not.toContain("Москвы");
    expect(noticeTexts(limitations)).toEqual([
      "Демонстрационная транспортная модель: показатели рассчитаны на синтетических данных.",
      "Не является рейтингом фактической доступности городов.",
    ]);
  });

  it("does not infer synthetic data from IDs, a non-ranking limitation, or unrelated presets", () => {
    expect(isDemoModel(null)).toBe(false);
    expect(isDemoModel({ limitations: [] })).toBe(false);
    expect(isDemoModel({ limitations: ["NOT_A_REAL_CITY_RANKING"] })).toBe(false);
    expect(demoModelSelection("demo-moscow-anything", [])).toBe(false);
    expect(demoModelSelection("ordinary", [preset("demo-moscow-anything")])).toBe(false);
    expect(demoModelSelection("same", [preset("same"), preset("same", [])])).toBe(false);
    expect(demoModelSelection("demo-moscow-anything", [preset("demo-moscow-anything", [])])).toBe(false);
    expect(isDemoModel({ limitations })).toBe(true);
  });

  it("keeps saved-result provenance when the next selection changes", () => {
    const view = render(createElement(DemoModelBanner, { selectedLimitations: [], savedLimitations: limitations, hasSavedResult: true }));
    expect(screen.getByText(/Показатели сохранённого анализа относятся к демонстрационным/)).toBeTruthy();
    view.rerender(createElement(DemoModelBanner, { selectedLimitations: limitations, savedLimitations: [], hasSavedResult: true }));
    expect(screen.getByText(/На карте сохранён прежний результат/)).toBeTruthy();
    view.rerender(createElement(DemoModelBanner, { selectedLimitations: [], savedLimitations: [], hasSavedResult: true }));
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("explains exact-preset and unsupported group errors with an actionable alternative", () => {
    expect(errorText({ message: "REQUEST_OUTSIDE_SUPPORTED_PRESETS" })).toContain("готовый набор");
    expect(errorText({ message: "ANALYSIS_GROUP_CATEGORY_REQUEST_UNSUPPORTED" })).toContain("единый расчёт");
  });
});
