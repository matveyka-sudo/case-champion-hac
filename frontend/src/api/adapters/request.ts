import { Temporal } from "@js-temporal/polyfill";
import type {
  AnalysisKind,
  AnalysisRequest,
  Category,
  Schemas,
  Territory,
} from "../types";
import { decode } from "../validation";
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
  date: string;
  slot: number;
  traffic: Schemas["Capabilities"]["traffic_modes"][number];
  travel: "pt" | "walk" | "car";
  cats: Partial<Record<keyof typeof categoryIds, number>>;
  paxModes: string[];
  cargoModes: string[];
  profileId: string;
  pipeline: boolean;
  pipelineProduct: "oil" | "oilprod" | "gas";
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
    date: new Date().toISOString().slice(0, 10),
    slot: 34,
    traffic: "static",
    travel: "pt",
    cats: { c01: 3, c04: 2, c09: 1 },
    paxModes: ["air", "rail", "bus", "car", "sea"],
    cargoModes: ["road", "rail", "air", "water"],
    profileId: "",
    pipeline: false,
    pipelineProduct: "oilprod",
    direction: "out",
  };
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
  const base = {
    schema_version: "2" as const,
    departure_at: departureAt(d.date, d.slot, origin.timezone),
    traffic_mode: d.traffic,
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
    return decode("UrbanPassengerRequest", {
      ...base,
      ...city,
      analysis_kind: d.kind,
      passenger_mode: (
        { pt: "public_transport", walk: "walk", car: "private_car" } as const
      )[d.travel],
      categories: Object.keys(priorities),
      priorities,
    });
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
    const product = (
      { oil: "oil", oilprod: "oil_products", gas: "gas" } as const
    )[d.pipelineProduct];
    if (
      !caps.pipeline_products.includes(product) ||
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
