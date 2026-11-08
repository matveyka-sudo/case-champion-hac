# Математическое ядро A2

Чистые расчёты ML1 по `docs/FORMULAS.md` и `config/product.json`: городские
пассажирские индексы, одна межгородская пассажирская цепочка, городские/межгородские
грузы, загрузка трубопровода и сравнение уже рассчитанных результатов.
Времена, население и подтверждения входных условий поступают от вызывающего слоя.
Маршрутизации, сетки, расчёта τ, построения эталона A, изменения расписаний,
сценарного движка, HTTP и LLM здесь нет.

## Городские пассажирские вызовы: первый этап

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

<!-- executable-example -->
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

## Дополнительная конфигурация

Существующие `UrbanConfig` и `load_urban_config` сохранены. Новые вызовы:

```python
load_passenger_config(path: str | Path | None = None) -> PassengerConfig
passenger_config_from_mapping(product: Mapping[str, object]) -> PassengerConfig
load_freight_config(path: str | Path | None = None) -> FreightConfig
freight_config_from_mapping(product: Mapping[str, object]) -> FreightConfig
```

`PassengerConfig`: methodology_version, waiting_half_life_seconds, reference_profile.
`FreightConfig`: methodology_version, area_h_bands, delivery_half_life_seconds,
pipeline_products. H читается из указанного городской грузовой методикой источника;
население и приоритеты пассажирских категорий для грузов не нужны.
Часы явно переводятся в секунды. Проверяются approved, согласованность версий,
положительность параметров и поддерживаемые правила A/равновесных площадок;
включение трубопроводов в delivery index отклоняется.
Пути загрузки по умолчанию не зависят от cwd, отображения параметров неизменяемы.

## Межгородская пассажирская цепочка

```python
intercity_passenger_index(chain: PassengerChain, reference: ReferenceTime,
                          *, config: PassengerConfig) -> PassengerResult
intercity_car_index(chain_id: str, path_seconds: float | None, reference: ReferenceTime,
                    *, config: PassengerConfig) -> CarResult
```

`PassengerChain(chain_id, path_seconds, initial_wait_seconds)` содержит времена
**одной** уже принятой допустимой цепочки. Path — от первого отправления до
прибытия, с промежуточными ожиданиями/переходами, без повторения initial wait.
Нулевой path даёт `ValueError("INVALID_PATH_TIME")`; None означает нехватку данных.

`ReferenceTime(reference_id, seconds, profile_version, completeness,
applicability_confirmed)` передаётся вызывающим слоем. Подтверждение применимости
означает совпадение границ/узлов, области и версий пути с эталоном. Profile version
проверяется против конфигурации. Сам эталон здесь не строится.

`PassengerResult`: chain, reference, path_index, waiting_index, index,
completeness, diagnostics. `CarResult`: chain_id, reference, path_index,
completeness, diagnostics — компонента расписанного ожидания у автомобиля нет.
Время неизвестного эталона не уничтожает известный waiting_index.
Причины зависимого unknown: REFERENCE_UNAVAILABLE, REFERENCE_NOT_APPLICABLE,
REFERENCE_VERSION_MISMATCH; неизвестное время цепочки — DATA_UNAVAILABLE.

При partial reference численный результат доступен только как предварительный:
`index.status=available`, `completeness=partial`, диагностика PARTIAL_REFERENCE.
Не теряйте completeness при адаптации в Score. Unknown-компонент также даёт
partial completeness и null у зависимого индекса. Status никогда не принимает
значение partial. При ref > path сохраняется cap 100 и REFERENCE_INCONSISTENCY,
сам эталон не исправляется. Full здесь означает полноту расчёта данной цепочки
и её эталона, а не доказательство оптимальности среди всех маршрутов/режимов.

Пример: **синтетическая цепочка**, не реальный рейс:

<!-- executable-example -->
```python
from math import isclose
from a2_engine import (
    PassengerChain, ReferenceTime, intercity_car_index,
    intercity_passenger_index, load_passenger_config,
)

config = load_passenger_config()
reference = ReferenceTime("test-ref", 3600, config.reference_profile, "full", True)
result = intercity_passenger_index(
    PassengerChain("test-chain", 7200, 43200), reference, config=config,
)
assert result.path_index.value == result.waiting_index.value == 50
assert isclose(result.index.value, 50)
assert result.completeness == "full"
assert intercity_car_index("test-car", 7200, reference, config=config).path_index.value == 50
```

## Городские и межгородские грузы

