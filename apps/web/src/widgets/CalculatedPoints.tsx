import { useRef, useState } from "react";
import type { Selection } from "../api/types";
import type { CalculatedPointOverview } from "../api/adapters/pointOverview";
import { measureText, scoreBand } from "../api/adapters/result";

export function CalculatedPoints({ overview, selection, areaSelected, onSelect, estimate = false, showTerritory = !estimate }: {
  estimate?: boolean;
  showTerritory?: boolean;
  overview: CalculatedPointOverview;
  selection: Selection | null;
  areaSelected: boolean;
  onSelect: (selection: Selection) => void;
}) {
  const details = useRef<HTMLDetailsElement>(null);
  const [expanded, setExpanded] = useState(false);
  const { territoryScore } = overview;
  const count = `${overview.points.length}${overview.denominator === null ? "" : ` из ${overview.denominator}`}`;
  return (
    <div className="api-calculated-points" aria-label="Обзор рассчитанных точек">
      {showTerritory && <p className="api-point-territory">
        {areaSelected ? "Индекс выбранной территории" : "Индекс города"}: {territoryScore ? measureText(territoryScore) : "не предоставлен"}
        {territoryScore?.completeness === "partial" && " · частичная оценка"}
      </p>}
      <details ref={details} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>{estimate ? "Точки с предварительной оценкой" : "Точки с общим индексом"} ({count})</summary>
        <p className="fhint">{estimate ? "Предварительная оценка по найденному маршруту. Минимум не подтверждён; оценка не заменяет общий индекс точки или города." : "Общие численные индексы отдельных точек не заменяют индекс города. Неизвестные показатели остаются в общем результате."}</p>
        {overview.denominator === null && <p className="fhint">{overview.inventoryCount === null
          ? "Общее число точек для этого списка не предоставлено."
          : "Переданная карта содержит не все точки из результата. Соотношение не определено."}</p>}
        {expanded && (overview.points.length ? (
          <ul>
            {overview.points.map((point) => (
              <li key={point.pointId}>
                <button
                  className="api-claim api-point-choice"
                  data-score-band={scoreBand(point.score)}
                  aria-pressed={selection?.entity_kind === "sample_point" && selection.entity_id === point.pointId}
                  aria-controls="apiResultCard"
                  onClick={() => {
                    if (details.current) details.current.open = false;
                    setExpanded(false);
                    onSelect({ entity_kind: "sample_point", entity_id: point.pointId });
                  }}
                >
                  <span>{point.name}{point.score.completeness === "partial" && <small>Частичная оценка</small>}</span>
                  <b>{measureText(point.score)}</b>
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="fhint">Численные общие индексы точек в ответе не предоставлены.</p>)}
      </details>
    </div>
  );
}
