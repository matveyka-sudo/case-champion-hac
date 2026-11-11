import { Temporal } from "@js-temporal/polyfill";
import type {
  AnalysisKind,
  AnalysisRequest,
  Category,
  Schemas,
  Territory,
} from "../types";
import { decode } from "../validation";
import { urbanScopeAvailability } from "./urbanScopes";
export const passengerModes = { pt: "public_transport", walk: "walk", car: "private_car" } as const;
export const categoryIds = {
  c01: "adult_polyclinic",
  c02: "child_polyclinic",
  c03: "kindergarten",
  c04: "school",
  c05: "college_university",
  c06: "rail_terminal",
  c07: "bus_terminal",
  c08: "airport",
  c09: "shopping_center",
} as const;
export const categoryLabels: Record<Category, string> = {
  adult_polyclinic: "Взрослые поликлиники",
  child_polyclinic: "Детские поликлиники",
  kindergarten: "Детсады",
  school: "Школы",
  college_university: "Колледжи и вузы",
  rail_terminal: "Ж/д вокзалы",
  bus_terminal: "Автовокзалы",
  airport: "Аэропорты",
  shopping_center: "ТЦ",
};
export class DraftError extends Error {
  readonly field: string;
  constructor(field: string, message: string) {
    super(message);
    this.field = field;
    this.name = "DraftError";
  }
}
export interface ApiDraft {
  kind: AnalysisKind;
  level: "city" | "region";
  cityId: string;
  originId: string;
  destinationId: string;
  areaId: string;
  snapshotId: string;
  date: string;
  slot: number;
  traffic: Schemas["Capabilities"]["traffic_modes"][number];
  travel: "" | "pt" | "walk" | "car";
  cats: Partial<Record<keyof typeof categoryIds, number>>;
  paxModes: string[];
  cargoModes: string[];
  profileId: string;
  pipeline: boolean;
  pipelineProduct: "" | "oil" | "oilprod" | "gas";
  direction: "out" | "in";
}
export function initialDraft(): ApiDraft {
  return {
    kind: "urban_passenger",
    level: "city",
    cityId: "",
    originId: "",
    destinationId: "",
    areaId: "",
    snapshotId: "",
    date: new Date().toISOString().slice(0, 10),
    slot: 34,
    traffic: "static",
    travel: "pt",
    cats: { c01: 3, c04: 2, c09: 1 },
    paxModes: ["air", "rail", "bus", "car", "sea"],
    cargoModes: ["road", "rail", "air", "water"],
    profileId: "",
    pipeline: false,
    pipelineProduct: "",
    direction: "out",
  };
}
export const pipelineProducts = {
  oil: "oil",
  oilprod: "oil_products",
  gas: "gas",
} as const;
export function isUrbanCity(t: Territory, caps: Schemas["Capabilities"]) {
  return t.level === "city" && caps.urban_city_names.includes(t.name);
}
export function eligibleSnapshots(
  d: ApiDraft,
  territories: Territory[],
  caps: Schemas["Capabilities"],
): Schemas["SnapshotAvailability"][] {
  const urban = d.kind.startsWith("urban_");
  const ids = urban ? [d.cityId] : [d.originId, d.destinationId];
  const places = ids.map((id) =>
    territories.find(
      (t) => t.territory_id === id && t.level === (urban ? "city" : d.level),
    ),
  );
  if (
    places.some((t) => !t) ||
    (!urban && d.originId === d.destinationId) ||
    (urban && !isUrbanCity(places[0]!, caps))
  )
    return [];
  try {
    const instant = Temporal.Instant.from(
      departureAt(d.date, d.slot, places[0]!.timezone),
    );
    const days = places.map((t) =>
      instant.toZonedDateTimeISO(t!.timezone).toPlainDate().toString(),
    );
    return (caps.snapshots || []).filter(
      (s) =>
        (s.analysis_kinds == null || s.analysis_kinds.includes(d.kind)) &&
        s.valid_from !== null &&
        s.valid_to !== null &&
        ids.every((id) => s.territory_ids.includes(id)) &&
        days.every((day) => day >= s.valid_from! && day <= s.valid_to!),
    );
  } catch {
    return [];
  }
}
export function resolvedSnapshotId(
  d: ApiDraft,
  eligible: Schemas["SnapshotAvailability"][],
) {
  return d.snapshotId || (eligible.length === 1 ? eligible[0].snapshot_id : "");
}
export function departureAt(date: string, slot: number, zone: string) {
  if (!Number.isInteger(slot) || slot < 0 || slot > 95)
    throw new DraftError("slot", "Время должно иметь шаг 15 минут");
  try {
    const day = Temporal.PlainDate.from(date);
    const value = Temporal.ZonedDateTime.from(
      {
        timeZone: zone,
        year: day.year,
        month: day.month,
        day: day.day,
        hour: Math.floor(slot / 4),
        minute: (slot % 4) * 15,
      },
      { disambiguation: "reject" },
    );
    return value.toString({
      timeZoneName: "never",
      calendarName: "never",
      fractionalSecondDigits: 0,
    });
  } catch {
    throw new DraftError(
      "date",
      "Проверьте дату и время в часовом поясе территории: момент отсутствует или неоднозначен",
    );
  }
}
export function toRequest(
  d: ApiDraft,
  territories: Territory[],
  caps: Schemas["Capabilities"],
): AnalysisRequest {
  const urban = d.kind.startsWith("urban_");
  const origin = territories.find(
    (t) =>
      t.territory_id === (urban ? d.cityId : d.originId) &&
      t.level === (urban ? "city" : d.level),
  );
  if (!origin)
    throw new DraftError(
      urban ? "cityId" : "originId",
      "Выберите территорию из серверного каталога",
    );
  if (urban && !isUrbanCity(origin, caps))
    throw new DraftError("cityId", "Город не поддерживает городской анализ");
  if (
    !urban &&
    (!territories.some(
      (t) => t.territory_id === d.destinationId && t.level === d.level,
    ) ||
      d.destinationId === d.originId)
  )
    throw new DraftError(
      "destinationId",
      "Выберите другую территорию того же уровня",
    );
  if (!caps.traffic_modes.includes(d.traffic))
    throw new DraftError("traffic", "Режим трафика не поддержан сервером");
  if (
    urban &&
    d.areaId &&
    !caps.areas?.some((a) => a.city_id === d.cityId && a.area_id === d.areaId)
  )
    throw new DraftError("areaId", "Район не принадлежит выбранному городу");
  const departure = departureAt(d.date, d.slot, origin.timezone);
  let snapshotId: string | undefined = d.snapshotId || undefined;
  if (caps.snapshots !== undefined) {
    const eligible = eligibleSnapshots(d, territories, caps);
    snapshotId = resolvedSnapshotId(d, eligible);
    if (!eligible.length)
      throw new DraftError(
        "snapshotId",
        "Нет подтверждённого набора данных для выбранных территорий и даты",
      );
    if (!snapshotId)
      throw new DraftError("snapshotId", "Выберите один из доступных наборов данных");
    if (!eligible.some((s) => s.snapshot_id === snapshotId))
      throw new DraftError(
        "snapshotId",
        "Выбранный набор данных не покрывает территории и дату — выберите другой",
      );
  }
  const base = {
    schema_version: "2" as const,
    departure_at: departure,
    traffic_mode: d.traffic,
    ...(snapshotId ? { snapshot_id: snapshotId } : {}),
  };
  const city = {
    city_id: d.cityId,
    ...(d.areaId ? { area_id: d.areaId } : {}),
  };
  const pair = {
    territory_level: d.level,
    origin_id: d.originId,
    destination_id: d.destinationId,
  };
  if (d.kind === "urban_passenger") {
    if (!d.travel) throw new DraftError("travel", "Выберите способ передвижения для выбранного города");
    const entries = Object.entries(d.cats) as [
      keyof typeof categoryIds,
      number,
    ][];
    if (!entries.length)
      throw new DraftError("cats", "Выберите хотя бы одну категорию");
    const priorities: Partial<Record<Category, "low" | "medium" | "high">> = {};
    for (const [id, weight] of entries) {
      if (
        !categoryIds[id] ||
        !caps.categories.includes(categoryIds[id]) ||
        ![1, 2, 3].includes(weight)
      )
        throw new DraftError("cats", "Недопустимая категория или приоритет");
      priorities[categoryIds[id]] = (
        { 1: "low", 2: "medium", 3: "high" } as const
      )[weight as 1 | 2 | 3];
    }
    const request = decode("UrbanPassengerRequest", {
      ...base,
      ...city,
      analysis_kind: d.kind,
      passenger_mode: passengerModes[d.travel],
      categories: Object.keys(priorities),
      priorities,
    });
    const scope = urbanScopeAvailability({ cityId: request.city_id, snapshotId: snapshotId || "", departureAt: request.departure_at,
      areaId: request.area_id || "", mode: request.passenger_mode, traffic: request.traffic_mode, categories: request.categories }, caps.urban_passenger_scopes);
    if (scope.problem) throw new DraftError(scope.problem.field, scope.problem.message);
    return request;
  }
  if (d.kind === "intercity_passenger") {
    const modes: Record<string, string> = {
      air: "air",
      rail: "rail",
      bus: "road",
      car: "private_car",
      sea: "water",
      mixed: "mixed",
    };
    if (!d.paxModes.length || d.paxModes.some((m) => !modes[m]))
      throw new DraftError("paxModes", "Выберите вид транспорта");
    return decode("IntercityPassengerRequest", {
      ...base,
      ...pair,
      analysis_kind: d.kind,
      passenger_modes: d.paxModes.map((m) => modes[m]),
    });
  }
  const profile = caps.cargo_profiles.find((p) => p.profile_id === d.profileId);
  if (!profile)
    throw new DraftError(
      "profileId",
      "Выберите зарегистрированный грузовой профиль",
    );
  if (d.kind === "urban_cargo") {
    if (profile.kind !== "delivery")
      throw new DraftError(
        "profileId",
        "Трубопровод неприменим к городскому доступу",
      );
    return decode("UrbanCargoRequest", {
      ...base,
      ...city,
      analysis_kind: d.kind,
      cargo_profile_id: profile.profile_id,
      direction: d.direction === "out" ? "outbound" : "inbound",
    });
  }
  if (d.pipeline) {
    const product = d.pipelineProduct && pipelineProducts[d.pipelineProduct];
    if (!product || !caps.pipeline_products.includes(product))
      throw new DraftError("pipelineProduct", "Выберите доступный продукт трубопровода");
    if (
      profile.kind !== "pipeline" ||
      profile.product !== product
    )
      throw new DraftError(
        "profileId",
        "Выберите трубопроводный профиль нужного продукта",
      );
    return decode("IntercityCargoRequest", {
      ...base,
      ...pair,
      analysis_kind: d.kind,
      cargo_profile_id: profile.profile_id,
      cargo_transport: "pipeline",
      cargo_modes: ["pipeline"],
      pipeline_product: product,
    });
  }
  if (
    profile.kind !== "delivery" ||
    !d.cargoModes.length ||
    d.cargoModes.some(
      (m) => !["road", "rail", "air", "water", "mixed"].includes(m),
    )
  )
    throw new DraftError(
      "cargoModes",
      "Выберите виды транспорта и профиль доставки",
    );
  return decode("IntercityCargoRequest", {
    ...base,
    ...pair,
    analysis_kind: d.kind,
    cargo_profile_id: profile.profile_id,
    cargo_transport: "multimodal",
    cargo_modes: d.cargoModes,
  });
}


