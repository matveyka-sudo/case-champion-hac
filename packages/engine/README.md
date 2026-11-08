# Математическое ядро A2

Чистые расчёты ML1 по `docs/FORMULAS.md` и `config/product.json`: городские
пассажирские индексы, одна межгородская пассажирская цепочка, городские/межгородские
грузы, загрузка трубопровода, сравнение готовых результатов и городской τ по OD-матрицам.
Времена, население и подтверждения входных условий поступают от вызывающего слоя.
Маршрутизации, сетки, построения реальных OD-матриц, эталона A, изменения расписаний,
сценарного движка, HTTP и LLM здесь нет.

## Структура и зависимости

```text
a2_engine/
  __init__.py                 # прежние 37 экспортов + городской анализ по кандидатам
  shared/
    __init__.py
    config.py                 # конфигурации, загрузчики и их проверки
    results.py                # MetricResult и численная валидация
    scoring.py                # общая экспонента category_index
  urban/
    __init__.py
    indices.py                # городские пассажирские индексы и H
    models.py                 # нормализованный контекст и принятые входы
    journeys.py               # проверка временных цепочек и ожиданий
    selection.py              # минимум по физическим объектам
    analysis.py               # точки, категории, режимы и территория
    tau_models.py             # принятые OD-матрицы, календарь и scope
    tau.py                    # выбор базовой недели и точный расчёт τ
    walk.py                   # AcceptedData, план и сборка walk/static анализа
    valhalla.py               # типы, запросы и проверка ответов route JSON 3.5.1
    district_models.py        # исходные ячейки, районы, доли и покрытие
    district_inputs.py        # сверка узкого дополнения с canonical
    districts.py              # районные индексы, QA и сравнение
  intercity/
    __init__.py
    passenger.py              # пассажирская цепочка, эталон, автомобиль
  freight/
    __init__.py
    urban.py                  # физические площадки и равновесный агрегат
    intercity.py              # полная цепочка доставки по срочности
  pipeline/
    __init__.py
    utilization.py            # основания и процент загрузки
  comparison/
    __init__.py
    deltas.py                 # знаковые дельты и появление/потеря связи
```

Городские и межгородские пассажирские расчёты разделены. Весь грузовой код —
в freight, трубопроводы и сравнения имеют собственные пакеты. Конфигурации
остаются вместе в shared/config.py. Dataclass определяются один раз рядом
с использующими их функциями; реэкспорты ссылаются на тот же объект.

Внутренние реализации используют конкретные модули shared абсолютными импортами.
Shared не импортирует предметные пакеты. Внутри реализации нет импорта из
корневого a2_engine: его __init__.py собирает публичный интерфейс через явные
реэкспорты. Межгород и оба грузовых модуля используют shared/scoring.py напрямую,
не зависят от urban. Городской пакет реэкспортирует category_index из shared;
её сигнатура, проверки, значения по умолчанию и поведение сохранены.
Используемый только городским агрегатом _weighted_mean остаётся в urban/indices.py.

### Перемещения и совместимость

Пути в таблице указаны относительно src/a2_engine:

| Старый путь | Новый путь |
| --- | --- |
| config.py | shared/config.py |
| results.py | shared/results.py |
| urban.py: category_index | shared/scoring.py; реэкспорт через urban/__init__.py |
| urban.py: остальные расчёты | urban/indices.py |
| passenger.py | intercity/passenger.py |
| freight.py: городские грузы | freight/urban.py |
| freight.py: доставка | freight/intercity.py |
| pipeline.py | pipeline/utilization.py |
| comparison.py | comparison/deltas.py |

Основной контракт `from a2_engine import ...` сохранён целиком: прежние 37 имён,
их сигнатуры и значения по умолчанию зафиксированы до переноса и проверяются
в tests/test_public_api.py. Существующие пути a2_engine.urban, a2_engine.freight,
a2_engine.pipeline и a2_engine.comparison сохранены пакетными __init__.py
с явными экспортами предметных функций и типов. Файл и одноимённый пакет
не сосуществуют. Новый a2_engine.intercity собирает пассажирский межгород.

Поиск по репозиторию перед переносом не нашёл внешних потребителей
a2_engine.config, a2_engine.results и a2_engine.passenger: эти пути использовали
только прежние реализации engine. Они не были заявленным публичным контрактом,
поэтому модули совместимости не оставлены. Для внутренних импортов используйте
новые канонические пути; для интеграций — корневой публичный интерфейс.

Загрузка конфигурации по умолчанию привязана к фиксированному пути
`<checkout>/packages/engine/src/a2_engine/shared/config.py`: после переноса
используется parents[5], затем config/product.json. Это учёт новой глубины,
без поиска первого подходящего файла, зависимости от cwd или абсолютного пути
разработчика. При размещении пакета отдельно от checkout передавайте явный путь.
Тест копирует пакет вне checkout и проверяет все три загрузчика с таким путём;
отдельные процессы из другого cwd проверяют загрузку по умолчанию.

Городской анализ добавил 23 экспорта; расчёт τ добавил ещё 12, пешая интеграция — 15, районы — 17 (всего 104).
Зафиксированы все 87 прежних сигнатур; расширение destination прошлого этапа описано ниже. Предметные реализации могут импортировать свои внутренние
модули и shared; shared по-прежнему не зависит от предметных пакетов.

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

## Городской анализ по нормализованным кандидатам

```python
analyze_urban_candidates(
    context: UrbanContext,
    points: Sequence[UrbanPoint],
    destinations: Sequence[UrbanDestination],
    candidates: Sequence[UrbanCandidate],
    coverage: Sequence[UrbanCoverage],
    *, area: UrbanArea, selected_categories: Mapping[str, str], modes: Sequence[str],
    population_coverage: UrbanPopulationCoverage, config: UrbanConfig,
    rules: UrbanJourneyRules, thresholds: Sequence[UrbanThreshold] = (),
) -> UrbanAnalysis
validate_urban_journey(candidate: UrbanCandidate, *, rules: UrbanJourneyRules) -> UrbanJourneyTiming
load_urban_journey_rules(path: str | Path | None = None) -> UrbanJourneyRules
urban_journey_rules_from_mapping(product: Mapping[str, object]) -> UrbanJourneyRules
```

Это чистая обработка **переданных** цепочек. Сетевых вызовов, поиска по графу
и восстановления пропущенных интервалов нет. Сам analyze_urban_candidates не
пересчитывает τ: его отдельно вычисляет calculate_urban_tau по OD-матрицам. Численные индексы
рассчитывают прежние select_h_seconds/category_index/point_index/territory_index.
UrbanJourneyRules отдельно загружает max_total_wait_hours в секунды; сигнатура
UrbanConfig не меняется. Полный time limit относится к сумме ожиданий, не total.

### Входная граница

Все новые контейнеры frozen; коллекции копируются в tuple/frozenset. Их назначение —
внутренняя нормализованная граница engine, не альтернативный REST-контракт.

| Тип | Поля и смысл |
| --- | --- |
| UrbanVersions | snapshot_id, graph_version, calendar_version, population_version (nullable), geometry_version, methodology_version, config_hash, traffic_version |
| UrbanContext | city_id, territory_id выбранной области, requested_departure_at, traffic_mode, versions, search_parameters_id принятого набора ограничений/окна поиска |
| UrbanArea | city_id, административная area_km2 либо None, geometry_version границы, source_refs |
| UrbanPoint | point_id, исходный cell_id, логический origin_node_id точки, access_node_id сети, access_distance_m, territory_id, population либо None, population_year/method, source_refs, optional population_share_id |
| UrbanDestination | destination_id записи, object_identity учреждения, categories, access_node_id входа, territory_id, eligible=True/False/None, in_service_scope, source_refs |
| UrbanLeg | mode, kind, from/to_node_id, start/end_at, duration_seconds, service_id либо None, evidence_refs |
| UrbanCandidate | journey_id, point_id, destination_id, category, passenger_mode, context, origin/destination_node_id, departure/arrival_at, legs, evidence_refs, optional geometry |
| UrbanGeometry | Только переданный actual LineString: coordinates lon/lat и evidence_refs; None остаётся none |
| UrbanSearchScope | context, point_id, category, passenger_mode, max_total_wait_seconds |
| UrbanCoverage | scope, явные catalog_complete/search_complete, evidence_refs, catalog_destination_ids принятого каталога в данной категории/области, optional provider_error |
| UrbanPopulationCoverage | context, явный complete, excluded_population либо None, evidence_refs, point_ids ожидаемой выборки |
| UrbanThreshold | city_id, passenger_mode, versions базового расчёта, traffic_mode, search_parameters_id, seconds либо None, evidence_refs |

