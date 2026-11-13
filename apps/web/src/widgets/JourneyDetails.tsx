import type { Schemas } from "../api/types";
import { legText, modeText, reasonText, statusText, timestampText } from "../api/adapters/result";
import { journeyLines, segmentGeometryNotice, transitLegStyle } from "../api/adapters/journeyGeometry";

export function JourneyDetails({ journey, features, originTimezone, destinationTimezone, scenario = false, comparison }: {
  journey: Schemas["JourneyResult"];
  features: Schemas["MapFeature"][];
  originTimezone?: string;
  destinationTimezone?: string;
  scenario?: boolean;
  comparison?: { walk: number; transit: number; transfers: number; sameDestination: boolean };
}) {
  const destination = features.find((f) =>
    f.entity_id === (journey.destination_object_id || journey.destination_node_id),
  )?.name || "Объект назначения";
  const segmentGeometry = journeyLines(journey).some((part) => part.segment);
  const isCaoTransit = journey.journey_id.endsWith("-transit");
  const minutes = (seconds: number) => (seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 });
  const walkingOnly = journey.legs.every((leg) => leg.kind === "walk");
  return (
    <details className="api-journey">
      <summary>
        {isCaoTransit && journey.total_seconds !== null
          ? `${destination} · ${minutes(journey.total_seconds)} мин ${walkingOnly ? "пешком" : "на ОТ"}`
          : scenario ? "Путь сценария" : "Путь"} · {statusText[journey.status]} ·{" "}
        {!journey.geometry || journey.geometry_kind === "none"
          ? segmentGeometry ? "геометрия участков" : "геометрия отсутствует"
          : journey.geometry_kind === "schematic" ? "схема" : "геометрия источника"}
      </summary>
      {isCaoTransit && comparison && <p>
        {walkingOnly
          ? <>Вариант на ОТ{comparison.sameDestination ? " до того же объекта" : " до другого объекта этой категории"} · {minutes(comparison.transit)} мин</>
          : <>Пешком до того же объекта · {minutes(comparison.walk)} мин</>}
        <br />Пересадок в варианте на ОТ · {comparison.transfers}
      </p>}
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
            {legText[leg.kind]} · {transitLegStyle(leg.service_id)?.label || modeText[leg.mode]}{leg.kind === "ride" && leg.service_id?.startsWith("cao:") ? ` ${leg.service_id.split(":")[2]}` : ""} ·{" "}
            {(leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин
          </li>
        ))}
      </ol>
      {reasonText(journey.reason_code) && <small>{reasonText(journey.reason_code)}</small>}
    </details>
  );
}
