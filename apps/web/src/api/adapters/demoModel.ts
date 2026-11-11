import type { Schemas } from "../types";

export const demoModelMarker = "DEMO_SYNTHETIC_TRANSPORT_MODEL";
export const demoModelWarning = "Демонстрационная транспортная модель — не оценка фактической доступности и не рейтинг городов";

// Classification follows the validated server disclosure, never a user-facing ID
// or a global capability limitation that could also describe another profile.
export function isDemoModel(value?: { limitations?: readonly string[] } | null) {
  return value?.limitations?.includes(demoModelMarker) === true;
}

export function demoModelSelection(snapshotId: string | null | undefined, presets: readonly Schemas["AnalysisPreset"][] = []) {
  const matching = snapshotId ? presets.filter((preset) => preset.request.snapshot_id === snapshotId) : [];
  return matching.length > 0 && matching.every(isDemoModel);
}
