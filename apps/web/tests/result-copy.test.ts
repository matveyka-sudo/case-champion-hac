import { describe, expect, it } from "vitest";
import { measureLabel, noticeText, reasonText } from "../src/api/adapters/result";

describe("partial walk result explanations", () => {
  it("labels actual backend metrics without changing unknown names", () => {
    expect(measureLabel("urban_h_seconds")).toBe("Опорное время города");
    expect(measureLabel("best_observed_journey_seconds")).toBe("Время найденного пути");
    expect(measureLabel("urban_accessibility_index")).toBe("Индекс доступности");
    expect(measureLabel("Время ожидания")).toBe("Время ожидания");
  });

  it("explains each known pilot limitation instead of repeating a generic notice", () => {
    const codes = [
      "ENDPOINT_SERIALIZATION_PRECISION_6", "GEOMETRY_UNAVAILABLE", "INCOMPLETE_COVERAGE",
      "INCOMPLETE_POPULATION_COVERAGE", "MINIMUM_TIME_UNPROVEN", "NETWORK_ONLY_GEOMETRY",
      "PARTIAL_SCHOOL_CATALOG", "PILOT_SAMPLE_ONLY", "TAU_UNAVAILABLE", "WALKING_SPEED_MODEL_5_KMH", "CANONICAL_ACCESS_MODEL",
    ];
    const explanations = codes.map(noticeText);
    expect(new Set(explanations).size).toBe(codes.length);
    for (const [index, text] of explanations.entries()) {
      expect(text).toBe(reasonText(codes[index]));
      expect(text).not.toContain("Для результата указано дополнительное ограничение");
      expect(text).not.toContain(codes[index]);
    }
    expect(reasonText("MINIMUM_TIME_UNPROVEN")).toContain("Минимальное время не подтверждено");
    expect(reasonText("WALKING_SPEED_MODEL_5_KMH")).toContain("модельная скорость 5 км/ч");
    expect(reasonText("INCOMPLETE_POPULATION_COVERAGE")).toContain("рейтинг города недоступен");
  });

  it("states the point-level access model without claiming complete city coverage", () => {
    const text = noticeText("CANONICAL_ACCESS_MODEL");
    expect(text).toContain("для выбранной точки");
    expect(text).toContain("по модели принятых пеших подходов");
    expect(text).toContain("Полный рейтинг города не рассчитан");
    expect(noticeText("canonical_access_model")).toBe(text);
  });

  it("keeps model, territory and uncomputed-data reasons distinct", () => {
    const labels = {
      PARTIAL_APPROACH_MODEL_COVERAGE: "Модель охватывает только часть пеших подходов.",
      POINT_ONLY_SCOPE: "Рассчитана только выбранная точка.",
      TERRITORY_INDEX_UNAVAILABLE: "Индекс территории не рассчитан.",
      CATALOG_DATE_OR_ROSTER_UNPROVEN: "Состав объектов или его актуальность на выбранную дату не подтверждены.",
      UNCOMPUTED_TERRITORY_POINTS: "Не все точки территории рассчитаны.",
      UNCOMPUTED_CATEGORY: "Эта категория объектов ещё не рассчитана.",
    };
    for (const [code, label] of Object.entries(labels)) {
      expect(reasonText(code)).toBe(label);
      expect(noticeText(code)).toBe(label);
    }
    expect(new Set(Object.values(labels)).size).toBe(Object.keys(labels).length);
  });

  it("does not turn an unfamiliar reason into a confirmed explanation", () => {
    expect(reasonText("FUTURE_UNREVIEWED_REASON")).toBe("");
    expect(noticeText("FUTURE_UNREVIEWED_REASON")).toBe("Для результата указано дополнительное ограничение.");
  });
});
