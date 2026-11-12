import type { CSSProperties } from "react";
import { indexColor } from "../api/adapters/mapIndexColor";
import { categoryLabels } from "../api/adapters/request";
import { modeText, timestampText } from "../api/adapters/result";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { Icon } from "../shared/ui";

const freightTypeNames: Record<string, string> = {
  port: "Порты",
  rail_freight_station: "Грузовые станции",
  terminal: "Терминалы",
  logistics_center: "Логистические центры",
};

const number = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
function duration(seconds: number, showDays = false) {
  if (seconds < 60) return `${number.format(seconds)} с`;
  if (seconds < 3600) return `${number.format(seconds / 60)} мин`;
  const minutes = Math.round(seconds / 60);
  if (showDays && minutes >= 1440) return `${Math.floor(minutes / 1440)} д ${Math.floor(minutes % 1440 / 60)} ч ${minutes % 60} мин`;
  return `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

const stationNames: Record<string, string> = {
  s2000006: "Белорусский вокзал",
  s2006004: "Ленинградский вокзал",
  s9612913: "Ростов-Главный",
  s9613017: "Батайск",
  s9613045: "Вокзал Азова",
  "don-rostov-pier-18": "Ростов-на-Дону, причал № 18",
  "don-azov-pier-14": "Азов, причал № 14",
  s9602494: "Московский вокзал (Санкт-Петербург)",
  s9637148: "Автовокзал на Обводном канале",
  s9842360: "Кингисепп",
  s9814853: "Мясная лавка (Кингисепп)",
  s9817866: "Ленрыба",
  "ferry-ust-luga": "Паромный терминал Усть-Луга",
  "ferry-baltiysk": "Паромный терминал Балтийск",
  s9838363: "Вокзал Балтийска",
  s9623375: "Автовокзал Калининграда",
  s2000003: "Казанский вокзал",
  s9600213: "Шереметьево",
  s9631119: "Храброво",
  s9623123: "Пашковский",
  s9613602: "Краснодар I",
  s9613022: "Новороссийск",
  s9876347: "Саларьево",
  "bus-kgd": "Автовокзал Калининграда",
  "bus-warsaw-west": "Варшава-Западная",
  "bus-minsk-central": "Минск-Центральный",
  "node-wd-q60220837": "Северные ворота",
  s9623281: "Краснодар-1",
  "node-wd-q48940963": "Красногвардейский",
  "node-osm-way-61808901": "Автовокзал Новороссийска",
};

export function CompactResultSummary({ result, selection, score, time, metrics, scores, cargoExample, originTimezone }: {
  result: AnalysisResult;
  selection: Selection | null;
  score?: Schemas["Score"] | null;
  time?: Schemas["Metric"] | null;
  metrics: Schemas["Metric"][];
  scores: Schemas["Score"][];
  cargoExample?: string | null;
  originTimezone?: string;
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
    const aviationAccess = request.passenger_modes?.length === 1 &&
      request.passenger_modes[0] === "air" && result.limitations.includes("AVIATION_WITH_GROUND_ACCESS");
    const carAccess = result.limitations.includes("STATIC_ROAD_ACCESS_TO_TERMINALS");
    const regionalAirConnection = result.limitations.includes("REGIONAL_AIR_VIA_SVO");
    const ferryAccess = request.passenger_modes?.length === 1 &&
      request.passenger_modes[0] === "water" && result.limitations.includes("WATER_WITH_GROUND_ACCESS");
    const donAccess = ferryAccess && result.limitations.includes("DON_PASSENGER_VESSEL");
    const modes = aviationAccess || ferryAccess ? ["mixed"] : request.passenger_modes ?? [...new Set(result.scores
      .filter((item) => item.name === "passenger_index" || item.name === "private_car_index")
      .map((item) => item.subject.mode).filter((mode) => mode != null))];
    return <section className="api-result-summary">
      <div className="api-summary-label">Индекс пассажирской связи</div>
      {modes.map((mode) => {
        const car = mode === "private_car";
        const transitMovement = car && result.limitations.includes("FTD_TRANSIT_ROAD_MOVEMENT_ONLY");
        const index = result.scores.find((item) => item.subject.mode === mode &&
          item.name === (car ? "private_car_index" : "passenger_index"));
        const findMetric = (name: string) => result.metrics.find((metric) =>
          metric.subject.mode === mode && metric.name === name && metric.status === "available");
        const path = findMetric(car ? "private_car_path_seconds" : "path_seconds");
        const travel = path?.value;
        const wait = car ? null : findMetric("initial_wait_seconds")?.value;
        const journey = result.journeys.find((item) => item.journey_id === path?.journey_id);
        const transfers = journey?.legs.filter((leg) => leg.kind === "wait_transfer") ?? [];
        const stationWalks = journey?.legs.filter((leg) => leg.kind === "walk") ?? [];
        const totalSeconds = journey?.total_seconds;
        const terminalTiming = (ferryAccess || carAccess) && totalSeconds != null && travel != null && wait != null;
        const movement = journey?.legs.filter((leg) => leg.kind === "ride" || leg.kind === "walk") ?? [];
        const movingSeconds = movement.reduce((total, leg) => total + leg.duration_seconds, 0);
        const roadSeconds = movement.filter((leg) => leg.mode === "private_car").reduce((total, leg) => total + leg.duration_seconds, 0);
        const routeNames = movement.map((leg) => stationNames[leg.from_node_id])
          .concat(journey ? [stationNames[journey.destination_node_id]] : []).filter(Boolean).join(" → ");
        const value = index?.status === "available" ? index.value : null;
        const reason = index?.reason_code;
        const missingReference = result.references.some((reference) => reference.status === "unknown");
        return <div key={mode}>
          <p className="api-summary-mode">{carAccess
            ? aviationAccess ? "Самолёт с автомобильным подъездом" : donAccess ? "Судно «Валдай» с автомобильным подъездом" : "Паром с автомобильным подъездом"
            : aviationAccess ? "Самолёт + поезд" : donAccess ? "Поезд и судно «Валдай»" : ferryAccess ? "Поезд, автобус и паром" : mode === "road" ? "Автобус" : modeText[mode]}</p>
          <div className="api-summary-value tnum">
            {value == null ? <span>Индекс не рассчитан</span> : <><strong>{number.format(value)}</strong><span>из 100</span></>}
          </div>
          {terminalTiming && <>
            <div className="api-summary-fact"><span>От запроса до прибытия</span><strong>{duration(totalSeconds, true)}</strong></div>
            <div className="api-summary-fact"><span>{carAccess ? "До выезда к терминалу" : "До первого отправления"}</span><strong>{duration(wait, true)}</strong></div>
            {carAccess ? <>
              <div className="api-summary-fact"><span>На машине, без пробок</span><strong>{duration(roadSeconds, true)}</strong></div>
              <div className="api-summary-fact"><span>{aviationAccess ? "В полёте" : donAccess ? "На судне" : "На пароме"}</span><strong>{duration(movingSeconds - roadSeconds, true)}</strong></div>
            </> : <div className="api-summary-fact"><span>В движении и пешком</span><strong>{duration(movingSeconds, true)}</strong></div>}
            <div className="api-summary-fact"><span>{carAccess ? "На посадку, пересадки и выход" : "На пересадки и ожидание"}</span><strong>{duration(Math.max(0, travel - movingSeconds), true)}</strong></div>
          </>}
          {!terminalTiming && travel != null && <div className="api-summary-fact">
            <span>{transitMovement ? "Время движения" : "Время в пути"}</span><strong>{duration(travel)}</strong>
          </div>}
          {!terminalTiming && wait != null && <div className="api-summary-fact">
            <span>До первого отправления</span><strong>{duration(wait)}</strong>
          </div>}
          {!carAccess && (aviationAccess || regionalAirConnection || mode === "road") && routeNames && <p className="api-summary-note">{routeNames}</p>}
          {mode === "road" && result.limitations.includes("INTERNATIONAL_BUS_VIA_POLAND") && <p className="api-summary-note">
            Маршрут через Польшу и Беларусь для пассажиров с правом въезда. Время прохождения границы может меняться.
          </p>}
          {ferryAccess && !donAccess && <p className="api-summary-note">Паром «Антей». Маршрут для пассажира без автомобиля, с заранее оформленным билетом.</p>}
          {journey && <details className="more">
            <summary>Детали поездки</summary>
            {(ferryAccess || carAccess) && routeNames && <p className="api-summary-note">{routeNames}</p>}
            <div className="api-summary-fact">
              <span>Время запроса</span><strong>{timestampText(request.departure_at, originTimezone, false)}</strong>
            </div>
            <div className="api-summary-fact">
              <span>{carAccess ? "Выезд к терминалу" : "Первое отправление"}</span><strong>{timestampText(journey.departure_at, originTimezone, false)}</strong>
            </div>
            {carAccess && movement.map((leg) => <div key={leg.start_at} className="api-summary-fact">
              <span>{stationNames[leg.from_node_id] || leg.from_node_id} → {stationNames[leg.to_node_id] || leg.to_node_id}</span>
              <strong>{duration(leg.duration_seconds, true)}</strong>
            </div>)}
            {ferryAccess && !carAccess && journey.legs.filter((leg) => leg.kind === "ride" && leg.mode === "water").map((leg) =>
              <div key={leg.start_at} className="api-summary-fact">
                <span>{donAccess ? "Время на судне" : "Время на пароме"}</span><strong>{duration(leg.duration_seconds)}</strong>
              </div>)}
            {mode === "road" && journey.legs.filter((leg) => leg.kind === "ride").length === 1 &&
              <p className="api-summary-note">Прямой рейс</p>}
            {!carAccess && transfers.length > 0 && <div className="api-summary-fact">
              <span>Ожидание на пересадках</span>
              <strong>{duration(transfers.reduce((total, leg) => total + leg.duration_seconds, 0))}</strong>
            </div>}
            {(aviationAccess || regionalAirConnection) && transfers.some((leg) => leg.from_node_id === "s9600213") &&
              <p className="api-summary-note">Пересадка в Шереметьево, терминал B. В расчёте заложено не менее 2 часов.</p>}
            {stationWalks.map((leg) => <div key={leg.start_at} className="api-summary-fact">
              <span>{leg.from_node_id && leg.to_node_id && stationNames[leg.from_node_id] && stationNames[leg.to_node_id]
                ? `${stationNames[leg.from_node_id]} → ${stationNames[leg.to_node_id]}`
                : "Пеший переход"}</span>
              <strong>{duration(leg.duration_seconds)}</strong>
            </div>)}
          </details>}
          {car && result.limitations.includes("ROAD_CHAIN_VIA_MOSCOW") &&
            <p className="api-summary-note">Маршрут через Москву.</p>}
          {transitMovement && <p className="api-summary-note">
            Транзит через Литву с УТД. Сценарий для легкового авто с российской регистрацией и собственником в машине.
            Индекс и время учитывают движение. Очереди и пограничный контроль не оценены.
          </p>}
          {reason === "NO_FEASIBLE_ROUTE" ? <p className="api-summary-note">В проверенной сети подходящего рейса нет.</p> :
            value == null && <p className="api-summary-note">{travel != null && missingReference
              ? "Для индекса нужно эталонное время в пути."
              : "Недостаточно подтверждённых данных для расчёта."}</p>}
        </div>;
      })}
      {result.evidence_refs.some((ref) => ref.source_id === "yandex-rasp") &&
        <p className="api-summary-note"><a href="https://rasp.yandex.ru/" target="_blank" rel="noreferrer">Данные предоставлены сервисом Яндекс.Расписания</a></p>}
      {modes.length === 0 && <p className="api-summary-note">Недостаточно данных для подтверждения поездки.</p>}
    </section>;
  }
  if (request.analysis_kind === "urban_cargo") {
    const groups = result.scores.filter((item) => item.name === "cargo_access_index");
    const measurements = groups.flatMap((group) => {
      const siteIds = new Set(group.components.filter((part) => part.kind === "score").map((part) => part.id));
      const siteScores = scores.filter((item) => siteIds.has(item.score_id));
      const displayed = selection ? siteScores : [group];
      if (!displayed.length) return [];
      const metricIds = new Set(siteScores.flatMap((item) =>
        item.components.filter((part) => part.kind === "metric").map((part) => part.id)));
      const times = metrics.flatMap((metric) =>
        metricIds.has(metric.metric_id) && metric.name === "minimum_truck_access_seconds" &&
        metric.status === "available" && metric.value !== null ? [metric.value] : []);
      const type = group.score_id.replace(/^cargo-territory-/, "");
      return [{ group, displayed, times, label: freightTypeNames[type] || "Грузовые узлы" }];
    });
    if (measurements.some(({ displayed }) => displayed.some((item) => item.status === "available" && item.value !== null))) return <section className="api-result-summary">
      <div className="api-summary-label">Индекс грузовой доступности</div>
      <p className="api-summary-mode"><Icon name="truck" />{request.direction === "outbound" ? "От площадок к грузовым узлам" : "От грузовых узлов к площадкам"}</p>
      {measurements.map(({ group, displayed, times, label }) => <div key={group.score_id}>
        {displayed.map((item) => <div key={item.score_id} className="api-summary-fact">
          <span>{label}</span>
          <strong>{item.status === "available" && item.value !== null ? `${number.format(item.value)} из 100` : "Нет данных"}</strong>
        </div>)}
        {times.length > 0 && <div className="api-summary-fact">
          <span>Время грузового доступа</span>
          <strong>{duration(Math.min(...times))}{Math.max(...times) > Math.min(...times) && ` – ${duration(Math.max(...times))}`}</strong>
        </div>}
      </div>)}
      <p className="api-summary-note">{cargoExample ? `Пример груза: ${cargoExample}` : "Расчёт по выбранным площадкам и узлам."}</p>
    </section>;
    const missingInputs = [
      result.limitations.includes("CARGO_COMPATIBILITY_UNKNOWN") && "совместимость груза с узлом",
      result.limitations.includes("TRUCK_ACCESS_UNKNOWN") && "подъезды к площадке и узлу",
    ].filter(Boolean);
    const movement = metrics.flatMap((metric) => metric.name === "observed_truck_network_seconds" && metric.status === "available" && metric.value !== null ? [metric.value] : []);
    const minimum = movement.length ? Math.min(...movement) : null;
    const maximum = movement.length ? Math.max(...movement) : null;
    return <section className="api-result-summary">
      <div className="api-summary-label">Индекс грузовой доступности</div>
      <div className="api-summary-value"><strong>Не рассчитан</strong></div>
      {minimum !== null && maximum !== null && <div className="api-summary-fact">
        <span>Время движения грузовика</span>
        <strong>{duration(minimum)}{maximum > minimum && ` – ${duration(maximum)}`}</strong>
      </div>}
      <p className="api-summary-mode"><Icon name="truck" />{request.direction === "outbound" ? "От площадок к грузовым узлам" : "От грузовых узлов к площадкам"}</p>
      <p className="api-summary-note">
        {missingInputs.length ? `Не хватает данных, чтобы подтвердить ${missingInputs.join(" и ")}.` : "Для выбранного груза и направления пока недостаточно данных."}
      </p>
    </section>;
  }
  if (request.analysis_kind === "intercity_cargo" && request.cargo_transport !== "pipeline") {
    const index = scores.find((item) => item.name === "delivery_index" && item.subject.mode === "mixed" && item.status === "available");
    const delivery = metrics.find((item) => item.name === "complete_delivery_seconds" && item.subject.mode === "mixed" && item.status === "available");
    return <section className="api-result-summary">
      <div className="api-summary-label">Индекс грузовой доставки</div>
      <div className="api-summary-value tnum">
        {index?.value == null ? <span>Нет данных для индекса</span> : <><strong>{number.format(index.value)}</strong><span>из 100</span></>}
      </div>
      <div className="api-summary-fact">
        <span>Полное время доставки</span><strong>{delivery?.value == null ? "Нет данных" : duration(delivery.value, true)}</strong>
      </div>
      {result.limitations.includes("MODELLED_PREBOOKED_TRUCK_DELIVERY") && <p className="api-summary-note">
        {cargoExample && <>{cargoExample}. </>}Машина и время обработки согласованы заранее в условиях модели.
      </p>}
    </section>;
  }
  return null;
}