```python
urban_cargo_site_index(site_id: str, travel_seconds: float | None, h_seconds: float | None,
                       *, scope: CargoScope, compatibility_confirmed: bool,
                       confirmed_no_route: bool = False) -> CargoSiteResult
urban_cargo_territory_index(sites: Sequence[CargoSiteResult], *, scope: CargoScope) -> MetricResult
intercity_cargo_index(*, cargo_profile_id: str, urgency: str,
                      initial_wait_seconds: float | None, movement_seconds: float | None,
                      handling_seconds: float | None, transfer_wait_seconds: float | None,
                      compatibility_confirmed: bool, config: FreightConfig) -> CargoDeliveryResult
```

`CargoScope(node_type, direction, cargo_profile_id)` задаёт тип узла, направление
inbound (приём площадкой) / outbound (отправка площадкой) и профиль груза.
`CargoSiteResult(site_id, scope, index)` сохраняет ID физической площадки.
Агрегат отклоняет повторный site_id и несовпадение scope; каждый исходный site
участвует один раз с равным вкладом, без населения и пассажирских приоритетов.
При unknown/not_applicable любой площадки полный агрегат unknown с причиной
SITE_INDEX_UNAVAILABLE; пустая выборка — not_applicable/ZERO_WEIGHT.
Применяется тот же H и экспоненциальный код, что для городских пассажиров.

`compatibility_confirmed=True` означает, что ML2/входной адаптер подтвердил
совместимость профиля, узла/цепочки и допустимость подъезда, включая направление.
False означает отсутствие подтверждения: индекс unknown/COMPATIBILITY_UNCONFIRMED.
Наличие порта/дороги такого подтверждения не даёт. Для confirmed_no_route действуют
те же требования полного покрытия/окна, что на первом этапе.

`CargoDeliveryResult`: cargo_profile_id, urgency, movement_seconds, total_seconds,
index. Времена — MetricResult в секундах. Все четыре непересекающихся компонента
складываются один раз. None в любом из них блокирует полный total/index,
но известное движение сохраняется. Известный 0 операции допустим.
При неподтверждённой совместимости известная сумма остаётся raw-временем цепочки,
индекс запрошенного профиля отсутствует. Срочность передаётся явно, из food class
или температуры не выводится. D и 31-дневное окно будущего поиска не ограничивают
длительность известной доставки. Переполнение суммы — TOTAL_TIME_OVERFLOW.

Пример: **только синтетические площадки и доставка**:

<!-- executable-example -->
```python
from a2_engine import (
    CargoScope, intercity_cargo_index, load_freight_config, select_h_seconds,
    urban_cargo_site_index, urban_cargo_territory_index,
)

config = load_freight_config()
h = select_h_seconds(100, bands=config.area_h_bands).value
scope = CargoScope("port", "outbound", "test-nonfood")
sites = [
    urban_cargo_site_index("test-site-a", 0, h, scope=scope, compatibility_confirmed=True),
    urban_cargo_site_index("test-site-b", None, h, scope=scope,
                           compatibility_confirmed=True, confirmed_no_route=True),
]
assert urban_cargo_territory_index(sites, scope=scope).value == 50
delivery = intercity_cargo_index(
    cargo_profile_id="test-nonfood", urgency="urgent", initial_wait_seconds=3600,
    movement_seconds=80000, handling_seconds=1800, transfer_wait_seconds=1000,
    compatibility_confirmed=True, config=config,
)
assert delivery.total_seconds.value == 86400
assert delivery.index.value == 50
```

## Трубопроводы

```python
pipeline_utilization(volume: float | None, capacity: float | None,
                     *, volume_basis: PipelineBasis, capacity_basis: PipelineBasis,
                     connection_confirmed: bool, config: FreightConfig) -> PipelineResult
```

`PipelineBasis(product, period, unit, connection_id, direction)` описывает
**нормализованную** величину за один период, а не произвольный rate. Сначала
ML2/адаптер приводит V и Q к одной единице и периоду, подтверждает направленное
подключение и фиксирует ID. Здесь проверяется точное равенство обеих bases;
конверсии единиц и интерпретации периодов нет. Различия дают
unknown/INCOMPARABLE_PIPELINE_DATA; неподтверждённое подключение —
CONNECTION_UNCONFIRMED, пропуск чисел — DATA_UNAVAILABLE. Продукт должен быть
в конфигурации, Q строго положителен, V неотрицателен. Невалидные числа отклоняются
даже при других пропусках. Переполнение процента — UTILIZATION_OVERFLOW.

`PipelineResult`: utilization_percent (MetricResult), diagnostics. Это процент
загрузки, **может превышать 100**, не проходит validate_index и не участвует
в индексе доставки. Более 100 сопровождается UTILIZATION_ABOVE_100.
Свободная коммерческая мощность не вычисляется.

Пример: **только синтетические объём и мощность**:

