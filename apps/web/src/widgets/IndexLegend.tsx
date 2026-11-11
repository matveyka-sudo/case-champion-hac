export function IndexLegend() {
  return (
    <div className="api-index-legend" role="group" aria-label="Шкала индекса от 0 до 100">
      <ul aria-label="Цветовые диапазоны индекса">
        <li aria-label="Красный: индекс меньше 50">
          <span className="api-index-swatch" data-band="low" aria-hidden="true" />
          {"<50"}
        </li>
        <li aria-label="Янтарный: индекс от 50 до менее 75">
          <span className="api-index-swatch" data-band="medium" aria-hidden="true" />
          {"50–<75"}
        </li>
        <li aria-label="Зелёный: индекс от 75 до 100">
          <span className="api-index-swatch" data-band="high" aria-hidden="true" />
          75–100
        </li>
        <li aria-label="Серый: индекс отсутствует">
          <span className="api-index-swatch" data-band="unknown" aria-hidden="true" />
          Нет индекса
        </li>
      </ul>
      <p><span className="api-partial-symbol" aria-hidden="true" />Точечный контур — частичная оценка</p>
    </div>
  );
}
