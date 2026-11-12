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
    const modes = request.passenger_modes ?? [...new Set(result.scores
      .filter((item) => item.name === "passenger_index" || item.name === "private_car_index")
      .map((item) => item.subject.mode).filter((mode) => mode != null))];
    return <section className="api-result-summary">
      <div className="api-summary-label">Индекс пассажирской связи</div>
      {modes.map((mode) => {
        const car = mode === "private_car";
        const index = result.scores.find((item) => item.subject.mode === mode &&
          item.name === (car ? "private_car_index" : "passenger_index"));
        const findTime = (name: string) => result.metrics.find((metric) =>
          metric.subject.mode === mode && metric.name === name && metric.status === "available")?.value;
        const travel = findTime(car ? "private_car_path_seconds" : "path_seconds");
        const wait = car ? null : findTime("initial_wait_seconds");
        const value = index?.status === "available" ? index.value : null;
        const reason = index?.reason_code;
        const missingReference = result.references.some((reference) => reference.status === "unknown");
        return <div key={mode}>
          <p className="api-summary-mode">{mode === "road" ? "Автобус" : modeText[mode]}</p>
          <div className="api-summary-value tnum">
            {value == null ? <span>Индекс не рассчитан</span> : <><strong>{number.format(value)}</strong><span>из 100</span></>}
          </div>
          {travel != null && <div className="api-summary-fact">
            <span>Время в пути</span><strong>{duration(travel)}</strong>
          </div>}
          {wait != null && <div className="api-summary-fact">
            <span>До первого отправления</span><strong>{duration(wait)}</strong>
          </div>}
          {car && result.limitations.includes("ROAD_CHAIN_VIA_MOSCOW") &&
            <p className="api-summary-note">Маршрут через Москву.</p>}
          {reason === "NO_FEASIBLE_ROUTE" ? <p className="api-summary-note">В проверенной сети подходящего рейса нет.</p> :
            value == null && <p className="api-summary-note">{travel != null && missingReference
              ? "Для индекса нужно эталонное время в пути."
              : "Недостаточно подтверждённых данных для расчёта."}</p>}
        </div>;
      })}
      {modes.length === 0 && <p className="api-summary-note">Недостаточно данных для подтверждения поездки.</p>}
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
