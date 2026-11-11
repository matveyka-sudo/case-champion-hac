import { describe, expect, it } from "vitest";
import { savedRequestDraft } from "../src/api/adapters/savedRequest";
import { decode } from "../src/api/validation";
import capsRaw from "./fixtures/api/capabilities.json";
import cargoRaw from "./fixtures/api/urban_cargo.json";
import catalogRaw from "./fixtures/api/territories.json";

describe("historical cargo profile representation", () => {
  it.each(["nonurgent", "multiple-handling"])("warns for a known %s profile without replacing its identity or requirements", (kind) => {
    const caps = decode("Capabilities", structuredClone(capsRaw));
    const profile = caps.cargo_profiles.find((value) => value.kind === "delivery")!;
    if (profile.kind !== "delivery") throw new Error("fixture delivery profile missing");
    if (kind === "nonurgent") profile.urgency = "nonurgent";
    else profile.handling_requirements = ["container", "general"];
    const baseline = decode("AnalysisResult", structuredClone(cargoRaw));
    if (baseline.request.analysis_kind !== "urban_cargo") throw new Error("fixture cargo request missing");
    baseline.request.city_id = "city-1";
    const before = structuredClone({ profile, request: baseline.request });
    const restored = savedRequestDraft(baseline, catalogRaw.map((value) => decode("TerritoryOption", value)), caps);
    expect(restored.cargoProfile).toBeNull();
    expect(restored.warning).toContain("нельзя точно представить текущими фильтрами");
    expect(restored.draft.profileId).toBe(profile.profile_id);
    expect({ profile, request: baseline.request }).toEqual(before);
  });
});
