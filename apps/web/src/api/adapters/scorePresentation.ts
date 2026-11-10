import type { AnalysisResult, Category, Schemas } from "../types";
export const observedScoreName = "observed_route_accessibility";
export const observedScoreLabel = "Предварительная оценка по найденному маршруту";
const numeric = (score: Schemas["Score"]) => score.status === "available" && score.value !== null && Number.isFinite(score.value);

export function displayedCategory(result: AnalysisResult, explicit?: Category | null) {
  if (explicit) return explicit;
  return result.request.analysis_kind === "urban_passenger" && result.request.categories.length === 1 ? result.request.categories[0] : null;
}
export function pointScorePresentation(result: AnalysisResult, candidates: Schemas["Score"][], canonical?: Schemas["Score"], explicitCategory?: Category | null) {
  const category = displayedCategory(result, explicitCategory);
  const mode = result.request.analysis_kind === "urban_passenger" ? result.request.passenger_mode : null;
  const exact = category ? candidates.filter((score) => score.subject.kind === "sample_point" && score.subject.category === category && score.name !== observedScoreName) : [];
  const primary = explicitCategory ? exact.length === 1 ? exact[0] : undefined : canonical;
  if (primary && numeric(primary)) return { score: primary, observed: false };
  if (!category || exact.some(numeric)) return { score: primary, observed: false };
  const observed = candidates.filter((score) => score.name === observedScoreName && score.subject.kind === "sample_point" &&
    score.subject.category === category && score.subject.mode === mode && numeric(score));
  return observed.length === 1 ? { score: observed[0], observed: true } : { score: primary, observed: false };
}
