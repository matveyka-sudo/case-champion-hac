import type { UrbanReadiness as Readiness } from "../api/adapters/urbanReadiness";
import { measureText, scoreBand } from "../api/adapters/result";

export function UrbanReadiness({ value }: { value: Readiness }) {
  if (!value.city) return null;
  const { overview, populationBasis } = value;
  const point = overview?.points.length === 1 ? overview.points[0].score : null;
  const cityScore = overview?.territoryScore;
  return (
    <section className="api-readiness" aria-label="Готовность выбранных условий">
      <details className="more"><summary>Готовность · {value.city.name}</summary>
      <dl>
        <dt>Набор данных</dt>
        <dd>{value.snapshot ? "Подходящий набор опубликован. Полнота данных не подтверждается этим фактом."
          : value.snapshotChoice ? "Выберите один из подходящих наборов." : "Подходящий набор не подтверждён."}</dd>
        <dt>Запуск</dt>
        <dd>{!value.serviceAvailable ? "Расчёт сейчас недоступен."
          : !value.publishedScopes ? "Детализация запуска сервером не предоставлена."
          : value.matchingScopes === 1 ? "Параметры запуска зарегистрированы. Это не гарантирует наличие индекса."
          : value.matchingScopes > 1 ? "Параметрам соответствуют несколько наборов запуска."
          : "Для выбранных условий запуск не зарегистрирован."}</dd>
        <dt>Население</dt>
        <dd>{populationBasis ? `В основе индекса: ${populationBasis.population === null ? "численность неизвестна" : `${populationBasis.population.toLocaleString("ru")} чел.`}; охват ${(populationBasis.coverage * 100).toLocaleString("ru", { maximumFractionDigits: 1 })}%${populationBasis.unknown_positive_weight ? "; есть неизвестные веса" : ""}.`
          : "Полнота данных о населении не подтверждена."}</dd>
        <dt>Каталог объектов</dt>
        <dd>Полнота каталога не подтверждена.</dd>
        <dt>Индексы точек</dt>
        <dd>{!overview ? "Для этих условий результат ещё не получен."
          : !overview.points.length ? "Численные общие индексы точек не предоставлены."
          : <>Рассчитано точек: {overview.points.length}{overview.denominator === null ? "" : ` из ${overview.denominator}`}.
            {point && <> Индекс: <b data-score-band={scoreBand(point)}>{measureText(point)}</b>.</>}</>}</dd>
        <dt>{value.areaSelected ? "Индекс выбранной территории" : "Индекс города"}</dt>
        <dd>{!overview ? "Для этих условий результат ещё не получен."
          : !cityScore ? "Не предоставлен."
          : <><b data-score-band={scoreBand(cityScore)}>{measureText(cityScore)}</b>.</>}</dd>
      </dl>
      </details>
    </section>
  );
}
