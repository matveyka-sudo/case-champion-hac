import type { Schemas } from "../types";
import { categoryLabels, type ApiDraft } from "./request";
import { modeText, trafficText } from "./result";
import { scopeContainsTime, type UrbanSelection } from "./urbanScopes";

const names = (values: readonly string[], labels: Record<string, string>) =>
  [...new Set(values)].map((value) => labels[value] || value).join(", ");

export function atlasFilterHelp({
  caps, selection, territory, date, kind, issue, question,
}: {
  caps: Schemas["Capabilities"] | null;
  selection: UrbanSelection;
  territory: string;
  date: string;
  kind: ApiDraft["kind"];
  issue: string | null;
  question: string;
}) {
  if (!caps) return "Список доступных расчётов пока не получен. Откройте фильтры и повторите загрузку.";
  const context = [territory, date, selection.mode ? modeText[selection.mode] : null, trafficText[selection.traffic]].filter(Boolean).join(" · ");
  const scope = caps.urban_passenger_scopes;
  const base = kind === "urban_passenger" && scope != null && selection.cityId
    ? scope.filter((item) => item.city_id === selection.cityId)
    : null;
  const atTime = base?.filter((item) => scopeContainsTime(item, selection.departureAt)) || [];
  const byMode = atTime.filter((item) => !selection.mode || item.passenger_modes.includes(selection.mode));
  const categories = names(byMode.flatMap((item) => item.categories), categoryLabels);
  const modes = names(atTime.flatMap((item) => item.passenger_modes), modeText);
  const detail = [
    issue,
    modes && `На выбранное время доступны: ${modes}.`,
    categories && `Категории для выбранного способа передвижения: ${categories}.`,
    base?.length === 0 && "Сервер не зарегистрировал городской пассажирский профиль для этой территории.",
    base?.length && !atTime.length && "Откройте «Источники и условия данных» и выберите время в опубликованном интервале.",
  ].filter(Boolean).join(" ");
  const scenario = /сценари|изменен|улучш|пересад/i.test(question)
    ? " После сохранения результата сервер покажет доступные сценарии. Если предложение есть, его можно проверить отдельной кнопкой."
    : "";
  return `${context ? context + ". " : ""}${detail || "Когда фильтры готовы, запустите расчёт. Сервер проверит доступность данных для этой комбинации."}${scenario}`;
}