Адаптер обязан разрешить версии и config_hash по опубликованной конфигурации,
проверить QA, период и registry evidence принятого snapshot. Engine проверяет
согласованность переданных контекстов, а не подлинность источников по сети.
Отдельная версия traffic_version идентифицирует принятый профиль/ответ трафика:
имя режима static/typical/live/historical её не заменяет.

Логический origin_node_id соответствует выбранной точке, а не автоматически
ближайшему сетевому узлу. При положительном access_distance_m требуется
переданный положительный пеший префикс до access_node_id. Engine не назначает
скорость и не добавляет недостающие метры/секунды. Конечный узел должен совпадать
с принятым входом объекта. Географию/здания и пригодность подтверждает ML2.
Объект из соседней территории разрешён при in_service_scope=True.
eligible=False означает подтверждённую непригодность, None — пробел подтверждения;
этот пробел блокирует полный минимум категории.

### Цепочка, выбор и результаты

Datetime должны быть aware, на входной границе они переводятся в UTC.
Городской departure_at и первый start_at равны requested_departure_at. Узлы
и timestamps соседних legs должны стыковаться без разрывов/перекрытий; последний
конец совпадает с arrival_at. duration_seconds проверяется против UTC timestamps
с точностью канонического Leg (rel_tol=1e-12, abs_tol=1e-9); составляющие engine
суммируются из **фактических UTC-интервалов**, без округления для UI.

Поддержаны четыре kind: walk, ride, wait_initial, wait_transfer. Перенос между
остановками — walk. Ожидание не меняет узел; initial wait расположен до первой
поездки, transfer wait — между поездками. Для ОТ допустимы пешие legs и ride/wait
с mode public_transport/rail/water; автомобильные legs не принимаются как ОТ.
Для автомобиля — private_car с реальными пешими подходами, для walk — только walk.
Общие road/mixed и прочие operation kinds адаптер должен однозначно нормализовать
по подтверждённой семантике или отклонить; молча терять их длительность нельзя.

Структурно неверная цепочка вызывает ValueError и не исчезает из расчёта как
«нет пути». Корректная цепочка сверх лимита ожидания возвращает admissible=False,
MAX_TOTAL_WAIT_EXCEEDED и все времена. Лимит включительный и читается из config.
Даже поездка дольше четырёх часов допустима, если её ожидания проходят ограничение.

UrbanJourneyTiming сохраняет candidate, total/walking/riding/initial_wait/
transfer_wait/total_wait_seconds, waiting_share, admissible и reason_code.
Доля ожидания — fraction (0..1), при total=0 она not_applicable/ZERO_DURATION.
Ни двойного штрафа за пересадки, ни повторного учёта walk в ожидании нет.

Сначала проверяются **все** кандидаты, включая те, которые не будут выбраны.
Затем для point/category/mode выбирается лучший допустимый путь к каждому
object_identity, после — лучший физический объект. Стабильный ключ выбора:
(total_seconds, object_identity, destination_id, journey_id). Порядок входа
не влияет на результат. В разных категориях одно учреждение участвует отдельно.

UrbanCategoryResult сохраняет best, objects (один лучший путь на учреждение),
rejected, index, count, threshold, coverage, minimum_proven, completeness,
evidence_refs и limitations. SelectedUrbanObject содержит полную запись destination
и проверенную journey, поэтому сохраняются и физическая идентичность, и конкретный
вход/запись/маршрут. При неполноте best — только лучший **известный** путь.

UrbanPointResult сохраняет исходную point, mode, результаты категорий и index.
UrbanTerritoryResult содержит points, index, completeness, ranking_eligible и evidence.
UrbanAnalysis содержит context/area, неизменяемый выбор приоритетов, h_seconds,
все проверенные journeys, раздельные modes, population_coverage, status и limitations.
Статус верхнего анализа может быть partial; статус отдельной численной метрики
остаётся available/unknown/not_applicable. Ограничение GEOMETRY_UNAVAILABLE
не уничтожает известное время, но настоящая дорожная линия не синтезируется.

### Полнота и τ

Полный минимум/индекс требует доказанных полного каталога и поиска в **точном**
scope. Полнота snapshot не подменяет полноту ответа маршрутизатора. Свидетельство
охвата должно иметь evidence, правильные время/точку/категорию/режим/версии/опции
и roster catalog_destination_ids, совпадающий с переданными записями категории
в принятой области обслуживания. Флаги полноты не имеют значения True по умолчанию.

| Случай | Результат категории |
| --- | --- |
| Полный scope, допустимый путь | Численный индекс, полный минимум |
| Полный scope, допустимых путей нет | index=0, available/NO_FEASIBLE_ROUTE, evidence; best=None |
| Неполный/непереданный scope | index=null/unknown/INCOMPLETE_COVERAGE; известный best сохраняется |
| Ошибка провайдера | unknown/DATA_UNAVAILABLE; она не может подтверждать полный поиск |
| Неизвестная пригодность записи | unknown/DESTINATION_ELIGIBILITY_UNKNOWN |
| Неизвестная площадь | H неизвестен, полный индекс найденного пути неизвестен; путь сохраняется |

У отсутствующего пути нет нулевого времени и фиктивных legs. Ноль относится
только к индексу при доказанном полном отсутствии допустимого пути.

Полный агрегат территории дополнительно требует UrbanPopulationCoverage.complete,
excluded_population=0, точного совпадения point_ids с исходной выборкой и известной
версии населения. Иначе он unknown, не перенормированное среднее оставшихся жителей.
Неизвестное положительное население/индекс блокирует численное полное среднее.
Известный нулевой вес не портит остальных, нулевой знаменатель даёт ZERO_WEIGHT.
Дубли point_id, полных cell_id, одинаковых shares и смешение полного веса с его
долями отклоняются. Корректность подготовленных непересекающихся долей и суммы
исходной ячейки подтверждает ML2; engine не распределяет население по геометрии.

Count использует уже принятый τ: уникальные object_identity с лучшим T <= τ.
Требуются полный scope и совпадение city/mode/базовых versions/traffic/опций.
Отсутствующий/unknown τ даёт TAU_UNAVAILABLE и **не блокирует H-индекс**.
Чужой контекст τ отклоняется как TAU_CONTEXT_MISMATCH. Никаких выдуманных pending
jobs нет: dependency_job_id может назначить только BACKEND для настоящего задания.
Из-за неизвестного count общий analysis может быть partial при доступном полном
индексе; рейтинг относится к конкретному полному Score, не ко всем метрикам сразу.

### Сквозной пример: только синтетические данные

Ниже флаги полноты и evidence IDs — явно тестовые утверждения, не доказательства
готовности реального города. Две точки: 20/40 минут, население 90/10, H=20 минут.

