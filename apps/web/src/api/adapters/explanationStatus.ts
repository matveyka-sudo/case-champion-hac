import type { Schemas } from "../types";

// Why the server answered with its template instead of a checked model answer.
const fallbackReasons: Record<string, string> = {
  DISABLED: "модель на этом стенде отключена",
  TIMEOUT: "модель не ответила вовремя",
  PROVIDER_UNAVAILABLE: "сервис модели недоступен",
  ASSISTANT_UNAVAILABLE: "ассистент временно недоступен",
  INVALID_EXPLANATION: "ответ модели не прошёл проверку",
  BUDGET_EXCEEDED: "лимит расходов на модель исчерпан",
  INPUT_LIMIT: "данные расчёта не поместились в запрос к модели",
  OUTPUT_LIMIT: "ответ модели превысил допустимый размер",
  MODEL_LIMIT: "модель исчерпала число попыток",
  TOOL_LIMIT: "модель исчерпала число обращений к данным",
  TOOL_REJECTED: "модель запросила недопустимое действие",
  TOOL_UNAVAILABLE: "данные для ответа модели недоступны",
  PRIVATE_SOURCE_EXTERNAL_MODEL_DISABLED: "расчёт использует закрытые источники, внешняя модель не вызывается",
  NO_METRICS: "в расчёте нет показателей для объяснения",
  NO_SELECTED_METRICS: "для выбранного объекта нет показателей",
};

export function explanationLabel(answer: Pick<Schemas["Explanation"], "status" | "reason_code">) {
  if (answer.status !== "template") return "Объяснение модели";
  const reason = fallbackReasons[answer.reason_code || ""];
  return reason ? `Шаблонное объяснение: ${reason}` : "Шаблонное объяснение";
}
