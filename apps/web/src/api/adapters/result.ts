import type { Schemas, Selection } from "../types";
export const statusText: Record<string, string> = {
  available: "Данные доступны",
  partial: "Частичные данные",
  unknown: "Нет данных",
  not_applicable: "Неприменимо",
  pending: "Вычисляется",
  methodology_pending: "Расчёт индекса недоступен",
  queued: "В очереди",
  running: "Расчёт выполняется",
};
export const modeText: Record<string, string> = {
  walk: "Пешком",
  public_transport: "Общественный транспорт",
  private_car: "Личный автомобиль",
  road: "Автомобильный транспорт",
  rail: "Железнодорожный транспорт",
  air: "Авиация",
  water: "Водный транспорт",
  mixed: "Смешанная цепочка",
  pipeline: "Трубопровод",
};
export const legText: Record<string, string> = {
  walk: "Пеший участок",
  ride: "Движение",
  wait_initial: "Первое ожидание",
  wait_transfer: "Ожидание пересадки",
  handling: "Обработка груза",
  transfer_operation: "Перегрузка / переход",
};
export const trafficText: Record<string, string> = {
  static: "Без пробок",
  typical: "Типичные условия",
  live: "Текущие условия",
  historical: "Исторические условия",
};
export const selectionText: Record<Selection["entity_kind"], string> = {
  cell: "Ячейка",
  sample_point: "Точка анализа",
  destination: "Объект назначения",
  freight_site: "Грузовая площадка",
  freight_node: "Грузовой узел",
  access_node: "Узел доступа",
  territory: "Территория",
};
export function reasonText(code: string | null | undefined) {
  if (!code) return "";
  return (
    (
      {
        NO_FEASIBLE_ROUTE: "Маршрут не найден в подтверждённой области поиска",
        PROVIDER_UNAVAILABLE: "Источник временно недоступен",
        ENGINE_UNAVAILABLE: "Расчётный модуль временно недоступен",
        MAP_DATA_UNAVAILABLE: "Геометрия недоступна",
        PENDING: "Дополнительный показатель вычисляется",
        TURNAROUND_UNVERIFIED:
          "Оборот транспорта и ресурсы для изменения рейса не подтверждены.",
        EDITABLE_GRAPH_UNAVAILABLE:
          "Нет доступного расписания для проверки изменения.",
        SYNTHETIC_ONLY: "Синтетический пример — не реальные результаты.",
        DATA_OR_ENGINE_UNAVAILABLE:
          "Нет готовых данных или доступного расчётного модуля.",
        NATIONAL_TERRITORY_CATALOG_NOT_ACCEPTED:
          "Полнота общероссийского справочника не подтверждена.",
        DATE_OUTSIDE_ACCEPTED_COVERAGE:
          "Дата находится за пределами подтверждённого покрытия.",
        PROVIDER_COVERAGE_NOT_COMPLETE:
          "Данные источника покрывают сообщение не полностью.",
      } as Record<string, string>
    )[code.toUpperCase()] || ""
  );
}
export function profileLabel(
  profile: Schemas["DeliveryCargoProfile"] | Schemas["PipelineCargoProfile"],
) {
  if (profile.kind === "pipeline")
    return { oil: "Нефть", oil_products: "Нефтепродукты", gas: "Газ" }[
      profile.product
    ];
  const handling: Record<string, string> = {
    container: "Контейнер",
    general: "Генеральный груз",
    bulk: "Навалочный груз",
    liquid: "Наливной груз",
  };
  const temperature = profile.temperature_requirements;
  const parts = [
    profile.food_class === "food" ? "Продовольственный" : "Непродовольственный",
    { urgent: "Срочный", ordinary: "Обычный", nonurgent: "Несрочный" }[
      profile.urgency
    ],
    ...profile.handling_requirements.map((v) => handling[v] || v),
  ];
  if (temperature.controlled)
    parts.push(
      temperature.min_c !== null && temperature.max_c !== null
        ? `${temperature.min_c}…${temperature.max_c} °C`
        : "Температурный режим",
    );
  const vehicle = profile.vehicle_constraints;
  if (vehicle.max_weight_tonnes !== null)
    parts.push(`до ${vehicle.max_weight_tonnes} т`);
  if (vehicle.max_height_m !== null)
    parts.push(`высота до ${vehicle.max_height_m} м`);
  return parts.join(" · ");
}
export function measureText(m: Schemas["Metric"] | Schemas["Score"]) {
  if (m.value === null || m.status !== "available")
    return (
      (
        {
          unknown: "Нет данных",
          not_applicable: "Неприменимо",
          methodology_pending: "Расчёт индекса недоступен",
          pending: "Вычисляется",
        } as Record<string, string>
      )[m.status] || "—"
    );
  const unit = "unit" in m ? m.unit : "points_0_100";
  const value = unit === "seconds" ? m.value / 60 : m.value;
  return `${new Intl.NumberFormat("ru", { maximumFractionDigits: 1 }).format(value)} ${unit === "seconds" || unit === "minutes" ? "мин" : unit === "points_0_100" ? "из 100" : unit === "hours" ? "ч" : unit}`;
}
export function subjectMatches(
  subject: Schemas["SubjectRef"],
  selection: Selection,
) {
  return [
    subject.point_id,
    subject.site_id,
    subject.node_id,
    subject.territory_id,
    subject.destination_id,
    subject.origin_id,
  ].includes(selection.entity_id);
}
export function selectFeature(
  features: Schemas["MapFeature"][],
  selection: Selection | null,
) {
  if (!selection) return null;
  return (
    features.find(
      (f) =>
        f.entity_kind === selection.entity_kind &&
        f.entity_id === selection.entity_id,
    ) || null
  );
}
export function safeSource(url: string | null | undefined) {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return ["https:", "http:"].includes(u.protocol) ? url : undefined;
  } catch {
    return undefined;
  }
}

export function timestampText(value: string | null, zone?: string) {
  if (!value) return "Время неизвестно";
  if (!zone) return value + " · часовой пояс не указан";
  try {
    return (
      new Intl.DateTimeFormat("ru", {
        timeZone: zone,
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(new Date(value)) +
      " · " +
      zone
    );
  } catch {
    return value + " · часовой пояс не подтверждён";
  }
}
export function deltaText(d: Schemas["ScenarioDelta"]) {
  const outcome = {
    improved: "Улучшение",
    worsened: "Ухудшение",
    unchanged: "Без изменений",
    changed: "Изменение",
    unknown: "Эффект неизвестен",
    new_connection: "Связь появилась",
    lost_connection: "Связь потеряна",
  }[d.outcome];
  const amount =
    d.delta === null
      ? "Без численной разности"
      : `${(d.unit === "seconds" ? d.delta / 60 : d.delta).toLocaleString("ru", { maximumFractionDigits: 1 })} ${d.unit === "seconds" ? "мин" : d.unit === "points_0_100" ? "балл." : d.unit}`;
  return `${outcome} · ${amount} · Δ: ${d.sign_convention === "before_minus_after" ? "база − сценарий" : "сценарий − база"}`;
}

export function noticeText(value: string) {
  return (
    reasonText(value) ||
    (/^[A-Z0-9]+(?:_[A-Z0-9]+)+$/i.test(value)
      ? "Для результата указано дополнительное ограничение."
      : value)
  );
}
