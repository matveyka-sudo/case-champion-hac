# Городские пассажирские индексы

Первый блок ML1: чистые расчёты по `docs/FORMULAS.md`, разделам 2–3,
и `config/product.json`. Времена до объектов и подготовленное население
поступают от вызывающего слоя. Маршрутизации, сетки, расчёта τ, API и LLM здесь нет.

## Публичные вызовы

Импорт из `a2_engine`. Все вычислительные функции работают без I/O,
не меняют входы и не округляют значения для интерфейса.

```python
load_urban_config(path: str | Path | None = None) -> UrbanConfig
urban_config_from_mapping(product: Mapping[str, object]) -> UrbanConfig
select_h_seconds(area_km2: float | None, *, bands: Sequence[AreaHBand]) -> MetricResult
category_index(travel_seconds: float | None, h_seconds: float | None,
               *, confirmed_no_route: bool = False) -> MetricResult
is_low_accessibility(index: MetricResult, *, threshold: float) -> bool | None
point_index(category_indices: Mapping[str, MetricResult],
            selected_categories: Mapping[str, str], *, config: UrbanConfig) -> MetricResult
point_indices_by_mode(category_indices_by_mode: Mapping[str, Mapping[str, MetricResult]],
                      selected_categories: Mapping[str, str],
                      *, config: UrbanConfig) -> dict[str, MetricResult]
territory_index(point_indices: Mapping[str, MetricResult],
                populations: Mapping[str, float | None]) -> MetricResult
```

`UrbanConfig` содержит версию методики, `area_h_bands` с H **в секундах**,
порог, веса, ID категорий и режимов. Загрузка проверяет согласованность версий,
статус approved, границы H и параметры, копирует данные в неизменяемые структуры.
Ни один параметр методики не подменяется независимой runtime-константой.
В checkout путь по умолчанию вычисляется от файла модуля, а не cwd.
При установке engine отдельно передавайте абсолютный путь опубликованного config.

`MetricResult(value, status, reason_code=None)` — внутренний численный тип,
не новая публичная система DTO. У H единица seconds; у индексов points_0_100.
`is_low_accessibility` возвращает `None` для неизвестной/неприменимой метрики,
а значение ровно на пороге не относит к низкой доступности.

| Состояние | Результат |
| --- | --- |
| Время известно | Рассчитанное значение, available |
| Время неизвестно | null, unknown, DATA_UNAVAILABLE |
| Неизвестна площадь / H | null, unknown, AREA_UNAVAILABLE / H_UNAVAILABLE |
| Подтверждено отсутствие маршрута | 0, available, NO_FEASIBLE_ROUTE |
| Неизвестна выбранная категория | null, unknown, CATEGORY_UNAVAILABLE |
| Неизвестно население | null, unknown, POPULATION_UNAVAILABLE |
| Неизвестен индекс точки с положительным населением | null, unknown, POINT_INDEX_UNAVAILABLE |
| Нулевое население всей выборки, включая пустую | null, not_applicable, ZERO_WEIGHT |

Неприменимый компонент с положительным весом также не даёт полного агрегата.
Индекс несуществующей в наборе выбранной категории — ошибка входного набора:
отсутствующее значение надо передать явно как unknown. Невыбранные категории
из конфигурации можно передать, они не участвуют в среднем.
`selected_categories` — отображение уникальных ID в приоритеты; дубли в исходном
JSON-списке должен отклонять BACKEND до преобразования в отображение.

`point_index` и `territory_index` рассчитывают **один режим** за вызов.
`point_indices_by_mode` проверяет ID режимов и возвращает отдельные результаты,
не усредняя их. Для территории нужны исходные ячейки либо заранее подготовленные
доли населения с одинаковыми ID в обоих отображениях. Не передавайте районные
средние вместо исходных точек. Полноту охвата территории, отсутствие дублей
и происхождение населения проверяют ML2/BACKEND; ядро проверяет числа и ID.
Неизвестный индекс при достоверно нулевом населении игнорируется;
неизвестное население никогда не становится нулём или единицей.