/** A published request is a frozen server choice, independent of editable filters. */
export function toPresetRequest(presetId: string, caps: Schemas["Capabilities"]): AnalysisRequest {
  const matches = (caps.analysis_presets || []).filter((preset) => preset.preset_id === presetId);
  if (matches.length !== 1) throw new DraftError("preset", "Набор условий больше недоступен. Обновите список и выберите другой.");
  return structuredClone(matches[0].request);
}

/** Display only: the original preset request remains the submitted payload. */
export function presetDraft(request: AnalysisRequest, catalog: Territory[]): ApiDraft {
  const urban = "city_id" in request;
  const origin = catalog.find((t) => t.territory_id === (urban ? request.city_id : request.origin_id));
  const time = Temporal.Instant.from(request.departure_at).toZonedDateTimeISO(origin?.timezone || "UTC");
  const draft: ApiDraft = { ...initialDraft(), kind: request.analysis_kind, level: urban ? "city" : request.territory_level,
    cityId: urban ? request.city_id : "", originId: urban ? "" : request.origin_id, destinationId: urban ? "" : request.destination_id,
    areaId: urban ? request.area_id || "" : "", snapshotId: request.snapshot_id || "", date: time.toPlainDate().toString(), slot: time.hour * 4 + Math.floor(time.minute / 15), traffic: request.traffic_mode,
    cats: {}, travel: "", paxModes: [], cargoModes: [], profileId: "", pipeline: false, pipelineProduct: "" };
  if (request.analysis_kind === "urban_passenger") {
    draft.travel = (Object.entries(passengerModes).find(([, mode]) => mode === request.passenger_mode)?.[0] || "") as ApiDraft["travel"];
    for (const [key, category] of Object.entries(categoryIds)) if (request.categories.includes(category)) draft.cats[key as keyof typeof categoryIds] = ({ low: 1, medium: 2, high: 3 } as const)[request.priorities[category] || "medium"];
  } else if (request.analysis_kind === "intercity_passenger") {
    const modes: Record<string, string> = { air: "air", rail: "rail", road: "bus", private_car: "car", water: "sea", mixed: "mixed" };
    draft.paxModes = (request.passenger_modes || []).map((mode) => modes[mode]);
  } else {
    draft.profileId = request.cargo_profile_id;
    if (request.analysis_kind === "urban_cargo") draft.direction = request.direction === "outbound" ? "out" : "in";
    else {
      draft.cargoModes = [...request.cargo_modes || []];
      draft.pipeline = request.cargo_transport === "pipeline";
      draft.pipelineProduct = (Object.entries(pipelineProducts).find(([, product]) => product === request.pipeline_product)?.[0] || "") as ApiDraft["pipelineProduct"];
    }
  }
  return draft;
}
