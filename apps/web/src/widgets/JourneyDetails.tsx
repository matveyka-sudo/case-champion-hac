import type { Schemas } from "../api/types";
import { legText, modeText, reasonText, statusText, timestampText } from "../api/adapters/result";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";

export function JourneyDetails({ journey, features, originTimezone, destinationTimezone, scenario = false }: {
  journey: Schemas["JourneyResult"];
  features: Schemas["MapFeature"][];
  originTimezone?: string;
  destinationTimezone?: string;
  scenario?: boolean;
}) {
  const destination = features.find((f) =>
    f.entity_id === (journey.destination_object_id || journey.destination_node_id),
  )?.name || "Объект назначения";
  const segmentGeometry = journeyLines(journey).some((part) => part.segment);
  return (
    <details className="api-journey">
      <summary>
        {scenario ? "Путь сценария" : "Путь"} · {statusText[journey.status]} ·{" "}
        {!journey.geometry || journey.geometry_kind === "none"
          ? segmentGeometry ? "геометрия участков" : "геометрия отсутствует"
          : journey.geometry_kind === "schematic" ? "схема" : "геометрия источника"}
      </summary>
      {segmentGeometry && <p className="fhint">{segmentGeometryNotice}.</p>}
      <p>
        {destination} ·{" "}
        {journey.total_seconds === null
          ? journey.time_kind === "movement" ? "Время движения неизвестно" : "Полное время неизвестно"
          : `${journey.time_kind === "movement" ? "Движение: " : ""}${(journey.total_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин`}
      </p>
      <p>
        Отправление: {timestampText(journey.departure_at, originTimezone)}
        <br />
        Прибытие: {timestampText(journey.arrival_at, destinationTimezone)}
      </p>
      <ol>
        {journey.legs.map((leg, i) => (
          <li key={i}>
            {legText[leg.kind]} · {modeText[leg.mode]} ·{" "}
            {(leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин
          </li>
        ))}
      </ol>
      {reasonText(journey.reason_code) && <small>{reasonText(journey.reason_code)}</small>}
    </details>
  );
}
