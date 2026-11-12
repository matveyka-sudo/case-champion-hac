import { useRef, useState } from "react";
import type { AnalysisResult, Selection } from "../api/types";

export function CalculatedJourneys({ result, onSelect }: {
  result: AnalysisResult;
  onSelect: (selection: Selection) => void;
}) {
  const details = useRef<HTMLDetailsElement>(null);
  const [expanded, setExpanded] = useState(false);
  const journeys = result.journeys.filter((journey) => journey.status === "available" && journey.point_id &&
    journey.time_kind === "complete_chain" && journey.total_seconds !== null && Number.isFinite(journey.total_seconds));
  if (!journeys.length) return null;
  return (
    <div className="api-calculated-points" aria-label="Обзор рассчитанных маршрутов">
      <details ref={details} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>Рассчитанные маршруты ({journeys.length})</summary>
        <p className="fhint">Показаны маршруты из сохранённого результата. Наличие маршрута не подтверждает индекс точки или полноту каталога.</p>
        {expanded && <ul>
          {journeys.map((journey) => {
            const destination = result.map_data?.features?.find((feature) => feature.entity_id === (journey.destination_object_id || journey.destination_node_id));
            const name = destination?.name?.trim();
            const label = name && name !== destination?.entity_id ? name : "Объект назначения";
            return (
              <li key={journey.journey_id}>
                <button className="api-claim api-point-choice" aria-controls="apiResultCard" onClick={() => {
                  if (details.current) details.current.open = false;
                  setExpanded(false);
                  onSelect({ entity_kind: "sample_point", entity_id: journey.point_id! });
                }}>
                  <span>{label}</span>
                  <b>{(journey.total_seconds! / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин · полное время</b>
                </button>
              </li>
            );
          })}
        </ul>}
      </details>
    </div>
  );
}
