import type { CSSProperties } from "react";
import { indexColor } from "../api/adapters/mapIndexColor";
import { categoryLabels } from "../api/adapters/request";
import { modeText } from "../api/adapters/result";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { Icon } from "../shared/ui";

const number = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
function duration(seconds: number) {
  if (seconds < 60) return `${number.format(seconds)} с`;
  if (seconds < 3600) return `${number.format(seconds / 60)} мин`;
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

export function CompactResultSummary({ result, selection, score, time, metrics }: {
  result: AnalysisResult;
  selection: Selection | null;
  score?: Schemas["Score"] | null;
  time?: Schemas["Metric"] | null;
  metrics: Schemas["Metric"][];
}) {
  const request = result.request;
  if (request.analysis_kind === "urban_passenger") {
    if (selection?.entity_kind === "destination") return (
      <p className="api-destination-hint">Выберите стартовую точку на карте, чтобы увидеть индекс и время до ближайших объектов.</p>
    );
    const index = score?.status === "available" ? score.value : null;
    const travelTime = time?.status === "available" ? time.value : null;
    return <section className="api-result-summary" style={{ "--result-color": indexColor(index) } as CSSProperties}>
      <div className="api-summary-label">{selection ? "Доступность из этой точки" : "Доступность в зоне расчёта"}</div>
      <div className="api-summary-value tnum">
        {index === null ? <span>Нет данных для индекса</span> : <><strong>{number.format(index)}</strong><span>из 100</span></>}
      </div>
      <p className="api-summary-mode"><Icon name={request.passenger_mode === "walk" ? "footprints" : request.passenger_mode === "private_car" ? "car-front" : "tram-front"} />{modeText[request.passenger_mode]}</p>
      <p className="api-summary-categories">{request.categories.map((category) => categoryLabels[category]).join(" · ")}</p>
      {selection && request.categories.length === 1 && <div className="api-summary-fact">
        <span>{travelTime === null ? "Время в пути" : time?.name === "minimum_journey_seconds" ? "До ближайшего из выбранных объектов" : "Время по найденному маршруту"}</span>
        <strong>{travelTime === null ? "Нет данных" : duration(travelTime)}</strong>
      </div>}
    </section>;
  }
  if (request.analysis_kind === "intercity_passenger") {
    const road = metrics.find((metric) => metric.name === "private_car_path_seconds" && metric.status === "available");
    return <section className="api-result-summary">
      <div className="api-summary-label">Время в пути на автомобиле</div>
      <div className="api-summary-value tnum">{road?.value == null ? <span>Нет данных</span> : <strong>{duration(road.value)}</strong>}</div>
      {result.limitations.includes("ROAD_CHAIN_VIA_MOSCOW") && <p className="api-summary-mode"><Icon name="route" />Маршрут через Москву</p>}
      <p className="api-summary-note">Для индекса нужно эталонное время в пути.</p>
    </section>;
  }
  if (request.analysis_kind === "urban_cargo") {
    const movement = metrics.flatMap((metric) => metric.name === "observed_truck_network_seconds" && metric.status === "available" && metric.value !== null ? [metric.value] : []);
    const minimum = movement.length ? Math.min(...movement) : null;
    const maximum = movement.length ? Math.max(...movement) : null;
    return <section className="api-result-summary">
      <div className="api-summary-label">Время движения грузовика</div>
      <div className="api-summary-value tnum">
        {minimum === null || maximum === null ? <span>Нет данных</span> : <strong>{duration(minimum)}{maximum > minimum && ` – ${duration(maximum)}`}</strong>}
      </div>
      <p className="api-summary-mode"><Icon name="truck" />{request.direction === "outbound" ? "От площадок к грузовым узлам" : "От грузовых узлов к площадкам"}</p>
      <p className="api-summary-note">Общее время доставки пока неизвестно.</p>
    </section>;
  }
  return null;
}
