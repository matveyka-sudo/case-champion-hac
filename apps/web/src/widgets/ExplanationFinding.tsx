import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { measureLabel, measureText, scoreBand, subjectMatches } from "../api/adapters/result";

export function ExplanationFinding({ finding, result, onSelect }: {
  finding: Schemas["Finding"];
  result: AnalysisResult | null;
  onSelect: (selection: Selection) => void;
}) {
  const metrics = [...new Set([...finding.metric_ids, ...finding.claims.map((c) => c.metric_id)])]
    .map((id) => ({ key: `metric:${id}`, kind: "metric", value: result?.metrics.find((v) => v.metric_id === id) }));
  const scores = [...new Set([...(finding.score_ids || []), ...(finding.score_claims || []).map((c) => c.score_id)])]
    .map((id) => ({ key: `score:${id}`, kind: "score", value: result?.scores.find((v) => v.score_id === id) }));
  const description = [...metrics, ...scores].reduce((text, { value }) =>
    value ? text.replaceAll(value.name, measureLabel(value.name)) : text, finding.text);
  return (
    <div>
      <p>{description}</p>
      {[...metrics, ...scores].map(({ key, kind, value }) => value ? (
        <button
          className="api-claim"
          key={key}
          data-score-band={"score_id" in value ? scoreBand(value) : undefined}
          onClick={() => {
            const feature = result?.map_data?.features?.find((f) => subjectMatches(value.subject, {
              entity_kind: f.entity_kind, entity_id: f.entity_id,
            }));
            if (feature) onSelect({ entity_kind: feature.entity_kind, entity_id: feature.entity_id });
          }}
        >
          {measureLabel(value.name)}: {measureText(value)}
        </button>
      ) : (
        <span key={key}>{kind === "score" ? "Индекс недоступен" : "Метрика недоступна"}</span>
      ))}
    </div>
  );
}