<!-- executable-example -->
```python
from datetime import UTC, datetime, timedelta
from a2_engine import (
    UrbanArea, UrbanCandidate, UrbanContext, UrbanCoverage, UrbanDestination,
    UrbanLeg, UrbanPoint, UrbanPopulationCoverage, UrbanSearchScope, UrbanThreshold,
    UrbanVersions, analyze_urban_candidates, load_urban_config, load_urban_journey_rules,
)

config, rules = load_urban_config(), load_urban_journey_rules()
versions = UrbanVersions(
    "synthetic-snapshot", "synthetic-graph", "synthetic-calendar", "synthetic-population",
    "synthetic-geometry", config.methodology_version, "synthetic-config-hash", "synthetic-traffic",
)
context = UrbanContext("synthetic-city", "synthetic-city", datetime(2026, 11, 8, 8, tzinfo=UTC),
                       "static", versions, "synthetic-search-options")
area = UrbanArea(context.city_id, 100, versions.geometry_version, ("synthetic-area-evidence",))
points = [UrbanPoint(
    f"p{i}", f"cell{i}", f"p{i}", f"p{i}", 0, context.territory_id,
    population, 2026, "synthetic-estimate", ("synthetic-population-evidence",),
) for i, population in [(1, 90), (2, 10)]]
destination = UrbanDestination(
    "school-record", "physical-school", frozenset({"school"}), "school-node",
    context.city_id, True, True, ("synthetic-registry-evidence",),
)
candidates = []
for point, seconds in zip(points, (1200, 2400), strict=True):
    start, end = context.requested_departure_at, context.requested_departure_at + timedelta(seconds=seconds)
    leg = UrbanLeg("walk", "walk", point.origin_node_id, destination.access_node_id,
                   start, end, seconds, None, ("synthetic-route-evidence",))
    candidates.append(UrbanCandidate(
        f"route-{point.point_id}", point.point_id, destination.destination_id, "school", "walk",
        context, point.origin_node_id, destination.access_node_id, start, end, (leg,),
        ("synthetic-route-evidence",),
    ))
coverage = [UrbanCoverage(
    UrbanSearchScope(context, point.point_id, "school", "walk", rules.max_total_wait_seconds),
    True, True, ("synthetic-search-proof",), frozenset({destination.destination_id}),
) for point in points]
population_coverage = UrbanPopulationCoverage(
    context, True, 0, ("synthetic-sampling-proof",), frozenset(p.point_id for p in points),
)
tau = UrbanThreshold(context.city_id, "walk", versions, "static", context.search_parameters_id,
                     1200, ("synthetic-tau-proof",))
result = analyze_urban_candidates(
    context, points, [destination], candidates, coverage, area=area,
    selected_categories={"school": "medium"}, modes=("walk",),
    population_coverage=population_coverage, config=config, rules=rules, thresholds=(tau,),
)
assert [p.index.value for p in result.modes[0].points] == [50, 25]
assert [p.categories[0].count.value for p in result.modes[0].points] == [1, 0]
assert result.modes[0].index.value == 47.5 and result.modes[0].ranking_eligible
```

### Сверка с BACKEND d2cfe9a и следующий адаптер

Прочитаны реальные a2_contracts.v2 dataset/journeys/results/requests и
apps/api/docs/handoff-ml1-ml2.md из work/backend на d2cfe9a без слияния.
В текущем checkout этих Python DTO ещё нет: engine не импортирует их обязательно,
не импортирует a2_api/FastAPI/jobs и не выдаёт UrbanAnalysis за готовый AnalysisResult.

| Внутреннее поле | Канонический источник или отображение |
| --- | --- |
| context.city_id/territory_id/time/traffic; selected_categories/modes | UrbanPassengerRequest.city_id/area_id/departure_at/traffic_mode, categories/priorities/passenger_mode; отдельный запрос/результат на режим в API |
| context.versions | VersionRefs snapshot/graph/calendar/population/methodology/config; geometry_version — AcceptedData.provenance; code_version сохраняет BACKEND |
| area | AcceptedData.territories: административная площадь города, версия geometry и source_refs |
| point cell/population/year/method | SamplePoint + PopulationCell; источник — исходная ячейка, не здание/посетители |
| point origin/access | Логический узел точки плюс SamplePoint.access_node_id/access_distance_m; реальный connector передаёт маршрутизатор |
| destination | Destination.destination_id/object_identity/categories/access_node_id/territory_id/source_refs; eligible из принятого registry/category_ownership, in_service_scope из принятой области обслуживания |
| candidate/legs | JourneyResult.journey_id/point_id/boundaries/traffic/snapshot + Leg.kind/mode/nodes/timestamps/duration/service/evidence |
| выбранные object_identity и destination_id | object_identity остаётся в выбранной Destination; JourneyResult.destination_object_id связывается с её destination_id, идентичность восстанавливается через immutable dataset |
| timing | JourneyResult scope=urban, time_kind=complete_chain, total_seconds/initial_wait_seconds/total_wait_seconds; departure_at=requested_departure_at, arrival_at=последний leg; path_seconds/reference_id для urban можно оставить None |
| walking/riding/transfer_wait/waiting_share | Отдельные Metric с seconds/fraction и journey_id; не повторный штраф в Score |
| geometry | LineGeometry с переданными координатами/evidence; geometry_kind=actual либо none, не синтезированная линия |
| category/point/territory index | Score value/status/reason, completeness, methodology_version, SubjectRef point/category/mode/territory; components связывает BACKEND |
| territory basis | PopulationBasis: исходная сумма, year/method, coverage, unknown_positive_weight; ranking_eligible переносится без повышения |
| count/threshold | CountResult: float целого count преобразовать в int, threshold_seconds, status/reason/evidence; его metric_id назначает BACKEND |
| context/proofs/limitations | Версии/ограничения и ссылки на сохранённые доказательства охвата; финальные analysis/job IDs, hashes и публикация — BACKEND |

Особые условия адаптации: не превращать отсутствие journey в duration=0; не ставить
неизвестное время компонентом полного нулевого Score. При известном нулевом
населении не включать неизвестный Score этой точки в components полного
территориального Score — канонический валидатор запрещает зависимость полного
Score от unknown. Точку и её raw данные при этом сохранять отдельно.
При partial поиске известный JourneyResult может быть available/completeness=partial,
а индекс категории остаётся unknown. Все source/evidence IDs разрешаются в
AcceptedData.evidence того же snapshot и превращаются в EvidenceRef при публикации.
UrbanAnalysis.journeys включает все проверенные входы для QA: в канонические
available JourneyResult публиковать только допустимые маршруты нужного результата,
а rejected сохранять как диагностику. Полнота Metric числа объектов определяется
его собственным count: available count точен даже при неизвестном H; полноту
индекса категории нельзя слепо переносить на независимую метрику count.

Точные запросы к коллегам для следующей интеграции:

- **BACKEND, apps/api adapter и packages/contracts/src/a2_contracts/v2/**:
  сопоставить перечисленные поля; сохранить routing proof (point/category/mode,
  requested time/window/options, graph/traffic versions, catalog roster, evidence)
  и population roster без вывода полноты из одного AcceptedData.qa. Dataset.CoverageScope
  на d2cfe9a описывает более широкое покрытие и сам не является доказательством
  завершённого поиска в точке. Приёмка: полный/пустой полный/неполный/ошибка провайдера
  дают разные результаты, потерянные строки каталога/выборки не дают рейтинг.
- **BACKEND, packages/contracts/src/a2_contracts/v2/journeys.py**:
  сейчас JourneyResult содержит независимый порог 14400. Согласовать валидацию
  с опубликованным max_total_wait_hours. Сначала передавать сырые нормализованные
  Leg в engine, затем публиковать допустимый JourneyResult; недопустимый кандидат
  сохранять как причину, не пытаться выдать его за available journey. Приёмка:
  изменение конфигурационного лимита не оставляет два разных порога в системе.
- **Маршрутизатор (Valhalla/OTP adapter)**: передать один контекст, явные wait legs,
  реальные source/destination connectors и выбранные границы, непрерывные UTC-
  интервалы, service/evidence IDs, actual geometry только при наличии. Полнота
  ответа включает пагинацию/лимиты/окно и конкретные опции. Приёмка: 32 минуты,
  walk-transfer без двойного учёта, 4ч/4ч1м, несовместимый traffic отклоняется.
- **ML2, packages/data_pipeline/**: принятый roster точек/ячеек и учреждений,
  физический object_identity, пригодность по каждой категории, административная
  площадь с версией границы, population year/method и исключённое население.
  Для shares доказать непересечение и сохранение суммы исходной ячейки.
  Передать готовый τ с базовыми версиями либо explicit unknown. Приёмка:
  перемещение точки не меняет её population, дубли входов не увеличивают count,
  обслуживающий город объект за границей не удаляется автоматически.

HTTP-исполнение маршрутов, построение реальных OD-матриц, последний
обратный рейс, эталон A и graph overlay сценариев остаются следующими этапами.

## Точный городской τ по принятым OD-матрицам

```python
load_urban_tau_rules(path: str | Path | None = None) -> UrbanTauRules
urban_tau_rules_from_mapping(product: Mapping[str, object]) -> UrbanTauRules
calculate_urban_tau(
    baseline_context: UrbanContext, points: Sequence[UrbanPoint],
    population_coverage: UrbanPopulationCoverage, weeks: Sequence[UrbanTauWeek], *,
    passenger_mode: Literal["walk", "public_transport", "private_car"], timezone: str,
    reference_at: datetime, rules: UrbanTauRules,
) -> UrbanTauResult
UrbanTauResult.to_threshold() -> UrbanThreshold
```

τ считается по поездкам **между расчётными точками**, не к учреждениям.
UrbanTauRules читает tau_days/tau_times/max_total_wait_hours из product.json.
В утверждённом профиле это 7 дней × 08:00/12:00/18:00/22:00 = 28 срезов.
Правило max_lookback_weeks=8 взято из IMPLEMENTATION.md, раздел «Порог τ»:
это явно документированное значение нового типа правил, поскольку поля в JSON нет.
Изменения правил — операторские и версионированные, не коэффициенты пользователя UI.
В тестовом профиле можно проверить иное число первых дней недели/местных времён;
production использует опубликованный семидневный профиль без изменения JSON.

### Нормализованные матрицы и календарь

| Тип | Поля и обязательная семантика |
| --- | --- |
| UrbanODScope | city_id, passenger_mode, UrbanVersions, traffic_mode, search_parameters_id, IANA timezone, point_ids, max_total_wait_seconds; это OD-покрытие, не UrbanCoverage каталога учреждений |
| UrbanODPair | origin/destination_point_id, state, seconds либо None, явные search_complete/rules_confirmed, evidence_refs, optional reason_code |
| UrbanTauDay | service_date, ordinary/is_holiday (bool либо None), calendar_confirmed, service_calendar_confirmed, evidence_refs |
| UrbanTauSlice | scope, service_date, local_time, aware departure_at, pairs, coverage_confirmed, evidence_refs |
| UrbanTauWeek | стабильный week_id входного артефакта, scope, week_start, calendar, slices, evidence_refs, typical_profile_accepted, traffic_evidence_refs |

Pair state — reachable / confirmed_unreachable / unknown. Число разрешено для
reachable, но в среднее оно попадёт только при полном доказанном поиске, подтверждении
городских правил и непустых evidence. rules_confirmed удостоверяет учтённые подходы,
все ожидания и соблюдение лимита scope; производитель проверяет цепочки прежним
validate_urban_journey и доказывает минимум маршрутизатором. Сам по себе известный
путь в неполном поиске не является точным T_ij. Для confirmed_unreachable seconds
обязательно None: ни 0, ни Infinity не кодируют отсутствие поездки.
Без доказательств объявленная недостижимость становится неизвестным положительным
весом, а не исключённой парой. Ошибку провайдера передавайте как unknown с причиной.

Дубликат направленной пары, неизвестный ID, несовместимый scope внутри недели,
повтор timestamp и некорректное число — ValueError. Диагональ игнорируется в математике,
но некорректные исходные числа/ID всё равно отклоняются. Для положительных весов
нужны все i != j: пропущенная запись остаётся unknown даже при coverage_confirmed=True.
Пары с достоверным нулевым весом можно не передавать. Неизвестное население не ноль.
Повторное использование полного cell_id/доли отвергается существующей проверкой точек.
Потерянное население, неподтверждённый population roster и неизвестная версия населения
блокируют полный городской τ. Для τ baseline_context.territory_id должен быть city_id.

### Границы базовой недели

Неделя — ISO-период с понедельника 00:00 до следующего понедельника 00:00
в IANA-зоне города, конец исключён. Текущая неделя местной даты reference_at
не используется. Рассматриваются восемь предыдущих завершённых ISO-недель:
week_start от current_monday−8 недель до current_monday−1 недели включительно.
Возраст измеряется номером завершённой календарной недели, а не 56×24 часами
от времени суток запроса. На точном местном понедельнике 00:00 прошлая неделя
уже завершена; за микросекунду до него она ещё текущая.

Весь семидневный календарь должен быть явно подтверждён, ordinary=True,
is_holiday=False. Для ОТ дополнительно подтверждаются реальные сервисные даты
после календарных исключений. По названию дня или системному календарю праздников
ничего не выводится. Typical требует принятого профиля с evidence; static не
подменяет typical. Равные численные матрицы разных срезов допустимы.

Local date/time каждого среза сверяются с UTC instant через ZoneInfo. Для дней
перехода DST используются соответствующие смещения. Неоднозначное или несуществующее
местное время заданного профиля делает неделю непригодной с отдельной причиной:
движок не выбирает fold и не сдвигает час сам. В обычном профиле 08/12/18/22 это
проверка редких календарных границ, а не добавление случайного зимнего коэффициента.

Из подходящих недель выбирается ближайшая. Поздняя праздничная/неполная неделя
не мешает выбрать более раннюю допустимую. Все переданные кандидаты сохраняются
в result.weeks с причинами; при отсутствии подходящей недели τ unknown с
NO_SUITABLE_BASELINE_WEEK и подробным result.reasons. Системные now/today не используются.

### Математика, диагностика и численная устойчивость

Для каждого среза считается Σ(P_i P_j T_ij) / Σ(P_i P_j) только по доказанно
достижимым направленным парам. Затем — равновесное среднее всех обязательных
срезов. Пул всех достижимых пар недели дал бы неверный результат при меняющейся
достижимости. Один срез без положительного достижимого знаменателя либо с unknown
положительным весом делает неделю непригодной; недостижимые пары не становятся нулевыми временами.

UrbanTauResult сохраняет baseline_context, explicit reference_at, rules, точки и
популяционное покрытие, выбранную selected_week, все weeks, τ/причины и evidence.
UrbanTauWeekResult содержит week_id/start/scope, τ и обязательные slices; отсутствующий
срез представлен диагностикой MISSING_MATRIX_SLICE с неизвестным весом, не нулевым средним.
UrbanTauSliceResult содержит местную дату/время, UTC departure, mean_seconds,
reachable_weight, confirmed_unreachable_weight, unknown_weight, weight_denominator,
unreachable_weight_share, причины и evidence.

Знаменатель доли недостижимости — **все** положительные Σ_{i != j} P_i P_j среза,
включая unknown, а не только достижимые. За неделю он суммируется по обязательным
срезам; числитель — сумма подтверждённо недостижимого веса по ним. Например,
14 срезов с обеими достижимыми парами и 14 с одной недостижимой дают долю 1/4.
Если оценка весов не выполнялась из-за непригодного календаря/населения, свойства
веса недели возвращают None, а не выдуманный 0. При нулевом знаменателе доля None.

Веса хранятся как Fraction (persons² на срез, сумма pair-slice весов за неделю).
Все произведения и суммы вычисляются точно для переданных чисел, включая 1e308²;
точные средние срезов используются и для окончательного τ. Только публикуемые
секунды переводятся в float. Если положительное значение невозможно представить
без обнуления — NUMERIC_UNDERFLOW/unknown; неконечное представление — NUMERIC_OVERFLOW.
Для JSON BACKEND должен сериализовать Fraction явно, например numerator/denominator
десятичными строками; преобразование больших весов в float недопустимо.

### Подключение и фиксация базового τ

result.to_threshold() сохраняет исходные city/mode/versions/traffic/search parameters.
Аргумента для их подмены нет. Готовый UrbanThreshold передаётся в прежний
analyze_urban_candidates(..., thresholds=(threshold,)). При unknown τ seconds=None:
H-индексы продолжают работать, count получает TAU_UNAVAILABLE. Подробные причины
нужно сохранять вместе с UrbanTauResult; существующий UrbanThreshold не заменяет
артефакт с базовой неделей и диагностикой.

Движение ползунка времени повторно использует тот же порог, не меняет reference_at
и не запускает выбор недели автоматически. Для сценария хранится ссылка на baseline
артефакт. При смене версии графа нельзя переписать versions порога: текущий городской
анализ такой контекст отвергнет. Будущий scenario adapter должен различать baseline
и overlay, сохраняя исходный τ; сценарный движок здесь не добавлен.

Ключ неизменяемого артефакта для BACKEND: city/mode/timezone, selected week_id/start,
точный roster/основание населения, все UrbanVersions, search_parameters_id, параметры
UrbanTauRules и evidence принятого профиля. Для воспроизведения выбора сохраняются
reference_at и идентичности всех недель-кандидатов. Текущее время запроса не подменяет
момент выбора. Hashes, кеш, сохранение, реальные jobs/dependency_job_id — BACKEND.

### Исполнимый пример τ: только синтетические матрицы

Здесь подтверждения и evidence IDs тестовые. Это не расчёт по реальным маршрутам
Калининграда. При тех же исходных версиях полученный threshold можно подставить
в предыдущий городской пример вместо вручную заданного числа 1200.

<!-- executable-example -->
```python
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from a2_engine import (
    UrbanContext, UrbanODPair, UrbanODScope, UrbanPoint, UrbanPopulationCoverage,
    UrbanTauDay, UrbanTauSlice, UrbanTauWeek, UrbanVersions,
    calculate_urban_tau, load_urban_tau_rules,
)

rules = load_urban_tau_rules()
reference_at = datetime(2026, 11, 8, 12, tzinfo=UTC)
versions = UrbanVersions("synthetic-snapshot", "synthetic-graph", "synthetic-calendar",
    "synthetic-population", "synthetic-geometry", rules.methodology_version,
    "synthetic-config-hash", "synthetic-traffic")
context = UrbanContext("synthetic-city", "synthetic-city", reference_at, "static",
                       versions, "synthetic-search-options")
points = [UrbanPoint(f"p{i}", f"cell{i}", f"p{i}", f"p{i}", 0, context.city_id,
    population, 2026, "synthetic-estimate", ("synthetic-population-evidence",))
    for i, population in [(1, 90), (2, 10)]]
ids = frozenset(p.point_id for p in points)
population = UrbanPopulationCoverage(context, True, 0, ("synthetic-population-proof",), ids)
scope = UrbanODScope(context.city_id, "walk", versions, "static", context.search_parameters_id,
                     "Europe/Kaliningrad", ids, rules.max_total_wait_seconds)
start = date(2026, 10, 26)
calendar = tuple(UrbanTauDay(start + timedelta(days=i), True, False, True, True,
    ("synthetic-calendar-proof",)) for i in range(7))
pairs = (UrbanODPair("p1", "p2", "reachable", 600, True, True, ("synthetic-minimum-proof",)),
         UrbanODPair("p2", "p1", "reachable", 1800, True, True, ("synthetic-minimum-proof",)))
slices = tuple(UrbanTauSlice(scope, start + timedelta(days=i), clock,
    datetime.combine(start + timedelta(days=i), clock, ZoneInfo(scope.timezone)),
    pairs, True, ("synthetic-matrix-proof",))
    for i in range(rules.tau_days) for clock in rules.tau_times)
baseline = UrbanTauWeek("synthetic-week-2026-10-26", scope, start, calendar, slices,
                        ("synthetic-week-proof",), False, ())
tau_result = calculate_urban_tau(context, points, population, (baseline,),
    passenger_mode="walk", timezone=scope.timezone, reference_at=reference_at, rules=rules)
assert tau_result.tau_seconds.value == 1200
assert tau_result.selected_week is not None and len(tau_result.selected_week.slices) == 28
threshold = tau_result.to_threshold()
assert threshold.seconds == 1200 and threshold.versions is context.versions
```

Контрольные результаты: две точки 90/10 и 600/1800с → 1200с; три точки 1/2/3
с суммой весов 22 и взвешенной суммой 930мин → (930/22)×60 ≈2536.363636с;
14 срезов со средним 15мин и 14 со средним 60мин →2250с, не ошибочные 1800с.
Сквозное подключение к count проверяется в tests/test_urban_tau.py: 50/25,
47.5 и counts 1/0; неизвестный τ не убирает готовый индекс.

### Реальные входы и передача для построения матриц

Проверена опубликованная work/ml2 до актуального 02221bf без слияния. После
указанного в задании de32230 уже есть walk access nodes и адаптер AcceptedData v2:
bundle kaliningrad-urban-2026-11-08.2 содержит 216 точек и 255 привязок, 39 принятых
объектов, а QA указывает **277.8 жителей в исключённых ячейках**. Это не полные
OD-времена и не основание полного городского τ. Готовых OD-матриц нет; расписания
ОТ и автомобильные привязки пока отсутствуют. Текущий checkout ML1 эти данные
не копирует и не объявляет реальный τ Калининграда рассчитанным.

- **ML2, packages/data_pipeline/**: передать городской roster без повторных весов,
  population version/year/method, полный учёт исключённых жителей, принятую
  обычную неделю и календарь реальных сервисных дат, source/network connectors.
  Нужно устранить потерю населения либо сохранить unknown полного τ; не раздать
  277.8 жителей оставшимся точкам. Критерий: неизменяемое основание точек и весов,
  все календарные подтверждения имеют evidence, неизвестное не превращается в 0.
- **Маршрутный слой ML1**: построить точные направленные OD-матрицы всех положительных
  пар для каждого обязательного среза. Пропуски/таймауты — unknown. Отдельно
  доказать минимум допустимого времени либо отсутствие допустимого пути при
  полном scope; учесть source/destination connectors и ожидания один раз.
  Критерий: совпадают snapshot/graph/calendar/geometry/population/traffic/methodology/
  config/options/roster; каждый положительный отсутствующий маршрут имеет явную
  подтверждённую запись, а не просто пропущен.
- **BACKEND, packages/contracts/src/a2_contracts/v2/ и apps/api adapter**:
  нужен отдельный контракт OD-покрытия и сохранённый UrbanTauResult с неделей,
  календарём, весами и baseline reference. Нельзя переиспользовать institutional
  CoverageScope как доказательство матрицы. Подключить to_threshold без подмены
  версий, сериализацию Fraction, кеш и настоящие фоновые задания. Приёмка:
  неизвестный τ не блокирует H-Score, count не получает выдуманный pending job,
  сдвиг ползунка не выбирает новую неделю, overlay не переписывает baseline.
- **DEVOPS, packages/engine/pyproject.toml и CI**: остаются установка src-пакета и
  запуск engine pytest/Ruff/mypy из закреплённого окружения; зависимостей для этого
  этапа не добавлено. До исправления упаковки используется PYTHONPATH.

## Пешая интеграция AcceptedData → Valhalla → UrbanAnalysis

Реализован чистый срез walk/static в urban/walk.py и urban/valhalla.py. Engine
не выполняет HTTP, не загружает producer bundle и не импортирует a2_contracts/a2_api.
Преобразование BundleData → AcceptedData v2 остаётся единственным у ML2.
Вызов prepare_walk_inputs получает model_dump(mode="json") уже проверенного
BACKEND-моделью AcceptedData. Он проверяет дополнительные инварианты проекции,
но не заменяет каноническую валидацию. Все входные данные сохраняются неизменными.

```python
prepare_walk_inputs(accepted, *, city_id, departure_at, methodology_version,
                    config_hash, search_parameters_id, build=None,
                    connectors=(), correlations=()) -> WalkInputs
plan_walk_routes(inputs, *, point_ids, categories) -> WalkRoutePlan
normalize_walk_response(request, envelope) -> WalkRouteResult
analyze_walk_responses(plan, responses, *, selected_categories, config, rules,
                       thresholds=()) -> WalkAnalysisResult
```

Добавлены 15 явных экспортов, всего 87. Все 72 прежних имени сохранены.
Единственное расширение прежней сигнатуры — keyword-only
UrbanDestination.physical_node_id=None. Восьмипозиционный старый вызов по-прежнему
заканчивает цепочку на access_node_id. С новым полем требуется реальный конечный
walk leg от access_node_id к physical_node_id; проверка границ пути сохранена.
Снимок публичного интерфейса теперь защищает все 72 прежних имени; старый вызов
UrbanDestination дополнительно проверяется отдельным тестом.

### Оба подхода и привязка к сети

Физические ID заданы однозначно: physical:point:<point_id> и
physical:destination:<destination_id>. Они обозначают исходные location принятых
записей, а не узлы OSM или Valhalla. access_node_id остаётся исходным ML2 ID.
Полная цепочка содержит три последовательных walk leg:
physical origin → source access → destination access → physical destination.
Первый и последний имеют длительности WalkConnector.seconds; средний —
trip.summary.time. Каждый участок учитывается ровно один раз.

WalkConnector фиксирует snapshot/source_graph, физический и сетевой ID, обе
координаты, seconds, traversable, time_basis и evidence_refs. Принят только
traversable=True с известным временем и evidence. Неизвестный подход блокирует
запрос; даже совпадение координат не доказывает нулевой подход. В этом этапе
потребляется переданное подтверждённое время: скорость для подходов не вычисляется.
access_distance_m расчётной точки не берётся из initial_center и не превращается
в секунды. Отсутствующий destination access distance не заполняется нулём.

ValhallaBuild раздельно хранит snapshot_id, исходный source_graph_version ML2,
build_id, фактический graph_version тайлов, engine_version, profile_version,
полный pedestrian_options_json и evidence. После принятия build контекст engine
использует именно фактический graph_version тайлов; исходная версия привязок
сохраняется отдельно. Snapshot сохраняется. Без build создаётся только входная
проекция с source graph и static:unbound — запросы не выдаются.

Для пилота поддержана JSON-схема Valhalla **3.5.1**, а не незакреплённый latest.
Проверены официальные [route docs 3.5.1](https://github.com/valhalla/valhalla/blob/3.5.1/docs/docs/api/turn-by-turn/api-reference.md),
[matrix docs 3.5.1](https://github.com/valhalla/valhalla/blob/3.5.1/docs/docs/api/matrix/api-reference.md)
и [pedestrian costing source 3.5.1](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/pedestriancost.cc).
Принятая production-сборка пока отсутствует: поддержка формата не означает её наличие.
walking_speed обязательно передаётся в профиль явно, источник/принятие — build evidence.
Число 5.1 в примере — только параметр синтетического профиля, не принятая скорость
реальных подходов. profile_hash включает версию движка, профиля и канонические options.

ValhallaCorrelation связывает ML2 access ID/координату с фактической принятой
корреляцией конкретного build, correlation_id и evidence. OSM way/offset не
выдаются за внутренний ID Valhalla. При отсутствии внутреннего ID достаточно
подтверждённой координаты и артефакта корреляции, сохраняющего выбранную дорогу.
Изменение координаты корреляции требует нового согласованного connector и версии;
этот пилот отклоняет его как REVISED_ACCESS_CONNECTOR_REQUIRED. На перекрёстках
одних координат недостаточно: evidence должно различать выбранную дорогу/направление.
Текст ID сам по себе не является доказательством.

### План, внешний исполнитель и response envelope

WalkRoutePlan содержит неизменяемые inputs, выбранные point_ids/categories,
requests и issues для заблокированных пар. Перебираются **все** принятые объекты
выбранной категории, включая несколько входов одного физического объекта.
Сокращения по прямому расстоянию нет. Порядок и request_id детерминированы.

Каждый WalkRouteRequest сохраняет point/destination IDs, build/profile, параметры
поиска, обе корреляции, evidence, network_only и payload_json. payload() возвращает
отдельный JSON dict для POST /route: два break locations с lon/lat, pedestrian,
полные costing_options, units=kilometers, format=json, maneuvers, alternates=0,
id=request_id. payload_hash — SHA256 UTF-8 строки payload_json, без её
переформатирования. date_time отсутствует для static. Параметры radius=0/search_cutoff=1/
node_snap_tolerance=0 не доказывают отсутствие повторной привязки; фактическая
корреляция проверяется отдельно. exclude_ferry в endpoint filter также не
доказывает отсутствие парома во всём пути.

Внешний слой выполняет план в следующем порядке:

1. Проверяет принятую сборку, effective profile и корреляции на этой сборке.
2. Отправляет неизменённый payload, сохраняет его hash и сырой ответ/content hash.
3. Фиксирует исход выполнения, фактически выбранные endpoints и их доказательства.
4. Проверяет состав рёбер на чистый walk; сохраняет доказательство отсутствия ferry.
5. Передаёт WalkResponseEnvelope в engine; повторные попытки и jobs остаются снаружи.

Envelope содержит request_id, направленные point_id/destination_id, полный build,
request_payload_hash, origin/destination correlation IDs, outcome, response_json,
evidence_refs, search_complete, minimum_time_evidence_refs,
unreachable_evidence_refs, pure_walk_evidence_refs, matched_origin/destination и
endpoint_evidence_refs. Эти сведения поступают от доверенного исполнителя после
проверки сохранённых артефактов. Они не копируются из полей входного запроса.
Повторённые trip.locations подтверждают только echo; без доказательств фактических
endpoints результат ACTUAL_ENDPOINTS_UNCONFIRMED. Несовпадение пары, build, профиля,
hash, выбранных endpoints или геометрии — ValueError, без смешивания контекстов.

WalkRouteResult сохраняет также SHA256 исходного response_json (response_hash),
чтобы сопоставлять результат с неизменяемым сохранённым ответом.
Для успешного JSON проверяются status, warnings, единицы, summary/leg, maneuvers,
конечность и неотрицательность чисел, pedestrian/foot, отсутствие ferry/transit.
Время — elapsed seconds из summary.time, не routing cost и не recosting.
Расстояние переводится из kilometers/miles в метры. Поддерживается один сетевой leg
между двумя endpoints; multi-leg/matrix/OSRM/multimodal не входят в пилот.
Опциональная polyline6 декодируется в (lon,lat), проверяется и хранится только как
network_geometry. Она не становится полной candidate.geometry: реальные геометрии
подходов пока не передаются, недостающие линии не дорисовываются.

Pedestrian выбирает путь по собственному cost, который может отличаться от elapsed
time. shortest и recosting не считаются доказательством минимального времени.
Без принятого аудита minimum_time_evidence_refs и полного поиска маршрут остаётся
лучшим известным кандидатом, minimum_proven=False; индекс категории unknown.
Это внешний обязательный аудит конкретного профиля, графа, ограничений и всех
допустимых входов. Engine проверяет соответствие его идентичности, а не выполняет
повторный поиск по графу. У реального текущего набора такого аудита нет.

Timeout/HTTP error/limit/no correlation, пропущенный ответ, неполный JSON, предупреждения
и код 442 сохраняют unknown с причиной. Только 442 вместе с отдельным достаточным
unreachable evidence и полным поиском подтверждает недостижимость. Ошибка не
исключает объект из обязательного набора и не превращается в нулевой индекс.

Каталог и поиск проверяются раздельно: partial catalog сохраняет лучший известный
маршрут, но не полный индекс. object_identity дедуплицирует несколько входов для
count. Потерянное население и пилотное подмножество точек блокируют городской рейтинг.
Отсутствующий τ не блокирует готовый H-индекс, count остаётся TAU_UNAVAILABLE.
Согласованный существующий UrbanThreshold можно передать через thresholds; его
контекст и версии проверяются прежним анализом. not_collected calendar допустим
для walk/static, но не подтверждает базовую неделю τ.

### Разрешение временных меток walk

На границе сборки цепочки datetime/timedelta нормализует длительности до
микросекунд. UrbanLeg получает фактическое (arrival−departure).total_seconds(),
а исходные секунды сохраняются без изменений в WalkConnector и WalkRouteResult.
Это нормализация представления временных меток; формулы индексов и τ не округляются.
Положительный участок, превратившийся в нулевой интервал, отклоняется явно с
WALK_DURATION_BELOW_TIMESTAMP_RESOLUTION; нулевые исходные участки допустимы.
Строгий validate_urban_journey для произвольных входных цепочек не ослаблен.

### Воспроизводимые прогоны

Самостоятельное воспроизведение сохранённого **синтетического** ответа, без HTTP:

```bash
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_walk.py
```

<!-- executable-example -->
```python
import runpy

demo = runpy.run_path("packages/engine/examples/synthetic_walk.py")
result = demo["run_demo"]()
assert [leg.duration_seconds for leg in result.analysis.journeys[0].candidate.legs] == [60, 1620, 120]
assert result.analysis.journeys[0].total_seconds == 1800
assert result.analysis.h_seconds.value == 1800
assert result.analysis.modes[0].points[0].index.value == 50
```

synthetic_walk_response.json — маленький сохранённый тестовый ответ с request_id;
все acceptance/proof/build IDs в этом примере синтетические. Геоданные этого
примера также синтетические. Отдельный тестовый canonical subset Калининграда
содержит реальные геоданные ML2, но его маршрутные ответы и подтверждения тестовые.

Для реального bundle используйте установленные/доступные через PYTHONPATH версии
packages/data_pipeline/src из work/ml2@02221bf и packages/contracts/src из
work/backend@d2cfe9a. В A2_DATA_DIR укажите каталог data с опубликованным bundle;
engine/src также должен входить в PYTHONPATH. Исходники соседей не копируются в engine.

```bash
uv run --frozen python packages/engine/examples/check_walk_bundle.py --bundle kaliningrad-urban-2026-11-08.2 --departure-at 2026-11-08T12:00:00+00:00
```

Этот прогон реально выполнен через load_accepted_data, то есть существующее
преобразование ML2 и каноническую валидацию BACKEND. Запись результата:
examples/kaliningrad_walk_readiness.json, полный canonical payload hash
1c437d5a042f952eae17a1d787ad7836792aeafa89f0e4ba8377070f0e0c5f47.
Подтверждены 1/286/216/39/255/1228 city/cells/points/objects/access/evidence.
Сумма населения исключённых ячеек по исходным числам **277.772**, в QA округлено 277.8.
Пилот airport и три перечисленные в записи точки дают 3 заблокированные пары,
0 исполнимых запросов: нет build, принятых Valhalla correlations и connectors.
HTTP-прогон не состоялся; реальные времена и индексы маршрутов не получены.
В опубликованных соседних ветках executor/build не обнаружены; локальный Docker
недоступен (docker.sock отсутствует). Публичный demo не использовался как production.

### Точная передача соседним ролям

- **BACKEND — apps/api/** и **packages/contracts/src/a2_contracts/v2/**:
  предоставить внешний исполнитель POST /route и сохранение request/envelope по
  описанным полям. Формализовать отдельные connector/correlation/route attestations,
  не institutional CoverageScope. Проверить реальную корреляцию по возможностям
  3.5.1 (/locate и сведения фактического пути), сохранить chosen road в evidence;
  не доверять echo. Принять аудит минимума времени профиля либо сохранять unknown.
  Приёмка: timeout/442 не no-route, hash/build/endpoints совпадают, сохранённый ответ
  воспроизводится, частичный поиск не становится полным, job создаётся реальным worker.
- **ML2 — packages/data_pipeline/** и immutable bundle supplement:
  передать snapshot_id/source_graph_version, physical/access coordinates, osm_way_ref,
  offset_along_way_m, destination access_distance_m и evidence привязок без потерь.
  Отдельно нужны подтверждение проходимости обоих connectors и известное время с
  time_basis/version (или согласованная модель, параметры и источник). Расстояния
  проекций не доказывают проходимость. Сохранить исключённых 277.772 жителей и
  partial/quarantine категорий; не перераспределять население молча. Приёмка:
  prepare_walk_inputs связывает дополнение с исходной версией, неизвестное остаётся unknown.
- **DEVOPS — infra/**, manifests/CI:
  согласовать стабильный image digest Valhalla 3.5.1, build/tiles hash, source OSM hash,
  runtime config/effective costing profile и endpoint для BACKEND executor.
  Другую версию сначала проверить на совместимость адаптера; engine отклоняет её явно.
  Приёмка: опубликован воспроизводимый build manifest и рабочий /route, engine tests
  проходят в установленном src-пакете. Зависимости и lock этим этапом не менялись.

## Районы: веса исходных ячеек, Gap, Deficit и рейтинг

Чистый районный слой urban/districts.py принимает уже рассчитанный UrbanAnalysis.
Он не ищет маршруты, не меняет категории/приоритеты и не ограничивает каталог
учреждений границами района. Общий городской H сохраняется в comparison scope:
для площади Калининграда 224.668 км² это 1800с, независимо от площади района.

```python
analyze_urban_districts(
    urban_analysis: UrbanAnalysis,
    original_cells: Sequence[UrbanOriginalCell],
    district_catalog: Sequence[UrbanDistrict],
    population_shares: Sequence[UrbanDistrictShare],
    coverage: UrbanDistrictCoverage,
) -> UrbanDistrictAnalysis
```

Готовый UrbanAnalysis передаётся напрямую, в том числе из
analyze_walk_responses(...).analysis. Районный результат не зависит от наличия
Valhalla. Верхние UrbanAnalysis.status и city ranking_eligible не используются
как готовность районного H-индекса: τ/count могут оставаться unknown.
Точечные/категорийные index и их собственная полнота проверяются отдельно.

### Входы, версии и проверки

| Тип | Содержание |
| --- | --- |
| UrbanOriginalCell | cell_id, city_id, grid_version, исходное population/year/method, point_id либо None, excluded, evidence_refs |
| UrbanDistrict | district_id, city_id (родитель), name, boundary_version, evidence_refs; только administrative_level=district |
| UrbanDistrictShare | cell_id, district_id, population (абсолютные жители либо None), evidence_refs; optional area_share только для аудита |
| UrbanSharePrecision | cell_quantum, share_quantum, evidence_refs; явно принятый rule=cell_nearest_share_floor |
| UrbanDistrictCoverage | исходный UrbanContext, catalog_version/distribution_version, grid_versions, district_boundary_versions, expected_district_ids/expected_cell_ids, complete_district_ids, original_cells_complete, evidence_refs, optional precision |

В одном вызове поддерживается одна согласованная группа районов одного города.
Полный context должен совпасть с UrbanAnalysis: snapshot, graph, population,
geometry, traffic/mode, methodology, config, request time и search parameters.
Режимы и метрики никогда не смешиваются между собой. Состав категорий и приоритеты,
H, версии каталога/долей/сетки/границ сохраняются в каждом UrbanDistrictComparison.
Рейтинг разных UrbanAnalysis этим API не строится: вся группа использует один
исходный контекст. Сравнение сторонних сохранённых групп требует проверки полного scope.

Отклоняются дубликаты ячеек/точек/районов/пар долей, несоответствие point→cell,
населения, года и метода, неподходящий город/уровень/границы/сетка, неизвестные IDs,
отрицательные/NaN/Infinity веса. Сумма известных районных весов одной ячейки не
может превышать её исходное население. Ни area_share, ни число объектов/входов
не используются как вес. Повторная геометрическая классификация отсутствует.

Отдельная prepare_urban_district_inputs принимает UrbanAnalysis и JSON-дампы
**уже проверенных** канонического AcceptedData и producer BundleData. Это узкая
проекция районного дополнения, а не ещё один BundleData→AcceptedData адаптер.
Она сверяет immutable snapshot, population/geometry/source graph, исходную границу
города, полный roster ячеек/точек, grid и population/year/method каждой ячейки,
sampled/excluded и parent_id районов. Возвращает UrbanDistrictInputs с четырьмя
аргументами для основного вызова. Принятую полноту и точность caller передаёт явно:

```python
prepared = prepare_urban_district_inputs(
    analysis, accepted.model_dump(mode="json"), bundle.model_dump(mode="json"),
    catalog_version=catalog_version, distribution_version=distribution_version,
    complete_district_ids=confirmed_district_ids, original_cells_complete=True,
    evidence_refs=coverage_evidence, precision=accepted_precision,
)
result = analyze_urban_districts(
    analysis, prepared.original_cells, prepared.district_catalog,
    prepared.population_shares, prepared.coverage,
)
```

### Население, точность и полнота

Для района territory_index применяется к абсолютным весам P_cell,r; у общей
ячейки в двух районах один и тот же точечный индекс и разные веса. Для города
territory_index вызывается независимо на **исходных** P_cell, каждая ячейка один раз.
Город не получается средним районов и не заменяет свой знаменатель суммой долей.
Так же отдельно агрегируются выбранные категории и общий точечный индекс,
отдельно для каждого режима. Формулы индексов не дублируются.

Unknown точки с положительным весом блокирует только затронутые районы.
Известный нулевой вес не требует индекса; неизвестный вес не ноль. Подтверждённый
индекс 0 с положительным весом участвует в среднем. Нулевая подтверждённая масса
даёт not_applicable/ZERO_WEIGHT, не индекс 0. Для отсутствующих пар cell/district
нулевой вклад допустим лишь при явно принятом полном распределении района.

Excluded-ячейки не исчезают: их положительный либо неизвестный районный вес
блокирует полный результат этого района. Город также видит их исходный вес и
отсутствующий индекс точки. Если исключённый вес равен 0, он не влияет на среднее.
Население не переносится к соседним точкам. Полнота города, разметки районов и
собственного покрытия каждого района проверяется раздельно. Для города также
сохраняются прежние проверки полного population roster и excluded=0 из анализа. Отсутствующие доли
не портят полный город из исходных ячеек.

Суммы населения, allocated и residual сохраняются как Decimal, полученный из
десятичных представлений входных чисел; для JSON передавайте их десятичными
строками. Диагностика не переполняется при конечных больших входных весах.
Само взвешенное среднее использует прежний territory_index. Выход содержит
cell_qa и district_populations, включая excluded_population и причины.

Остаток population−sum(shares) никогда не перераспределяется. Положительный
необъяснённый остаток — UNALLOCATED_POPULATION, районная полнота unknown;
городской расчёт от этого не меняется. Неизвестная доля сохраняет неизвестную
массу соответствующего района и unknown residual. Подтверждение точности не
выводится из малости разницы.

Для конкретного опубликованного правила cell_nearest_share_floor допустимая
граница остатка одной ячейки — cell_quantum/2 + N_shares*share_quantum.
Нужны precision evidence, значения на заявленной сетке точности и фактические
записи долей. При их наличии остаток в этой границе получает
DOCUMENTED_PRECISION_RESIDUAL и не блокирует полный индекс по сохранённым весам.
Большая разница остаётся неполным распределением. Веса не доводятся до 100%.

### Gap, Deficit и область рейтинга

Каждый UrbanDistrictMetric содержит scope, city_index, districts (index/deficit/
ranking_eligible/reasons), gap и ranking. Полный Gap вычисляется только при
полном ожидаемом наборе применимых районов. Unknown хотя бы одного ожидаемого
района оставляет INCOMPLETE_DISTRICT_GAP. Полностью подтверждённые ZERO_WEIGHT
районы неприменимы и исключаются. При пустом применимом наборе Gap not_applicable;
при одном в полном наборе Gap=0. Пропавшие районы не создают такой ноль.

Deficit_r=max(0,I_city−I_r) доступен при известных полных городе и этом районе;
неизвестность другого района ему не мешает. При unknown городе Deficit unknown.
Неприменимый район сохраняет not_applicable/ZERO_WEIGHT.

Ranking содержит полный scope с expected_district_ids, entries, excluded с
причинами, complete и positions_scope. Допускаются только полные available индексы
одной метрики. Неприменимые районы имеют явное исключение. complete означает, что
все ожидаемые применимые результаты известны. При unknown возвращается
available_subset: позиции относятся только к доступному подмножеству.
Сортировка — по исходному числу по убыванию; равные числа имеют competition ranks
**1,1,3**, ID обеспечивает стабильный порядок при равенстве. Округления для экрана нет.

### Исполнимый синтетический пример

```bash
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_districts.py
```

<!-- executable-example -->
```python
import runpy

demo = runpy.run_path("packages/engine/examples/synthetic_districts.py")
result = demo["run_demo"]()
metric = result.modes[0].overall
assert {d.district_id: d.index.value for d in metric.districts} == {"A": 70, "B": 25, "C": 80}
assert metric.city_index.value == 51.25 and metric.gap.value == 55
assert [d.deficit.value for d in metric.districts] == [0, 26.25, 0]
assert [row.district_id for row in metric.ranking.entries] == ["C", "A", "B"]
partial = demo["run_demo"](unknown_third=True).modes[0].overall
assert {d.district_id: d.index.value for d in partial.districts} == {"A": 70, "B": None, "C": 80}
assert partial.city_index.status == partial.gap.status == "unknown"
assert not partial.ranking.complete and partial.ranking.positions_scope == "available_subset"
```

Вход примера — явно синтетические сохранённые точечные индексы, не рейтинг
Калининграда. Они показывают повторное использование готового UrbanAnalysis,
включая partial верхнего статуса из-за unknown τ.

### Реальные районные входы ML2 .4

Проверена work/ml2@e631929c01792ced9f0a9305ddfce5b312305fa4 через существующие
load_bundle и load_accepted_data, без слияния и копирования исходников соседей.
Набор kaliningrad-urban-2026-11-08.4: 286 ячеек, 216 точек, три района, 262 принятых
объекта всех девяти категорий. Каталог airport полный, остальные partial.
Готовый canonical не содержит районных долей; они приняты узким дополнением
той же immutable версии. Исторические .2 fixtures/отчёты не переписаны.

| Район | Все районные доли, жителей | Excluded-доли, жителей |
| --- | ---: | ---: |
| Центральный | 138478.673 | 193.203 |
| Ленинградский | 193507.398 | 63.411 |
| Московский | 153790.202 | 21.140 |

Город из исходных ячеек: **485776.410**, районные доли: **485776.273**,
остаток **0.137**. Городские excluded: **277.772**; районные excluded также
сохраняют собственное округление. Правило сохранения подтверждено кодом ML2
build_city.py:_cells этого коммита: round(cell.population,3) и floor(share*1000)/1000.
Документированный положительный остаток выявлен в 133 ячейках. Это отдельная QA
диагностика; excluded-потеря населения не объявлена погрешностью округления.

Воспроизводимая проверка (доступ к ML2/contracts — как в walk readiness, с A2_DATA_DIR
на реальный data и явными PYTHONPATH для временных/установленных пакетов):

```bash
uv run --frozen python packages/engine/examples/check_district_bundle.py --bundle kaliningrad-urban-2026-11-08.4
```

Результат записан в examples/kaliningrad_district_readiness.json. В прогоне
использован настоящий canonical + producer и готовый анализ без маршрутных
ответов: индексы/GAP unknown, рейтинг неполный и без численных мест. Положительное
excluded-население каждого района дополнительно препятствует полной готовности.
Внешний Valhalla в этом этапе не запускался; прежние ограничения сохраняются.

### Передача BACKEND и ML2

- **BACKEND — packages/contracts/src/a2_contracts/v2/** и **apps/api/**:
  нужен районный supplement с snapshot/provenance, каталогом parent/level/boundary
  version, cell→point, абсолютными population shares/year/method/grid, evidence,
  expected rosters и полнотой каждого района, precision rule/quantums/evidence.
  Выход адаптировать из UrbanDistrictAnalysis: индексы режимов/категорий/общего,
  город из исходных ячеек, cell residual QA, excluded по районам, Gap/Deficit,
  полный comparison scope, entries/excluded/complete/positions_scope рейтинга.
  Decimal сериализовать строками. Приёмка: 51.25 не среднее районов, 1,1,3,
  partial subset не выдаётся за полные места; unknown count не блокирует H-рейтинг.
- **ML2 — packages/data_pipeline/** и immutable district supplement:
  сохранить .4 и source rule, абсолютные веса, связи точек с исходными ячейками,
  year/method/grid/boundary versions и precision evidence. Не нормировать остаток
  0.137 и не скрывать excluded 277.772. Для реальных полных индексов нужно устранить
  excluded-потерю либо сохранять неизвестность затронутых районов. Приёмка:
  контрольные суммы выше, canonical/supplement одной версии, partial каталоги не full.

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

Проверки переноса находятся в tests/test_public_api.py и входят в общий запуск
pytest: сохранение 87 прежних экспортов/сигнатур и совместимости вызовов, тождественность каноническим объектам,
frozen dataclass, импорты предметных пакетов в разных порядках, все три загрузчика
из другого cwd, явный путь вне checkout, прежние ошибки файла/JSON и направление
зависимостей через shared и собственные модули. Девять рабочих блоков README
(все восемь прежних и новый районный пример) с меткой
executable-example можно выполнить отдельно из корня checkout:

```bash
PYTHONPATH=packages/engine/src uv run --frozen python - <<'PY'
from pathlib import Path
import re

text = Path("packages/engine/README.md").read_text()
examples = re.findall(r"<!-- executable-example -->\s*```python\n(.*?)```", text, re.S)
assert len(examples) == 9
for number, source in enumerate(examples, 1):
    exec(compile(source, f"README example {number}", "exec"), {})
    print(f"README example {number}: PASS")
PY
```

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