Некорректные входы вызывают `ValueError`, отсутствие файла — `FileNotFoundError`,
невалидный JSON — `JSONDecodeError`. Булевы значения не принимаются как числа.
Длительности конечны и неотрицательны, площадь/H и веса приоритетов положительны,
население неотрицательно, индексы 0..100. Ошибки не маскируются статусом unknown.
Пустой выбор категорий и несовпадающие наборы ID отклоняются.
Очень большие конечные веса масштабируются степенью двойки, чтобы избежать
переполнения; численный выход среднего за диапазон его компонентов ограничивается
этим диапазоном. Округление до знаков интерфейса не выполняется.

## Пример backend: только синтетические тестовые данные

```python
from a2_engine import (
    category_index,
    is_low_accessibility,
    load_urban_config,
    point_index,
    select_h_seconds,
    territory_index,
)

config = load_urban_config()
h = select_h_seconds(100, bands=config.area_h_bands).value  # тестовая площадь
selection = {"school": "low", "airport": "high"}

# Только тестовый признак: в реальном запуске полноту графа/окно доказывает caller.
no_route = category_index(None, h, confirmed_no_route=True)
p1 = point_index(
    {"school": category_index(0, h), "airport": no_route},
    selection, config=config,
)
p2 = point_index(
    {"school": category_index(0, h), "airport": category_index(0, h)},
    selection, config=config,
)
territory = territory_index({"p1": p1, "p2": p2}, {"p1": 90, "p2": 10})
assert p1.value == 25
assert territory.value == 32.5
assert is_low_accessibility(p1, threshold=config.low_accessibility_below) is True
```

Backend сохраняет версию `config.methodology_version`, config hash, режим,
snapshot и evidence в каноническом AnalysisResult/Score. Признак
`confirmed_no_route=True` разрешён только после доказательства полноты
графа/каталога и окна поиска; пустой список и ошибка провайдера этого не доказывают.
При неизвестном H обычный индекс unknown; подтверждённый no-route даёт ноль
независимо от H. Невалидный известный H всё равно отклоняется.
Неизвестный τ не участвует в этих вызовах и не блокирует индекс.

## Воспроизводимые проверки

Из **корня этого checkout**, Python 3.12, закреплённый uv 0.12.17:

```bash
uv lock --check
uv sync --frozen --all-packages --group dev
uv pip check
PYTHONPATH=packages/engine/src uv run --frozen python -c 'import a2_engine; print(a2_engine.__file__)'
PYTHONPATH=packages/engine/src uv run --frozen pytest packages/engine/tests -q
uv run --frozen ruff check packages/engine/src packages/engine/tests
uv run --frozen ruff format --check packages/engine/src packages/engine/tests
PYTHONPATH=packages/engine/src uv run --frozen mypy packages/engine/src packages/engine/tests
git diff --check
```

Существующий `package=false` не устанавливает engine как импортируемый пакет.
Поэтому `PYTHONPATH` необходим до правки упаковки DEVOPS. Отдельный тест запускает
настоящий Python из другого cwd с абсолютным PYTHONPATH, импортирует `a2_engine`
и загружает конфигурацию по умолчанию. Тестовые конфигурации и данные находятся
только в tests; реальные данные/рейтинги не создаются.

## Передача коллегам

- **DEVOPS:** `packages/engine/pyproject.toml` и при необходимости `uv.lock`:
  настроить устанавливаемый src-layout пакет a2_engine и выбранный build backend.
  Приёмка: после frozen sync импорт `a2_engine` работает без PYTHONPATH из любого
  cwd; engine pytest/Ruff/mypy добавлены в CI. Манифесты в этом блоке не менялись.
- **BACKEND:** `packages/contracts/` и `apps/api/`: реализовать канонические DTO
  schema_version=2 по specs, адаптировать `MetricResult` с сохранением value/status/
  reason_code, единиц и версии; отклонять дубли categories в исходном запросе.
  Приёмка: unknown остаётся null, ZERO_WEIGHT — not_applicable, NO_FEASIBLE_ROUTE —
  available 0, пропуски выбранных компонентов не дают полного Score.
  Адаптер должен передавать один mode и одну конфигурацию/версию на агрегат.
- **ML2:** для следующего этапа нужны принятые `population_cells`/`sample_points`
  из AcceptedData с ID исходной ячейки/доли, населением либо явным unknown,
  годом/методом/покрытием и административной площадью с версией границы.
  Приёмка: ноль отличается от unknown, доли не удваивают население, потери выборки
  не скрываются. Изменений ML2 для работы чистых функций сейчас не требуется.