<!-- executable-example -->
```python
from a2_engine import PipelineBasis, load_freight_config, pipeline_utilization

basis = PipelineBasis("oil", "2026", "tonnes", "test-pipeline", "test-a->test-b")
result = pipeline_utilization(
    120, 100, volume_basis=basis, capacity_basis=basis,
    connection_confirmed=True, config=load_freight_config(),
)
assert result.utilization_percent.value == 120
assert result.diagnostics == ("UTILIZATION_ABOVE_100",)
```

## Сравнение готовых результатов

```python
delta_index(before: MetricResult, after: MetricResult,
             *, comparison: ComparisonContext) -> DeltaResult
time_saved_seconds(before: TravelTime, after: TravelTime,
                    *, comparison: ComparisonContext) -> DeltaResult
```

`ComparisonContext(comparable, verification_id)` требует явного решения входного
адаптера. verification_id — ссылка на сохранённую проверку одинаковых настроек,
точек/весов, методики/config, базового snapshot/графа, календаря, трафика,
полноты и границ метрик; исключением является только согласованное изменение.
Ядро не выполняет эту внешнюю проверку по двум числам. С partial-результатами
допустимо сравнивать только одинаковый явно заявленный поднабор, без полного
рейтинга. Без доказанной сопоставимости передавайте comparable=False:
дельта unknown/INCOMPARABLE_RESULTS. verification_id обязателен в обоих случаях.

`DeltaResult(value, status, reason_code=None)` принимает конечные **знаковые**
значения. Положительное — улучшение; отрицательное — ухудшение. MetricResult,
проверки исходных времён и диапазона индексов первого этапа не ослаблены.
Unknown компонент даёт null/DATA_UNAVAILABLE, индекс вне 0..100 отклоняется.

`TravelTime(seconds, route_exists)` отличает подтверждённый путь, подтверждённое
отсутствие и unknown (route_exists=None). Известное число требует route_exists=True.
Для отсутствующего пути используется **None**, не 0 или Infinity. Появление/потеря
связи даёт DeltaResult(None, not_applicable, CONNECTION_APPEARED) либо
CONNECTION_DISAPPEARED;
два отсутствующих пути — NO_CONNECTION. Известная индексная дельта между 0 с
NO_FEASIBLE_ROUTE и доступной связью допустима и также помечается CONNECTION_APPEARED.
Только время экономии при переходе без пути не имеет численной оценки.

Пример: **синтетические сопоставимые результаты**:

<!-- executable-example -->
```python
from a2_engine import ComparisonContext, MetricResult, TravelTime, delta_index, time_saved_seconds

comparison = ComparisonContext(True, "test-comparability-check")
assert delta_index(
    MetricResult(70, "available"), MetricResult(55, "available"), comparison=comparison,
).value == -15
assert time_saved_seconds(
    TravelTime(1800, True), TravelTime(2100, True), comparison=comparison,
).value == -300
new_connection = time_saved_seconds(
    TravelTime(None, False), TravelTime(600, True), comparison=comparison,
)
assert new_connection.value is None
assert new_connection.reason_code == "CONNECTION_APPEARED"
```

Все новые контейнеры — внутренние immutable-типы engine, не замена канонических
DTO BACKEND. Единицы времён/экономии — seconds, индексов/их дельт — points_0_100 /
index points, загрузки — percent. Подтверждения в примерах тестовые: они не
свидетельствуют о наличии реального источника, рейса или подключения.

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
  Для второго блока сохранить completeness отдельно от status, reference_id/profile
  и диагностики. Сохранять QA-предупреждение о превышении загрузки 100%; не валидировать
  этот процент как индекс. Delta value должен быть знаковым. Для времени отсутствующего
  пути хранить null, а не 0. Перед вычислением сравнений выпустить verification_id
  проверки фиксированных параметров/версий и передать его в ComparisonContext.
  Приёмка: partial reference не становится full Score, ухудшения -15/-300 сохраняются,
  появление связи не даёт Infinity; scope и physical site IDs доходят без потерь.
- **ML2:** для следующего этапа нужны принятые `population_cells`/`sample_points`
  из AcceptedData с ID исходной ячейки/доли, населением либо явным unknown,
  годом/методом/покрытием и административной площадью с версией границы.
  Приёмка: ноль отличается от unknown, доли не удваивают население, потери выборки
  не скрываются. Изменений ML2 для работы чистых функций сейчас не требуется.
  Для межгорода/грузов: `packages/data_pipeline/` → принятые reference_edges/
  minimum_transfers с версиями и областью применимости, freight_sites с физическими
  ID, freight_compatibility по направлению/профилю и pipeline_connections с
  нормализованными V/Q, продуктом, периодом, единицами и направлением. Критерий:
  пропуски операций не нули, совпадение периода/единиц проверяемо, повторные записи
  площадки не увеличивают её вклад. Булевы подтверждения выдаёт принятый адаптер,
  не пользователь формы и не LLM.
