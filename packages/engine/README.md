# Математическое ядро A2

Чистые расчёты ML1 по `docs/FORMULAS.md` и `config/product.json`: городские
пассажирские индексы, анализ межгородских пассажирских цепочек по расписаниям, городские/межгородские
грузовая инфраструктура и поиск полной доставки, отдельные трубопроводные связи/загрузка,
сравнение готовых результатов, городской τ, районные
агрегаты, поиск прямой поездки/последнего возврата по нормализованному расписанию,
static car replay, типичный/живой/исторический traffic evaluator, направленные OD-срезы для τ
и самостоятельное построение межгородского эталона A на принятом графе.
Времена, население и подтверждения входных условий поступают от вызывающего слоя.
Scenario Engine выполняет immutable изменения расписания и полный повторный расчёт.
Загрузки внешних расписаний, сетки, построения реальных OD-матриц, HTTP и LLM здесь нет.

Независимые находки, измерения и границы проверки: [AUDIT.md](AUDIT.md).
Завершение критических oracle/stress-проверок: **922 tests passed**,
**READY WITH EXTERNAL DEPENDENCIES**; установка пакета, DTO supplements,
accepted data/executor и совместный E2E остаются условиями интеграции.
Основной сценарный API и handoff описаны в разделе «Scenario Engine v3».

## Структура и зависимости

```text
a2_engine/
  __init__.py                 # 268 явных публичных экспортов (прежние 237 сохранены)
  shared/                     # конфигурация, MetricResult, общая экспонента
    config.py                 # расположение и config/product.json path сохранены
    results.py
    scoring.py
  urban/
    __init__.py                # предметные реэкспорты
    models.py                  # общий контекст, точки, объекты, legs, coverage
    indices.py                 # H и чистые индексы
    journeys.py                # проверка городских временных цепочек
    selection.py               # выбор физического объекта и tie-breaking
    validation.py              # общая проверка точек для analysis и tau
    analysis/                  # models.py, validation.py, calculation.py
    valhalla/                  # models.py, requests.py, responses.py, codec.py
    walk/                      # models.py, inputs.py, analysis.py
    car/                       # models.py, analysis.py, traffic.py
    transit/                   # models.py, journeys.py, search.py, last_return.py
    tau/                       # models.py, calendar.py, calculation.py
    od/                        # models.py, planning.py, collection.py, traffic.py, _planning.py
    districts/                 # models.py, inputs.py, calculation.py
    district_models.py         # совместимость: только aliases
    district_inputs.py         # совместимость: только aliases
    tau_models.py              # совместимость: models + city_zone
    last_return.py             # совместимость: transit.last_return/models
  routing/
    reference/                 # models.py, calibration.py, search.py: самостоятельный A
    road.py                    # независимый static auto/foot JSON boundary
    traffic/                   # models.py, profiles.py, evaluation.py: чистый traffic слой
    timetable_models.py        # нормализованные исполнения и scope
    timetable.py               # прежний городской поиск с бюджетом ожидания
    scheduled.py               # first-departure/mode frontier без городского wait budget
  intercity/analysis/           # модели, physical aliases, выбор индекса и частота
  intercity/reference.py       # проверенный адаптер rich result → прежний ReferenceTime
  intercity/passenger.py
  freight/urban.py
  freight/intercity.py
  freight/models.py            # профиль, vehicle/temperature, bound compatibility
  freight/truck.py             # отдельный принятый грузовой routing input/proof
  freight/urban_analysis.py    # roster, nearest compatible nodes и scalar aggregate
  freight/delivery/           # models.py + search.py: delivery completion
  pipeline/analysis.py        # только accepted end-to-end connections
  pipeline/utilization.py
  comparison/deltas.py
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

### История первого выделения доменных пакетов

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

При первом выделении доменов контракт `from a2_engine import ...` сохранил прежние 37 имён,
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

История расширений: городской анализ добавил 23 экспорта, τ — 12, walk — 15,
районы — 17, расписание/возврат — 16 (на том этапе 120), static car/OD — 21.
На структурной базе f4c8ae4 API содержал 141 экспорт; snapshot защищает сигнатуры и поля
этих 141. Traffic-этап добавил ещё 27 (теперь 168). Ранее snapshot охватывал 104 сигнатуры; расширение destination прошлого этапа описано ниже. Предметные реализации могут импортировать свои внутренние
модули и shared; urban также использует независимый routing. Shared и routing не
зависят от предметных пакетов.

## Структурный этап urban (09.11.2026)

Исходная база: `cd7572ebb0a2d1f054c974d304ab37a0bf9a81f1`. Только размещение кода
и явные импорты; продуктовых функций и изменений алгоритмов здесь нет.

| Старое расположение в urban | Каноническое расположение | Куда заходить |
| --- | --- | --- |
| analysis.py | analysis/models.py, validation.py, calculation.py | результаты, проверки кандидатов, сборка анализа |
| valhalla.py | valhalla/models.py, requests.py, responses.py, codec.py | pedestrian JSON boundary и codec |
| walk.py | walk/models.py, inputs.py, analysis.py | AcceptedData projection, план и replay |
| car.py | car/models.py, analysis.py | static car inputs, план и сборка |
| transit.py | transit/models.py, journeys.py, search.py | timetable projection и прямой поиск |
| last_return.py | transit/models.py, last_return.py | последний возврат |
| tau.py + tau_models.py | tau/models.py, calendar.py, calculation.py | OD/week модели, IANA calendar, точное τ |
| od.py | od/models.py, planning.py, collection.py | задачи/батчи, внешняя routing boundary, приём результатов |
| districts.py + district_models.py + district_inputs.py | districts/models.py, inputs.py, calculation.py | исходные доли/покрытие, supplement, районные агрегаты |

Прежние одноимённые module paths теперь являются пакетами с явными реэкспортами:
`urban.analysis`, `urban.valhalla`, `urban.walk`, `urban.car`, `urban.transit`,
`urban.tau`, `urban.od`, `urban.districts`. Старого .py рядом с пакетом нет.
Четыре файла совместимости в корне urban содержат только aliases:
`district_models`, `district_inputs`, `tau_models`, `last_return`. Они позволяют
существующим потребителям продолжать прямые импорты. Проверяется `old is new`,
включая используемые private helpers, без sys.modules и подмены __module__.

`_check_points` определён только в `urban/validation.py`; analysis и τ импортируют
его напрямую. `_local_instant` принадлежит `tau/calendar.py`, OD больше не
импортирует его из calculation или фасада. `check_urban_inputs`, `path_legs` и
identity JSON helper принадлежат `transit/journeys.py` и общие для forward/return.
Valhalla codec остаётся внутри urban, общий routing не начинает зависеть от urban.

Реализации импортируют конкретные модули абсолютными путями; __init__.py — только
точки экспорта. Shared не импортирует urban/routing, routing не импортирует urban,
intercity/freight не используют urban. Функции не стали classmethod, dataclass
fields/defaults/frozen не менялись, slots не добавлялись. Config.py не перемещён.
Канонический __module__ закономерно изменился у перенесённых определений; старые
пути по-прежнему возвращают тот же объект. Это не обещание сохранить старую строку
__module__, а совместимость imports и сигнатур.

До переноса сняты `tests/fixtures/public_api_structure.json` (141 сигнатура,
порядок экспортов, frozen и fingerprint всех полей/defaults) и inventory старых
импортов. Контроли результатов хранят 14 компактных SHA256 вместо больших дампов:
walk, районы, transit/return, car, payload, OD tasks/observations, week, τ,
threshold и partial/unknown варианты. Ожидания получены из исходного cd7572e.
Сравниваются полные нормализованные результаты: числа, причины, доказательства,
геометрия, порядок выбранных объектов и все вложенные identities/hashes.

В tests/test_public_api.py CANONICAL_MODULES указывает новые настоящие определения;
полный snapshot дополняет прежние signature guards. Отдельные процессы проверяют
старые/новые пути в трёх порядках импорта и загрузчики из другого cwd. Прежний
external config-path test сохранён. Tests/fixtures/examples не перемещались.

Итог структурного этапа: 596 passed; Ruff/format src/tests и всех examples,
strict mypy src/tests (77 файлов) и src/tests/examples (84 файла) прошли.
Для mypy примеров указывайте source files вместе с examples: упаковка package=false
не менялась. Отдельный mypy только examples не подтверждает импорт typed sources.

Команды ниже остаются прежними; 10 executable README controls и оба car/OD replay
исполняются теми же способами. Реальные HTTP/Valhalla данные и новые traffic
profiles этим переносом не добавлены.

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
    selection,
    config=config,
)
p2 = point_index(
    {"school": category_index(0, h), "airport": category_index(0, h)},
    selection,
    config=config,
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
проверяется против конфигурации. Scalar-функции сами не строят эталон;
новые build_reference и reference_time_from_result описаны в разделе ниже.

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
    PassengerChain,
    ReferenceTime,
    intercity_car_index,
    intercity_passenger_index,
    load_passenger_config,
)

config = load_passenger_config()
reference = ReferenceTime("test-ref", 3600, config.reference_profile, "full", True)
result = intercity_passenger_index(
    PassengerChain("test-chain", 7200, 43200),
    reference,
    config=config,
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
    CargoScope,
    intercity_cargo_index,
    load_freight_config,
    select_h_seconds,
    urban_cargo_site_index,
    urban_cargo_territory_index,
)

config = load_freight_config()
h = select_h_seconds(100, bands=config.area_h_bands).value
scope = CargoScope("port", "outbound", "test-nonfood")
sites = [
    urban_cargo_site_index("test-site-a", 0, h, scope=scope, compatibility_confirmed=True),
    urban_cargo_site_index(
        "test-site-b", None, h, scope=scope, compatibility_confirmed=True, confirmed_no_route=True
    ),
]
assert urban_cargo_territory_index(sites, scope=scope).value == 50
delivery = intercity_cargo_index(
    cargo_profile_id="test-nonfood",
    urgency="urgent",
    initial_wait_seconds=3600,
    movement_seconds=80000,
    handling_seconds=1800,
    transfer_wait_seconds=1000,
    compatibility_confirmed=True,
    config=config,
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
    120,
    100,
    volume_basis=basis,
    capacity_basis=basis,
    connection_confirmed=True,
    config=load_freight_config(),
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
assert (
    delta_index(
        MetricResult(70, "available"),
        MetricResult(55, "available"),
        comparison=comparison,
    ).value
    == -15
)
assert (
    time_saved_seconds(
        TravelTime(1800, True),
        TravelTime(2100, True),
        comparison=comparison,
    ).value
    == -300
)
new_connection = time_saved_seconds(
    TravelTime(None, False),
    TravelTime(600, True),
    comparison=comparison,
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
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLeg,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    UrbanVersions,
    analyze_urban_candidates,
    load_urban_config,
    load_urban_journey_rules,
)

config, rules = load_urban_config(), load_urban_journey_rules()
versions = UrbanVersions(
    "synthetic-snapshot",
    "synthetic-graph",
    "synthetic-calendar",
    "synthetic-population",
    "synthetic-geometry",
    config.methodology_version,
    "synthetic-config-hash",
    "synthetic-traffic",
)
context = UrbanContext(
    "synthetic-city",
    "synthetic-city",
    datetime(2026, 11, 8, 8, tzinfo=UTC),
    "static",
    versions,
    "synthetic-search-options",
)
area = UrbanArea(context.city_id, 100, versions.geometry_version, ("synthetic-area-evidence",))
points = [
    UrbanPoint(
        f"p{i}",
        f"cell{i}",
        f"p{i}",
        f"p{i}",
        0,
        context.territory_id,
        population,
        2026,
        "synthetic-estimate",
        ("synthetic-population-evidence",),
    )
    for i, population in [(1, 90), (2, 10)]
]
destination = UrbanDestination(
    "school-record",
    "physical-school",
    frozenset({"school"}),
    "school-node",
    context.city_id,
    True,
    True,
    ("synthetic-registry-evidence",),
)
candidates = []
for point, seconds in zip(points, (1200, 2400), strict=True):
    start, end = (
        context.requested_departure_at,
        context.requested_departure_at + timedelta(seconds=seconds),
    )
    leg = UrbanLeg(
        "walk",
        "walk",
        point.origin_node_id,
        destination.access_node_id,
        start,
        end,
        seconds,
        None,
        ("synthetic-route-evidence",),
    )
    candidates.append(
        UrbanCandidate(
            f"route-{point.point_id}",
            point.point_id,
            destination.destination_id,
            "school",
            "walk",
            context,
            point.origin_node_id,
            destination.access_node_id,
            start,
            end,
            (leg,),
            ("synthetic-route-evidence",),
        )
    )
coverage = [
    UrbanCoverage(
        UrbanSearchScope(context, point.point_id, "school", "walk", rules.max_total_wait_seconds),
        True,
        True,
        ("synthetic-search-proof",),
        frozenset({destination.destination_id}),
    )
    for point in points
]
population_coverage = UrbanPopulationCoverage(
    context,
    True,
    0,
    ("synthetic-sampling-proof",),
    frozenset(p.point_id for p in points),
)
tau = UrbanThreshold(
    context.city_id,
    "walk",
    versions,
    "static",
    context.search_parameters_id,
    1200,
    ("synthetic-tau-proof",),
)
result = analyze_urban_candidates(
    context,
    points,
    [destination],
    candidates,
    coverage,
    area=area,
    selected_categories={"school": "medium"},
    modes=("walk",),
    population_coverage=population_coverage,
    config=config,
    rules=rules,
    thresholds=(tau,),
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

HTTP-исполнение маршрутов, построение реальных OD-матриц, эталон A и graph overlay
сценариев остаются следующими этапами. Поиск последнего возврата по нормализованному
расписанию реализован отдельным вычислительным вызовом ниже.

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
    UrbanContext,
    UrbanODPair,
    UrbanODScope,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanTauDay,
    UrbanTauSlice,
    UrbanTauWeek,
    UrbanVersions,
    calculate_urban_tau,
    load_urban_tau_rules,
)

rules = load_urban_tau_rules()
reference_at = datetime(2026, 11, 8, 12, tzinfo=UTC)
versions = UrbanVersions(
    "synthetic-snapshot",
    "synthetic-graph",
    "synthetic-calendar",
    "synthetic-population",
    "synthetic-geometry",
    rules.methodology_version,
    "synthetic-config-hash",
    "synthetic-traffic",
)
context = UrbanContext(
    "synthetic-city", "synthetic-city", reference_at, "static", versions, "synthetic-search-options"
)
points = [
    UrbanPoint(
        f"p{i}",
        f"cell{i}",
        f"p{i}",
        f"p{i}",
        0,
        context.city_id,
        population,
        2026,
        "synthetic-estimate",
        ("synthetic-population-evidence",),
    )
    for i, population in [(1, 90), (2, 10)]
]
ids = frozenset(p.point_id for p in points)
population = UrbanPopulationCoverage(context, True, 0, ("synthetic-population-proof",), ids)
scope = UrbanODScope(
    context.city_id,
    "walk",
    versions,
    "static",
    context.search_parameters_id,
    "Europe/Kaliningrad",
    ids,
    rules.max_total_wait_seconds,
)
start = date(2026, 10, 26)
calendar = tuple(
    UrbanTauDay(start + timedelta(days=i), True, False, True, True, ("synthetic-calendar-proof",))
    for i in range(7)
)
pairs = (
    UrbanODPair("p1", "p2", "reachable", 600, True, True, ("synthetic-minimum-proof",)),
    UrbanODPair("p2", "p1", "reachable", 1800, True, True, ("synthetic-minimum-proof",)),
)
slices = tuple(
    UrbanTauSlice(
        scope,
        start + timedelta(days=i),
        clock,
        datetime.combine(start + timedelta(days=i), clock, ZoneInfo(scope.timezone)),
        pairs,
        True,
        ("synthetic-matrix-proof",),
    )
    for i in range(rules.tau_days)
    for clock in rules.tau_times
)
baseline = UrbanTauWeek(
    "synthetic-week-2026-10-26",
    scope,
    start,
    calendar,
    slices,
    ("synthetic-week-proof",),
    False,
    (),
)
tau_result = calculate_urban_tau(
    context,
    points,
    population,
    (baseline,),
    passenger_mode="walk",
    timezone=scope.timezone,
    reference_at=reference_at,
    rules=rules,
)
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
assert [leg.duration_seconds for leg in result.analysis.journeys[0].candidate.legs] == [
    60,
    1620,
    120,
]
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
    analysis,
    accepted.model_dump(mode="json"),
    bundle.model_dump(mode="json"),
    catalog_version=catalog_version,
    distribution_version=distribution_version,
    complete_district_ids=confirmed_district_ids,
    original_cells_complete=True,
    evidence_refs=coverage_evidence,
    precision=accepted_precision,
)
result = analyze_urban_districts(
    analysis,
    prepared.original_cells,
    prepared.district_catalog,
    prepared.population_shares,
    prepared.coverage,
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

## Точный поиск по событиям и последний городской возврат

Самостоятельный расчёт по **нормализованным** расписаниям: routing/timetable_models.py
и routing/timetable.py не зависят от urban, HTTP, GTFS loader или OTP. urban/transit.py
проверяет городской контекст и создаёт прежние UrbanCandidate; urban/last_return.py
возвращает отдельную обратную цепочку. В journeys.py общие строгие проверки вынесены
в summarize_urban_legs; прежний validate_urban_journey и все его проверки сохранены.
104 прежние публичные сигнатуры зафиксированы; добавлено 16 экспортов, всего 120.
Архитектурный тест разрешает urban→routing, но запрещает routing→urban.

```python
search_urban_transit(
    network: UrbanTransitNetwork, point: UrbanPoint, destination: UrbanDestination, *,
    category: str, rules: UrbanJourneyRules,
    passenger_mode: Literal["public_transport", "walk"] = "public_transport",
    resource_limit: int | None = None,
) -> UrbanTransitResult

search_urban_last_return(
    network: UrbanTransitNetwork, point: UrbanPoint, selected_destination: UrbanDestination, *,
    local_day: date, rules: UrbanJourneyRules, lower_leave_at: datetime | None = None,
    passenger_mode: Literal["public_transport", "walk"] = "public_transport",
    resource_limit: int | None = None,
) -> UrbanLastReturnResult
```

В прямом поиске t0 — network.context.requested_departure_at; оно не переносится
к отправлению автобуса. rules загружается load_urban_journey_rules из product.json:
сейчас максимальное **суммарное ожидание** 14400с. Полное время и движение этим
числом не ограничены. Public_transport включает подтверждённые чисто пешие пути.
Производные H/τ/индексы/count/районы не пересчитываются новым алгоритмом.

### Нормализованные входы и идентичность

| Тип | Содержание |
| --- | --- |
| TimetableEvent | sequence, node_id, aware arrival_at/departure_at, pickup/dropoff, evidence_refs |
| TimetableRun | snapshot_id, source_id, trip_id конкретного исполнения, service_date, IANA timezone, transport_type, events, active (bool/None), calendar_evidence_refs |
| TimetableFootpath | path_id, направленные from/to_node_id, исходные seconds либо None, confirmed, evidence_refs |
| TimetableTransfer | rule_id, направленные from/to_node_id, minimum_seconds, allowed (bool/None), evidence_refs, optional from_run_id/to_run_id; semantics=alight_to_departure |
| TimetableScope | snapshot/graph/calendar/timetable/footpath/transfer versions, IANA timezone, aware start_at/end_at, node_ids, отдельные schedule/calendar/footpaths/transfers/continuations completeness, evidence_refs, limitations |
| Timetable | scope, runs, footpaths, transfers; неизменяемые и стабильно упорядоченные записи |
| UrbanTransitNetwork | исходный UrbanContext, Timetable, evidence_refs; snapshot/graph/calendar должны совпасть |

run_id — устойчивый SHA256 комбинации snapshot/source/trip identity/service date,
не один template trip_id. Два исполнения шаблона в разные даты не склеиваются.
Для частотного источника будущий адаптер обязан дать отдельную trip identity каждому
конкретному старту. Event идентифицируется через run_id и sequence. Sequence может
иметь пропуски; повторный node_id разрешён. Конфликтующие IDs и обратные интервалы
отклоняются. Все исходные event timestamps явно переданы; внутри нормализуются в UTC.

Каждое service date уже должно учитывать исключения. active=False с evidence
исключает исполнение; None или отсутствие календарных доказательств — неизвестность,
а не воскресное/праздничное расписание по догадке. События прошлых сервисных суток
после полуночи не удаляются. Фильтровать такие исполнения только по service_date нельзя.

Преобразование service-day seconds в этом этапе **не реализовано**. Будущий GTFS
адаптер должен следовать [официальной семантике времени](https://gtfs.org/documentation/schedule/reference/#field-types):
сервисный отсчёт определяется через полдень минус 12 часов, значения могут превышать
24 часа, а исключения календаря применяются явно. Нельзя использовать `% 86400`
или безусловно прибавлять секунды к местной полуночи в DST-день. Engine принимает
уже развёрнутые aware instants, не угадывает fold и не восстанавливает пропущенные события.

Footpath — подтверждённое физическое движение с известным временем, не минимальный
интервал пересадки. Обратная связь передаётся отдельно. Положительное время,
исчезающее в timedelta, отклоняется; допустимые дробные seconds нормализуются только
на границе timestamps. TimetableStep.duration_seconds берётся из UTC-интервала,
original_seconds и footpath_id сохраняют происхождение. Геометрия не синтезируется.

Физические endpoints, отличные от принятых access nodes, должны быть граничными
узлами пеших подходов/выходов, а не остановками рейса. Исходящий физический подход
связывает origin с его access, конечный участок — destination access с physical
endpoint. Противоречащие связи отклоняются PHYSICAL_ACCESS_LINK_MISMATCH; новый
адаптер не обходит прежние проверки подходов UrbanPoint/UrbanDestination.

### Состояние поиска и корректность отсечения

solve_timetable — событийный label search с очередью по абсолютному времени.
Off-vehicle состояние: node, время, накопленное ожидание в целых микросекундах и
последнее событие высадки (run/sequence, следовательно его время и транспорт).
On-vehicle состояние: конкретный run и индекс события. В пути сохраняются реальные
steps, исходные walk seconds, первые посадки и source transport types.

Две off-vehicle метки сравниваются **только** при одинаковом узле и событии высадки;
начальные метки также отделены от пересадочных. On-vehicle сравнение — лишь внутри
одного исполнения и события. Это обеспечивает одинаковые дальнейшие возможности
и якорь минимального интервала. Достаточное доминирование:

`t1 <= t2 AND w1 + (t2 - t1) <= w2`.

Первая метка может воспроизвести возможности второй, оплатив дополнительное ожидание;
при time-independent walk она придёт к следующему событию не позже, с не большим
ожиданием. Минимальные пересадки имеют тот же якорь высадки. Внутри одного события
рейса время совпадает, достаточно меньшего ожидания. Обычное «раньше и меньше уже
ждал» не используется. Контрпример A/B даёт B: 5+235=240 минут; A требует 245.
Отдельно проверен такой же эффект для двух пеших подходов к одному совместимому состоянию.

Посадка проверяет pickup, высадка — dropoff. Продолжение того же исполнения идёт
по следующему sequence, включает промежуточную стоянку в ride и не требует новых
pickup/dropoff. Возвращение в тот же node не означает конец/начало другого рейса.
transfers считает фактические смены run между посадками, а не ride legs.
Bus/tram/metro/trolleybus отображаются в UrbanLeg.mode=public_transport; rail/water
сохраняют поддерживаемый mode. Исходный transport_type остаётся в TimetableRun/Step.
Расписанные времена автобуса не умножаются повторно на дорожный traffic factor.

При пересадке minimum_seconds — минимальный интервал **от высадки до отправления**,
уже включающий пеший переход. После фактического walk оставшееся время до отправления
считается wait_transfer; минимум не прибавляется ещё раз. Даже при одном node нужны
явные применимые правила (минимум может быть 0). Нет правила/времени/доказательств —
неизвестная связь; allowed=False с evidence — запрет. Из применимых правил выбирается
наиболее специфичное по from/to run; равная конфликтующая специфичность — ошибка.

Область конечна: [scope.start_at, scope.end_at], прибытие на end_at разрешено.
Никакого ограничения двух пересадок, первых N рейсов или четырёх часов всей поездки
нет. Нулевые циклы отсекаются эквивалентными метками. Положительные реальные пешие
циклы не заменяются фиктивным ожиданием и не удаляются небезопасным правилом;
при микросекундном времени и конечном горизонте множество состояний конечно, но
может быть большим. resource_limit — явный общий лимит расширений, по умолчанию None.
При его достижении RESOURCE_LIMIT_REACHED, interrupted=True и unknown; best-known
сохраняется. Первая извлечённая допустимая цель доказывает earliest arrival в области,
так как все временные переходы неотрицательны. Геометрические эвристики отсутствуют.

### Последний выход от выбранного объекта

Объект передаётся конкретным UrbanDestination из прямого результата/анализа;
другой объект не выбирается. Оптимизируется leave_at, не время автобуса.
Для каждого pickup события выбранного **местного календарного** дня вычисляется
кратчайший подтверждённый пеший подход от physical объекта. Он даёт позднейший
выход `first_departure - walk_duration` без первоначального ожидания. Любой более
длинный подход к тому же событию выйдет раньше и не улучшит оставшийся бюджет.
Для этой первой посадки общий solver проверяет все продолжения с требованием хотя
бы одной посадки. Перебираются все такие события, затем выбирается максимальный выход;
при равенстве — более раннее прибытие и стабильный порядок.

Пересадки/прибытие после полуночи разрешены. Первая посадка в 00:05 следующего дня
не подходит, даже если service_date предыдущая и исходное расписание писало 24:05.
День проверяется через UTC instant → scope.timezone. Границы дня проверяются без
выбора неоднозначной/несуществующей полуночи по умолчанию. Полнота требует покрытия
всего первого дня и необходимых продолжений; обрезка в 00:00/рейс за end_at оставляет unknown.

lower_leave_at — явная aware нижняя граница, дополнительно действует start_at области.
Нет скрытых часов работы, длительности визита и требования прибыть домой до полуночи.
Pure walk не имеет последнего рейса: для walk mode not_applicable; в public_transport
наличие пешей альтернативы не отменяет поиск рейсов. Если транспортный возврат доказанно
отсутствует, но пеший путь есть, WALK_ONLY_RETURN/not_applicable. Если нет обоих —
confirmed_unreachable с proven=True и причиной NO_FEASIBLE_RETURN_WITHIN_SCOPE;
подтверждённое время при этом отсутствует. Неполнота не позволяет сделать такой вывод.

Возврат хранится в UrbanReturnJourney, не в обратном UrbanCandidate. Legs проходят
те же строгие проверки, но сохраняют свои physical object→point boundaries.
Неизвестный возврат никак не меняет уже рассчитанный дневной H-индекс.

### Результаты и доказательства полноты

TimetableSearchResult сохраняет timetable/scope, endpoints/t0, state,
best_known TimetablePath, proven, reasons/evidence, expanded_states/interrupted,
точные max_wait_seconds/allow_transit/require_transit/first_boarding/resource_limit.
Path содержит шаги и посадки; пустой список рейсов сам по себе не подтверждает отсутствие.
Полнота — внешняя принятая аттестация именно этой области, версии, календаря, пеших
связей и пересадок. Неизвестные записи дополнительно снимают доказательство даже при
ошибочно выставленном общем флаге. Допускается консервативный unknown от непроверенных
входов всей области; он не превращается в no-route.

UrbanTransitResult.best_known — проверенный UrbanJourneyTiming (или None), .seconds
available только при доказанном минимуме; при unknown найденный кандидат сохраняется.
.search_complete применим также к подтверждённому отсутствию **в принятой области**,
не утверждает отсутствие связи на всех датах/за её пределами. Ограничения области
должны входить в согласованные search parameters и coverage. Нельзя вручную объявить
укороченную выгрузку полным городским поиском. Полнота институционального каталога
отдельна: UrbanCoverage можно сделать search_complete только после обработки всех
требуемых объектов категории с доказанными результатами. Одной пары недостаточно.

UrbanLastReturnResult сохраняет selected point/destination, local_day/timezone,
network/context, исходные rules/lower_leave_at/resource_limit, status/state/proven,
best_known, reasons/evidence и ресурсную
диагностику. leave_at/first_departure_at/arrival_at доступны напрямую только при
status=available. При unknown времена найденной цепочки находятся исключительно
в best_known. Цепочка содержит totals ожидания/ходьбы/движения и source_path с версиями.

### Самостоятельный пример: только синтетическое расписание

```bash
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_transit.py
```

Исходные данные: O→S пешком 5мин, автобус S 08:12→T 08:27, выход T→D 5мин,
начало 08:00. Возвраты: D→A 20мин, A 23:55→G 00:15; D→B 5мин,
B 23:50→G 00:10; G→O 5мин. Все календарные/coverage evidence в примере тестовые.

<!-- executable-example -->
```python
import runpy
from zoneinfo import ZoneInfo

demo = runpy.run_path("packages/engine/examples/synthetic_transit.py")
outbound, analysis, last = demo["run_demo"]()
assert outbound.seconds.value == 1920
assert outbound.best_known is not None
assert (
    outbound.best_known.walking_seconds,
    outbound.best_known.riding_seconds,
    outbound.best_known.total_wait_seconds,
) == (600, 900, 420)
assert outbound.best_known.waiting_share.value == 0.21875
assert analysis.modes[0].index.value is not None
assert analysis.modes[0].points[0].categories[0].count.reason_code == "TAU_UNAVAILABLE"
zone = ZoneInfo("Europe/Kaliningrad")
assert (
    last.leave_at is not None
    and last.first_departure_at is not None
    and last.arrival_at is not None
)
assert last.leave_at.astimezone(zone).isoformat() == "2026-11-08T23:45:00+02:00"
assert last.first_departure_at.astimezone(zone).isoformat() == "2026-11-08T23:50:00+02:00"
assert last.arrival_at.astimezone(zone).isoformat() == "2026-11-09T00:15:00+02:00"
```

При H=1800с индекс примера 47.74208019552083. Пешие 40мин проигрывают прямой цепочке,
28мин выигрывают. Это не настоящий расчёт Калининграда. Корректность отсечений
дополнительно проверена независимым DFS без доминирования на 80 маленьких конечных
расписаниях; это тестовый oracle, не production search.

### Следующая общая интеграция

- **ML2 / normalizer**: источник с явным snapshot, конкретные исполнения и активные
  даты после исключений, IANA timezone и aware stop events с pickup/dropoff/sequence,
  mode, evidence, направленные реальные подходы/выходы/переходы с временами. Не
  восстанавливать отсутствующие события и не переносить holiday на weekday шаблон.
  Подготовить scope с графом, календарём, footpath/transfer/timetable versions,
  границами и доказательством полноты, включая продолжения после полуночи.
- **BACKEND, packages/contracts/src/a2_contracts/v2/**: ScheduledService сначала
  развернуть в отдельный TimetableRun каждой активной даты; исходный service_id
  сохранить как source provenance. stop sequence переносится без перенумерации.
  Canonical Transfer.seconds нельзя автоматически назвать одновременно walk time
  и минимумом: нужны отдельный TimetableFootpath и явный alight_to_departure rule
  с допустимостью, направлением и optional run specificity. Городской контекст
  связывается UrbanTransitNetwork без подмены graph/snapshot/config/traffic versions.
- **BACKEND result adapter**: прямой best-known candidate передаётся в прежний анализ
  с отдельной route/catalog coverage; unknown minimum не становится доступным индексом.
  Canonical LastReturn сейчас не хранит best-known и полноту/область при unknown:
  нужны отдельные best_known_journey_id, proven/completeness/scope или сохранённый
  внутренний артефакт. Нельзя помещать best-known времена в поля подтверждённого
  LastReturn. Ни fake dependency jobs, ни HTTP в engine не добавлены.
- **Worker/DEVOPS**: передавать явный resource_limit и сохранять interrupted как
  частичный результат, не no-route. GTFS ingest, HTTP, OTP, общее подключение DTO,
  реальные расписания и прогоны остаются отдельной интеграцией после вычислительного
  этапа. Манифесты/lock/глобальная конфигурация этим изменением не затрагиваются.

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
pytest: сохранение 104 прежних экспортов/сигнатур и совместимости вызовов, тождественность каноническим объектам,
frozen dataclass, импорты предметных пакетов в разных порядках, все три загрузчика
из другого cwd, явный путь вне checkout, прежние ошибки файла/JSON и направление
зависимостей через shared и собственные модули. Десять рабочих блоков README
(все девять прежних и новый transit/return пример) с меткой
executable-example можно выполнить отдельно из корня checkout:

```bash
PYTHONPATH=packages/engine/src uv run --frozen python - <<'PY'
from pathlib import Path
import re

text = Path("packages/engine/README.md").read_text()
examples = re.findall(r"<!-- executable-example -->\s*```python\n(.*?)```", text, re.S)
assert len(examples) == 10
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

## Static car и направленные OD — функциональный этап до структурного переноса (09.11.2026)

Новый путь не изменяет pedestrian `ValhallaBuild` и walk API. Основные вызовы
экспортированы из `a2_engine`:

- `RoadBuild(snapshot_id, source_graph_version, build_id, graph_version,
  profile_version, costing, options_json, evidence_refs, engine_version="3.5.1",
  traffic_mode="static")`: отдельный профиль auto или foot. Его hash включает
  build/source/profile/options/evidence. Для auto обязательны
  `speed_types=["freeflow"]`, запреты игнорировать access/restrictions/closures.
  Отсутствующие параметры используют закреплённые defaults **3.5.1**;
  executor обязан включить любые server overrides в принятый options_json.
- `RoadEndpoint(build, physical_node_id, access_node_id, coordinate,
  correlation_id, direction, seconds, evidence_refs)`: подтверждённая направленная
  пешая связь entry либо exit и принятая фактическая correlation. Coordinate —
  точка доступа на графе; физическая граница идентифицирована physical_node_id.
  Evidence должно подтверждать направление, длительность (включая явный ноль),
  автомобильную применимость и совпадение correlation с этой точкой доступа.
  Pedestrian connector не становится автомобильным по совпадению координат.
- `plan_car_routes(inputs: CarInputs, *, categories) -> CarPlan` обходит весь
  переданный применимый каталог. Пропущенный connector даёт issue, не маршрут.
- `normalize_road_response(request, response) -> RoadResult` проверяет exact
  request/payload/profile, echo и отдельные actual endpoint attestations.
  Request hash связывает физические/access IDs, correlation IDs, departure,
  направление, profile/build/source graph и search parameters.
- `analyze_car_responses(plan, responses, *, selected_categories, config, rules,
  thresholds=()) -> CarAnalysisResult` вызывает существующий urban analyzer.

`CarInputs(context, points, destinations, entries, exits, area, population,
 catalogs, timezone)` принимает **уже нормализованные принятые входы**. Entries и
exits — tuple `(entity_id, RoadEndpoint)`; catalogs — tuple
`(category, complete, evidence_refs)`. UrbanPoint.access_node_id и
UrbanDestination.access_node_id должны обозначать именно автомобильные доступы.
Это новый mode-specific input, не изменение pedestrian ML2-проекции.

Полная цепочка: physical origin → walk entry → auto ride → walk exit → physical
object. Network departure = physical departure + entry. Парковка/посадка не
добавляются. Raw секунды остаются в endpoint/result; timestamps округляются один
раз при создании каждого интервала, duration равен фактической разности. Положительные
значения ниже microsecond resolution отклоняются. Геометрия полной цепочки
остаётся unknown: сетевой shape не выдаётся за геометрию внешних подходов.

### Проверенная семантика Valhalla 3.5.1

Прочитаны исходники именно tag 3.5.1:
[SpeedMask_Parse](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/dynamiccost.cc),
[AutoCost](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/autocost.cc),
[route algorithm selection](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/route_action.cc),
[matrix selection](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/matrix_action.cc),
[time-distance matrix](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/timedistancematrix.cc),
[datetime parsing](https://github.com/valhalla/valhalla/blob/3.5.1/src/baldr/datetime.cc).

Auto elapsed secs и generalized cost различаются. Defaults speed mask допускают
traffic; запрос явно задаёт freeflow и depart-at type=1, prioritize_bidirectional=false.
Static не убирает временные access/turn ограничения. Исполнитель должен проверить
тайл timezone, closure snapshot и фактически использованный timedep алгоритм:
route selection может откатиться при превышении max_timedep_distance.

3.5.1 разбирает date_time с точностью до минуты (`%FT%R`). Поэтому subminute
network entry и неоднозначный fold не округляются: planner фиксирует issue,
OD executor оставляет такую пару unknown. Для поддержки точных субминутных
ограничений нужен отдельно проверенный protocol/executor, не silent rounding.

Matrix алгоритм и поведение time зависят от costing/config/размера и выбора
направления. Этот этап **не принимает sources_to_targets**. Используются парные
`/route`: batch — только транспортная упаковка независимых запросов, у каждой
строки своё network departure. Нет двусмысленного mapping row/column и arrive-by.

HTTP 200, summary.time, shortest/alternates не доказывают minimum elapsed time.
`RoadResponse.minimum_time_evidence_refs` и `search_complete` — независимые
attestations исполнителя для **точного** request/profile/boundaries/time и всех
ограничений. При их отсутствии найденный путь остаётся best-known, индекс unknown.
Ошибка 442 становится confirmed_unreachable только с исчерпывающим доказательством
внешнего исполнителя. Нормализатор сам доказательства не создаёт.

### OD planner и collector

`plan_urban_od_week(scope, points, *, week_start, reference_at, calendar, rules,
 profile_hash, evidence_refs) -> UrbanODPlan` принимает одну завершённую ISO-неделю.
`reference_at` явный; `rules.tau_days/tau_times` приходят из конфигурации. Calendar
передаётся в существующий τ-калькулятор; отсутствующий календарь не считается
обычной неделей. DST gaps/folds отвергаются существующим строгим конвертером.

`plan.tasks()` — ленивые направленные задачи для всех различных point IDs на
каждом срезе; `plan.batches(size)` — ограниченные tuple батчи. Полный roster и
население сохранены; только подтверждённое нулевое произведение позволяет пропуск.
Unknown population не удаляется. Coordinate/access совпадения не склеивают точки.
Идентичности построены по canonical JSON, включая отсортированные множества;
не зависят от Python hash seed или порядка входных точек.

- `od_road_request(task, entries, exits)` создаёт road request между **точками**,
  без UrbanDestination/категорий. `profile_hash=RoadBuild.profile_hash`.
- `od_road_observation(task, normalize_road_response(request, response))`
  сохраняет proven Tij либо unknown + best_known_seconds.
- Для ОТ: `profile_hash=road_hash(timetable)` из `a2_engine.routing.road`;
  `od_timetable_observation(task, timetable, *, resource_limit=None)` реально
  вызывает solve_timetable между физическими узлами на departure каждого среза.
  Allow transit=true, pure walk разрешён, общий лимит ожидания берётся из scope.
- `UrbanODCollector(plan).add(observations)` принимает части в любом порядке.
  Равный replay идемпотентен; конфликт отклоняется без last-wins. Compact
  observations хранят pair/proofs/best-known, без подробных недельных маршрутов.
- `collector.to_week()` или `assemble_urban_od_week(plan, observations)` сохраняет
  все 28 slice identities, даже без ответов. Отсутствующая пара остаётся unknown.
  Диагностика best-known доступна в collector.observations; внешние response/path
  артефакты и их постоянное хранение принадлежат исполнителю, не τ-модели.
- Готовый week передаётся без изменения формул в `calculate_urban_tau(...,
  reference_at=plan.reference_at, rules=rules)`, затем `result.to_threshold()`.

Срезы не переиспользуются автоматически даже для static: calendar restrictions
могут различаться. В этом этапе reuse не предоставляется; каждое время требует
собственного результата. Typical/live/historical явно отклоняются, static τ не
переименовывается. Unknown τ блокирует count, но сохраняет готовый H-индекс.

Исполнимые replay (все inputs/proofs искусственные):

```bash
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_car.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_od.py
```

Контроли: walk60 + auto1620 + walk120 = 1800 секунд, H1800 →50;
A→B600/B→A1800 на 28 срезах →τ1200; первые14 средних900 и последние14
средних3600 →τ2250. Регрессия также проводит OD→τ→threshold→car count.

Реальные car connectors/build/Valhalla executor и backend DTO adapter пока не
подключены. Изменения только добавляют ML1 API; canonical BACKEND DTO не меняются.
Реальных автомобильных индексов или τ Калининграда этот этап не подтверждает.
Следующий этап: принятый directed edge typical profile с dates/bins/season/graph,
затем live/historical freshness и path-entry-time validation, без static fallback.

Для текущего узкого road replay ferry исключён на correlation boundary; ответ с
`summary.has_ferry != false` остаётся unknown. Это не доказательство отсутствия
паромной связи. Расширение ferry требует принятой модели её времени/ожидания.

Итог этапа: 141 additive public exports (120 прежних), 591 engine tests;
Ruff/format, strict mypy (src/tests + оба новых examples), 10 прежних README
examples, новые car/OD replay, frozen lock/pip/config/diff checks пройдены.

## Traffic typical/live/historical — вычислительный этап (09.11.2026)

Рабочий каталог этого этапа — основная папка PyCharm:
`/Users/matveyyuri/Desktop/Общее/хакатоны/case-champion-hac`.
База — f4c8ae4, 596 тестов и 141 export. Новые 27 exports дают всего 168.
Старые static модели, сигнатуры и identities сохранены; динамический результат
не упаковывается в RoadBuild/RoadResult. Новых зависимостей нет.

### Файлы и публичный интерфейс

Все перечисленные имена доступны из `a2_engine`; тематические фасады —
`a2_engine.routing.traffic`, `a2_engine.urban.car`, `a2_engine.urban.od`.
Внутренние реализации импортируют конкретные модули.

| Файл | Новые публичные имена |
| --- | --- |
| routing/traffic/models.py | TrafficScope, TrafficEdge, TrafficGroup, TrafficCalendarDay, TrafficProvenance, TrafficObservation, TrafficProfile, TrafficSnapshot |
| routing/traffic/profiles.py | build_typical_profile, select_traffic_profile |
| routing/traffic/evaluation.py | TrafficEndpoint, TrafficRequest, TrafficTransition, TrafficPath, TrafficStep, TrafficPathResult, TrafficMinimumReceipt, evaluate_traffic_path, verify_traffic_minimum |
| urban/car/traffic.py | TrafficCarInputs, TrafficCarAnalysisResult, traffic_car_requests, analyze_traffic_car |
| urban/od/traffic.py | plan_typical_od_week, traffic_od_request, traffic_od_observation, TypicalODCollector |
| urban/od/_planning.py | Общая внутренняя валидация/сборка OD-плана; прежний static wrapper оставляет исходный guard и идентичности |

Основные сигнатуры:

```python
build_typical_profile(profile_id, group, observations, *, valid_from, valid_until,
                      calendar, provenance)  # -> TrafficProfile
select_traffic_profile(snapshot, group_edge_id, entry_at, *, checked_at)
evaluate_traffic_path(request, path)         # -> TrafficPathResult
verify_traffic_minimum(result, receipt)      # -> новый TrafficPathResult
traffic_car_requests(inputs, *, categories)
analyze_traffic_car(inputs, results, *, selected_categories, config, rules,
                    thresholds=())         # -> TrafficCarAnalysisResult
plan_typical_od_week(scope, points, snapshot, *, week_start, reference_at,
                     calendar, rules, evidence_refs, search_scope_id)
traffic_od_request(task, snapshot, entries, exits, *, checked_at, search_scope_id)
traffic_od_observation(task, result)         # -> прежний UrbanODObservation
TypicalODCollector(plan, snapshot, *, search_scope_id)
```

### Данные, QA и неизменяемость

TrafficScope фиксирует base snapshot, source graph, фактический graph/build,
traffic snapshot version, территорию, IANA timezone, mode, calendar_version,
engine_version и полный canonical costing_options_json. `costing_profile_hash`
проверяется как SHA256 canonical JSON `[engine_version, options, temporal_policy]`.
Options обязательно указывают `costing="auto"`, `speed_source="accepted_snapshot"`.
Это **внутренний контракт исполнителя**, не готовый HTTP payload Valhalla.

TrafficSnapshot.content_hash вычисляется из scope, профилей, календаря и evidence.
Переданный declared_content_hash проверяется. `version_key=traffic_version:content_hash`
должен быть UrbanVersions.traffic_version; base snapshot/graph/calendar/city/mode
проверяются адаптером. Одного совпадения строк версий недостаточно. Profiles и
calendar нормализуются в immutable tuples; конфликтующие ID/edge content запрещены.

TrafficGroup = точное направленное ребро (ID/from/to/length), scope, day_type,
season и местный 15-minute bin. CalendarDay содержит явные день/группу/сезон/evidence:
engine не угадывает holidays, weekdays, зимние коэффициенты или часы пик.
TrafficProvenance содержит source/method/license/quality/evidence; это проверяемые
принятые метаданные, не автоматическая аутентификация источника или лицензии.

`build_typical_profile` реально считает медиану переданных скоростей km/h. Порог
**пять разных локальных дней** — инженерное правило v1 IMPLEMENTATION.md, не новый
параметр методики. Пять записей одного дня недостаточны. Проверяются сравнимость
группы, календарь каждого наблюдения, валидность, ID и конфликтующие измерения
одного источника в один instant. Идентичный replay дедуплицируется; last-wins нет.
Недостаточная выборка даёт unknown-профиль с причиной, а не выдуманную скорость.
Samples hash и `aggregation="median_recomputed_v1"` отмечают пересчёт.

Уже агрегированный TrafficProfile с `aggregation="accepted_aggregate"` проходит
проверку QA, различных observation_days, source period, group и calendar evidence;
медиана без raw-наблюдений **не пересчитывается**. Calendar должен подтверждать и
даты наблюдений, и даты применения. `snapshot.profile_reason(profile)` отличает
отсутствующий календарь от несовместимой группы; `typical_accepted` выставляется
только после этих проверок для всех предоставленных профилей. Это не обещание
полного дорожного покрытия: пропущенные участки/пары всё ещё блокируют результат.

Observed period (`observed_from/to`) независим от UTC applicability
(`valid_from/valid_until`, полуоткрытый интервал). Закрытие — отдельный флаг;
нулевая скорость недопустима, unknown closure не становится открытой дорогой.

### Временная модель и минимум

Поддержана только явно принятая `entry_speed_v1`: `seconds=L/(speed_kph/3.6)`
на **въезде каждого ребра**. Скорость держится на полном ребре, даже если оно
пересекает следующий bin; интегрирования скорости нет. Следующее ребро выбирает
свой bin после предыдущего прибытия и дополнительных переходов. Данный контракт
поддерживает полные directed edges; частичные начальные/конечные рёбра требуют
отдельной нормализации/расширения, их длина не угадывается по координатам.

TrafficPath привязан к тому же scope/build/graph/policy; проверяются непрерывность,
границы и roster переходов. TrafficTransition содержит физические секунды и
`included_in_previous_edge`: дополнительный переход учитывается один раз; уже
включённый сохраняет raw_seconds, но не добавляет elapsed повторно. Evidence
переходов должно подтверждать их применимость для переданного пути/запроса.
Generalized penalties выбора пути не превращаются в физическое время.

TrafficEndpoint подтверждает направленный внешний walk connector и correlation.
Физическое отправление + entry = фактический network entry. Времена aware UTC;
local date/bin получаются через IANA zone без округления фактических секунд.
DST folds имеют разные UTC request IDs; один local-bin профиль применяется к обоим
только в пределах своего явного UTC validity. Раздельные UTC validity могут задать
разные профили; неоднозначная выдача остаётся unknown. Положительные submicrosecond
длительности отклоняются, raw fractions и созданные timestamp durations сохранены.

Чистый evaluator не расширяет минутную точность `/route` Valhalla 3.5.1.
Executor должен подтвердить exact instant независимо от того, каким протоколом
получен путь. Подтверждение другого/округлённого времени не подходит.

TrafficPathResult отдельно хранит known_seconds, шаги, profile_complete,
search_complete, minimum_proven, reasons и provenance. Полностью оценённый путь
без search proof — best-known с известным временем и unknown полным H-индексом.
Пересчёт static path с traffic не доказывает его optimality. Геометрия полной
цепочки не выдумывается.

TrafficMinimumReceipt связывает exact request, traffic content, path и evaluation
hashes. Request ID включает UTC departure/check time, build/costing/policy, physical
и access/correlation boundaries, search parameters/scope. Receipt требует search
completeness, minimum flag и evidence предпосылок алгоритма. `exhaustive` либо
`verified_temporal_algorithm` — утверждение доверенного executor, не выданное
engine подтверждение. FIFO не предполагается, скачки не сглаживаются. Если метод
executor требует FIFO, его evidence обязано подтверждать это для данного профиля.
HTTP200, alternatives, static receipt и одиночный summary минимум не доказывают.
Адаптеры повторяют evaluation/receipt validation; известная более быстрая
альтернатива отклоняет противоречивый receipt медленного пути.

Blocked edge означает невозможность **этого пути**, не пары. Missing, истечение,
непринятый профиль и отсутствие minimum proof не дают confirmed_unreachable.
Глобальный no-route этим supplied-path evaluator не устанавливается.

### Live и historical

`checked_at` обязателен и не берётся из now(). Для live отдельно проверяются
observed_at, fetched_at, validity и применимость ко времени въезда/всего ребра.
Fetched_at не заменяет observed_at; TTL engine не придумывает. При движении
проверяется тот же неизменяемый snapshot; новые наблюдения не подмешиваются.

Для historical обязательны реальные `observation_start_at/observation_end_at`
и observed_at внутри них. Source coverage покрывает выбранное время и всё ребро;
его конец не может быть позже fetched_at. Широкий applicability interval или
«похожий день» не расширяют фактическое наблюдение. Код причины отличает missing,
unknown timestamp, истечение во время ребра и неподтверждённое source coverage.

### Typical OD → τ и городской анализ

Новый planner использует исходный roster/правила недели/срезов и ленивые батчи.
Его profile_hash связывает `[traffic content hash, search_scope_id]`. Поэтому
пара/время/версии/search region входят в task identity. Query/collector проверяют
те же scopes, календарь, mode, reference/check instant и физические границы.
Collector принимает части и равный replay, отвергает конфликты и смешение snapshots.
Он переиспользует прежний UrbanODCollector, добавляя только проверенный traffic
bridge и typical_profile_accepted/evidence. Старые static задачи не переименованы.

Из known-but-unproven пути сохраняется best_known_seconds; пригодного Tij нет.
Пропущенная положительная пара, unknown population и excluded population остаются
неизвестными. Все 28 идентичностей сохраняются, матрицы static/typical автоматически
между срезами не копируются. `calculate_urban_tau` и Fraction-формула не изменены:
среднее среза, затем равновесное среднее срезов; threshold сохраняет baseline versions.
Unknown τ не уничтожает готовый H. Для live/historical политика baseline τ этим
этапом не вводится: count unknown, typical threshold не подставляется.

```bash
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_traffic.py
```

Replay явно синтетический: 08:13 + walk60 + edge60 + edge600 + walk60 = **780s**;
typical/live/historical цепочка **1800s**, H1800 → **50** только с отдельным fake
trusted receipt; без него путь известен, индекс unknown. Typical A→B600/B→A1800
на 28 срезах → **τ1200**, threshold используется в count (школа1800 →count0).
Это не реальные пробки или τ Калининграда.

### Handoff для общей интеграции

- **ML2:** передать directed edge IDs/from/to/full length и base/source graph/build/
  calendar versions; calendar для observation и application dates (IANA/day type/
  season); raw TrafficObservation или accepted aggregate с distinct dates, QA и
  отдельными периодами. Для historical — source UTC interval и fetched time после
  его завершения; для live — observed/fetched/valid-until без выдуманного TTL.
  Нужны source/method/license/quality/evidence и доказанные направленные car
  connectors/correlations. Критерий: typed snapshot принимается, QA/coverage
  неполнота остаётся явной, подмена группы/версии/content выявляется.
- **BACKEND:** adapters к новым внутренним типам, immutable TrafficSnapshot и
  checked_at; UrbanVersions.traffic_version=`snapshot.version_key`, graph/calendar/
  mode должны совпасть. Передавать отдельно known time, completeness, minimum/search
  proof, best-known и причины; numeric ranking только после полной существующей
  urban validation. Сохранять raw step seconds и evidence, не делать null→0.
  Canonical DTO в этом PR ML1 не изменены. Нужен отдельный handler/E2E этап.
- **DEVOPS/executor:** загрузить именно content-bound profiles в закреплённый
  граф/build, поддержать `entry_speed_v1` и exact query time/approaches/turn accounting,
  вернуть подтверждённый directed path и отдельно receipt, связанный со всеми
  hashes/boundaries/search scope. Принятие/подлинность evidence подтверждает внешний
  контур. Stock minute Valhalla response сам по себе для subminute proof недостаточен;
  если exact search/предпосылки не подтверждены, публикуется только best-known.
  HTTP, ingest, map matching, graph deployment и постоянный cache не входят в engine.

Проверки: исходная база **596 passed**, итоговый suite **656 passed**; Ruff/format,
strict mypy src/tests/examples, прежние 141 signatures/fields и static hashes,
все прежние README/replay, новый traffic replay, frozen lock/pip/config/diff checks.
Реальные accepted traffic data, production executor и совместный backend E2E ещё
не подключены. Реальные измерения пробок/автомобильный рейтинг не объявляются готовыми.


## Межгородской эталон A: самостоятельный builder (2026-11-09)

Текущий этап реализует accepted graph → per-edge calibration → explicit state graph
→ точный поиск → rich result → проверенный ReferenceTime → прежние индексы.
Это вычислительное ядро на нормализованных принятых входах. Межгородские реальные
данные не загружались; приведённые ниже маршруты и подтверждения синтетические.
Реальное расписание, actual journey search, частоты, cargo/scenario, HTTP и handler
остаются вне этого этапа. Численные FORMULAS v3 и product.json не изменены.

Публичные вызовы (из `a2_engine` либо канонических модулей):

```python
calibrate_reference_graph(graph: ReferenceGraph, scope: ReferenceScope) -> CalibratedReferenceGraph
build_reference(graph: ReferenceGraph, scope: ReferenceScope) -> ReferenceSearchResult
reference_time_from_result(result: ReferenceSearchResult, comparison: ReferenceComparison,
                           *, config: PassengerConfig) -> ReferenceTime
```

`routing/reference/models.py`: frozen ReferenceNetwork, ReferenceScope,
ReferenceState, ReferenceCalibration, ReferenceEdge, ReferenceOperation, ReferenceGraph.
Input collections копируются в отсортированные tuples; ID, версии, единицы, evidence,
NaN/Infinity/negative/boolean costs, дубликаты и конфликтующие ID, все FK и направление
проверяются до поиска. Порядок входных рёбер, состояний и observations не влияет на ID.
Поддержан только текущий `a2-methodology-v3 / edge-calibrated-v1`; config в адаптере
проверяется отдельно. Graph coverage и accepted evidence — утверждения внешнего
контура приёмки, а не доказательства, которые engine может получить из интернета.
Непустые evidence IDs связаны с контекстом и fingerprint; подлинность/допустимость
самих документов, лицензий, источников и полнота их реестра проверяются до вызова.

`ReferenceNetwork` фиксирует graph/snapshot/acceptance/applicability/compatibility
versions, method/profile, evidence и optional RoadBuild. `ReferenceScope` фиксирует
направленные территории, наборы transport nodes, allowed modes и comparison kind.
`exact_node_pair` требует singleton sets; `city_pair_best_node` выбирает любую пару
из фиксированных общих наборов. Пересекающиеся boundary sets отклоняются как
неоднозначные межгородские границы; пустой нулевой маршрут не создаётся.
Один common scope передаётся для всех сравниваемых actual chains/видов транспорта,
включая отдельный автомобильный индекс: reference не перестраивается по actual mode.

Каждое movement edge ведёт departure state → arrival state того же режима.
Состояние обязано различать существенные платформу, входящий сервис/ребро, владение
автомобилем и другие ограничения посредством уникального state/context ID. Перевод
arrival → [transfer states] → departure возможен только через явные совместимые
ReferenceOperation; несколько обязательных операций сохраняются раздельно.
в том числе при одинаковом physical node ID. Входная нормализация обязана не
сливать состояния, у которых разные допустимые продолжения. Operation может быть
между разными узлами, но никаких связей по близости/координатам не выводится.
Перенос автомобиля на rail/air не предполагается без принятого перехода.

`compatible` — обязательный keyword аргумент edge и operation, без принятия
совместимости по умолчанию. `compatible=True/False/None` означает подтверждена/запрещена/неизвестна;
`seconds=None` — неизвестное время, не 0. Подтверждённый 0 допустим. `operation_kind`
является описанием, не фильтром: одна и та же операция внутри цепочки включается,
до первого departure и после последнего arrival не включается благодаря границам.
Отсутствующая операция в графе с `coverage=full` означает отсутствие разрешённой
связи; если связь возможна, но ещё не подтверждена, ML2 обязан передать unknown
операцию либо partial network coverage. Отсутствие записи не может означать
молчаливую полноту ещё не обследованной сети.

Калибровка rail/air/water: минимум `confirmed=True` running-time samples **каждого**
направленного edge. Все samples связаны с edge ID, from/to states и одним Network.
Неподтверждённые samples не используются как seconds и сохраняют partial coverage.
Нет правила 5 дней, ожиданий, универсальных скоростей или historical whole-trip min.
Разные edges могут выбирать разные принятые source services в одном scope.

Road требует `static_freeflow_elapsed`, seconds, `RoadBuild` с закреплёнными
Valhalla 3.5.1, source/snapshot/build/graph/profile/options/evidence и auto freeflow.
Build каждого sample должен совпадать с build Network; конфликтующие elapsed costs
одного fixed edge/build отклоняются. `boundary_semantics` различает physical edge
и явно ограниченную logical connection. Последняя требует принятого physical
elapsed time между указанными states. Полное время pairwise RoadResult **нельзя**
раздавать физическим рёбрам; preference penalties и dynamic traffic не принимаются.
Для воздуха геодезическая длина только informational speed, не cost и не geometry.
Отсутствие geometry_ref не делает известный running time неизвестным.

`calibration.py` хранит принятый граф, scope, откалиброванные arcs, выбранные samples,
coverage, excluded IDs, причины пропусков и fingerprint. `search.py` выполняет
multi-source/multi-target Dijkstra по **состояниям**, поддерживает parallel edges,
восстанавливает чередование movement/operations и выбранные origin/destination.
Сравнение точное над рациональным представлением входных float, без промежуточного
округления. Финальные секунды — float. Tie-break `(cost, hops, arc IDs, start state)`
не допускает бесконечного улучшения на нулевом цикле. Никаких отсечек пересадок или
длительности нет. Переполнение суммы выявляется до помещения candidate в очередь и
завершает вызов ошибкой `REFERENCE_PATH_SUM_OVERFLOW`, без публикации A.
Стоимость хранения labels включает последовательность arcs; это CPU/in-memory
builder, не утверждение готовности к поиску на национальном production-графе.

| Данные/поиск | `search_status` | seconds и completeness |
|---|---|---|
| Полная принятая область, путь найден | minimum_proven | numeric, full |
| Подтверждённая часть, неизвестные альтернативы/операции/coverage | minimum_proven | numeric, partial; минимум только известной подсети |
| Полная область, пути нет | no_path_proven | None, full coverage; REFERENCE_NO_FEASIBLE_PATH |
| Неполная область, известного пути нет | known_subnetwork_no_path | None, partial; REFERENCE_COVERAGE_INCOMPLETE |

`full_ranking_eligible` истинен только при numeric minimum и full coverage. Полный
рейтинг partial reference запрещён, даже если найденный путь целиком подтверждён:
неизвестная альтернатива могла быть быстрее. Неполнота не обнуляет известный A.
Адаптер сохраняет numeric partial и прежний `PARTIAL_REFERENCE`, preliminary index.
A=None оставляет known actual times и waiting index. A>actual сохраняет исходный A,
cap 100 и `REFERENCE_INCONSISTENCY`. Полнота reference не означает полноту всего
пассажирского анализа: actual modes, chains и их evidence проверяет вызывающий слой.

Ресурсного прерывания API нет: вызов возвращается после доказательства минимума
известной подсети либо исчерпания достижимых состояний. Отмена/ошибка не возвращает
best-known как A. Если executor позже добавит budget/cancel, ему потребуется отдельный
search-incomplete result с best-known и seconds=None, без полного рейтинга.

`ReferenceComparison` должен быть создан из контекста actual analysis. Адаптер
сравнивает весь scope, method/profile config и принадлежность actual nodes общим
boundary sets. В city-pair actual A2/B2 допустимы при выбранных эталонных A1/B1;
в exact-node это другой анализ. При несовпадении возвращает scalar с
`applicability_confirmed=False`; прежние формулы дают REFERENCE_NOT_APPLICABLE либо
REFERENCE_VERSION_MISMATCH. Actual static/typical/live/historical допустимы;
новые traffic observations не меняют зафиксированный static A.

### Mapping к опубликованному canonical DTO

Прочитан BACKEND `work/backend@6ce41795572b3708dc09f49868a3cefaa7e77fb3`,
`packages/contracts/src/a2_contracts/v2/journeys.py:ReferenceResult` и
`dataset.py:ReferenceEdge/MinimumTransfer`. Код DTO и backend не изменён.

| canonical ReferenceResult | engine source / правило |
|---|---|
| reference_id | result.reference_id, hash algorithm version + accepted graph + scope + result |
| seconds | result.seconds; None при отсутствии пути |
| status | available при numeric, иначе unknown; not_applicable при отказе адаптера |
| completeness | result.completeness (coverage); в not_applicable ответе сохранить partial/diagnostics анализа |
| reason_code | numeric full: None; numeric partial: PARTIAL_REFERENCE; no_path_proven: REFERENCE_NO_FEASIBLE_PATH; partial no-path: REFERENCE_COVERAGE_INCOMPLETE; отказ: REFERENCE_NOT_APPLICABLE |
| profile_version | result.scope.network.profile_version |
| graph_version | result.scope.network.graph_version |
| boundary_node_sets | scope.origin_node_ids / destination_node_ids, **не** только выбранная пара |
| edges | result.path.edge_ids либо [] |
| necessary_operations | result.path.operation_ids либо [] |
| calibration_evidence | result.evidence_refs: network/coverage, все samples/edges/operations, в том числе unchosen/unknown alternatives |

Для `not_applicable` canonical seconds обязаны быть None (scalar внутри сохраняет
A для диагностики). Текущий DTO не выражает выбранную пару, snapshot/acceptance/
applicability/compatibility/methodology, comparison kind, allowed modes, порядок
movement+operations вместе, breakdown, выбранные calibrations, omitted IDs/причины,
search_status и graph fingerprint. BACKEND должен хранить богатый result или
расширить DTO до их включения. Нельзя выбрасывать их ради успешной сериализации.
Плоские canonical ReferenceEdge без state/compatibility и готовый scalar running
не доказывают полноту калибровок. MinimumTransfer с node IDs нужно нормализовать
в конкретные arrival/departure states и applicability, не соединять все режимы.

### Пример, контроли и интеграционный handoff

```sh
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_reference.py
```

<!-- executable-example -->
```python
import runpy

reference, passenger, car = runpy.run_path("packages/engine/examples/synthetic_reference.py")[
    "run_demo"
]()
assert reference.seconds == 3600 and reference.full_ranking_eligible
assert reference.path.edge_ids == ("rail", "air")
assert reference.path.operation_ids == ("transfer",)
assert passenger.path_index.value == passenger.waiting_index.value == car.path_index.value == 50
assert abs(passenger.index.value - 50) < 1e-10
```

Контроли: direct 5000 против 1800+300+1500 → A=3600; actual path=7200,
wait=43200 → path/wait/passenger/car=50. По отдельным samples AB=(1000,4000),
BC=(4000,1000), transfer=300 → A=2300. Unknown transfer → partial 5000,
без direct → unknown. Forbidden transfer → full 5000. Common sets A1/A2→B1/B2
дают 600 и выбранные A1/B1; exact A2/B2 даёт 900. A=9000/actual=7200 → cap 100.
Тесты также проверяют 45 случайных малых графов независимым полным перебором
простых **state paths с operations**, ещё 20 operation-state графов, zero cycles, ties, reversed direction,
parallel edges, numeric/ID/provenance ошибки и subprocess/PYTHONHASHSEED identity.

- **ML2:** accepted directed reference edges и edge-level running samples, fixed
  source scope/snapshot, обязательные minimum transfers, unknown/forbidden
  compatibility, достаточные state IDs, network/calibration coverage и provenance.
  Проверен опубликованный `work/ml2@0e6dbca005674f86c5cde7c2f2087477d509e7df`:
  `contract_v2.py` пока экспортирует пустые reference_edges/minimum_transfers.
  Это не помешало вычислительному синтетическому builder, но реальный A не рассчитан.
- **BACKEND:** adapter canonical accepted input → engine normalized graph; handler
  build_reference → ReferenceComparison(actual scope) → reference_time_from_result
  → прежние formulas. Сохранять rich result, один common scope, nullable states,
  partial diagnostics и запрет полного рейтинга. Не вызывать builder по actual mode.
- **DEVOPS/executor:** закреплённые Valhalla engine/build/graph/costing options,
  accepted static physical elapsed seconds для directed reference edges (или явно
  ограниченных logical connections); внешняя проверка acceptance/evidence.

Проверка публичной совместимости: сохранены порядок, подписи, поля/defaults/frozen
всех прежних 168 symbols (`tests/fixtures/public_api_168.json`, захвачен до изменений).
Добавлено 15, всего 183. Старый snapshot 141 не заменён. Архитектурный guard разрешает
intercity → routing.reference.*, обратные routing → intercity/urban по-прежнему
запрещены. Manifests/lock/config/canonical DTO и другие владельцы не изменены.


Проверки этого этапа: исходные 656 → **742 passing tests**, Ruff check/format
(103 files), strict mypy src/tests/examples (100 Python files), все 11 README
controls и 7 самостоятельных synthetic examples; uv lock --check, uv pip check,
product-config validator и git diff --check прошли. Полный набор команд и точные
результаты записаны в docs/tasks/ml1.md. Проверка выполнялась с PYTHONPATH для
текущего package=false; manifest/lock не изменялись.


## Полный вычислительный пассажирский межгород по расписаниям — 09.11.2026

Реализован самостоятельный анализ нормализованных событий: поиск допустимых
целых цепочек, оценка **максимального I_chain**, лучшие road/rail/air/water и mixed,
общий общественный максимум, отдельный автомобиль и физические отправления.
Это CPU/in-memory расчёт на переданных принятых данных, без HTTP/raw ingestion,
jobs, сценариев, cargo и backend handler. Реальные маршруты России не проверялись.
Предыдущие отчёты выше сохранены как история; актуальные возможности — здесь.

### Интерфейсы и входные данные

Из `a2_engine` экспортируются основной `analyze_intercity_passenger`,
`IntercityAnalysisScope`, `TransportModeBinding`, `PhysicalService`,
`PhysicalDeparture`, `BoardingReadiness`, `CapitalBoundary`, `AcceptedIntercityCar`,
`IntercityPassengerAnalysis`, `IntercityChainResult`, `IntercityModeResult`,
`IntercityFrequency`; независимый routing API — `search_scheduled_chains`,
`ScheduledChain`, `ScheduledSearchResult`. Новый `IntercityPassengerRules` и
`load_intercity_passenger_rules(path=None)` / `intercity_passenger_rules_from_mapping`
читают departure_window_days из текущей approved configuration (сейчас 7).
Прежний PassengerConfig, его loaders и все старые сигнатуры не изменены.

```python
analyze_intercity_passenger(
    scope: IntercityAnalysisScope, requested_at: datetime, timetable: Timetable,
    transport_modes: tuple[TransportModeBinding, ...],
    physical_services: tuple[PhysicalService, ...], reference: ReferenceSearchResult,
    *, config: PassengerConfig, rules: IntercityPassengerRules,
    frequency_start_at: datetime, frequency_end_at: datetime,
    readiness: tuple[BoardingReadiness, ...] = (),
    car: AcceptedIntercityCar | None = None, resource_limit: int | None = None,
) -> IntercityPassengerAnalysis
```

`scope.comparison_scope` — независимый контекст из request/accepted analysis data,
а не автоматически скопированный scope reference. Проверяются его snapshot,
graph, compatibility version против timetable scope.transfer_version, method
против rules/config и границы против node roster; затем существующий reference
adapter сверяет весь expected scope и фактические nodes каждой цепочки с A.
Применяется **один common A**. City-pair best-node и exact pair различаются.
Для субъектов territory_kind=regions требует двух явных принятых CapitalBoundary
(region/capital city/transport nodes/evidence), без угадывания по названиям.
TransportModeBinding — точное соответствие строки transport_type виду движения,
с evidence; substring guessing отсутствует. Unknown transport type ограничивает
полноту. Declared applicable_modes/modes_complete — отдельное входное утверждение
приёмки, а не вывод из того, какие рейсы случайно присутствуют в выгрузке.

Timetable/Run/Event/Footpath/Transfer и общие helpers используются прежние.
Нормализация календаря, exceptions, service dates, >24:00 и IANA/DST — внешний
контур; engine получает явные active True/False/None и aware timestamps, приводит
их к UTC. Одинаковые порядковые номера строк независимых таблиц не соединяются.
Все подтверждения и происхождение остаются принятыми входными условиями;
engine проверяет согласованность, но не удостоверяет подлинность документов.

### Точный поиск и временные границы

`routing/scheduled.py` перечисляет допустимые первые departure events в origin
sets из `[requested_at, requested_at + departure_window_days суток)`.
Для каждого первого события W0 фиксирован. При фиксированном common A индекс
монотонен по T_path, поэтому достаточно earliest final arrival для каждого
**первого события и точного набора использованных транспортных видов**.
Все такие кандидаты затем оцениваются прежним intercity_passenger_index;
максимум выбирается по целой цепочке, а не по компонентам разных рейсов.

Состояние сохраняет first boarding, onboard run/event, last alight event,
physical node и mode set. На нём хранятся Pareto labels по ready time и
canonical action order `(число actions, action IDs)`. Более ранний label с худшим
порядком не отбрасывает более поздний с лучшим порядком. Waiting steps не входят
в action order: ожидание до одного и того же события не нарушает dominance.
Таким образом сохраняется и максимум score, и детерминированное вторичное правило:
при равных индексах earliest final arrival, затем меньше actions, затем stable IDs.
Prefix score для pruning не используется. Нулевые walk/transfer cycles увеличивают
число actions и не создают бесконечного улучшения. Поиск не останавливается на
первом target и не ищет виды фильтрацией одного общего победителя.

Окно 7 суток касается **только первой посадки**. Дальнейшие события используют
независимый конечный TimetableScope.end_at. Нужны schedule/calendar/footpath/
transfer/continuation coverage и evidence; end_at меньше конца first window либо
выход продолжения за горизонт дают неполноту. `continuations_complete=True` —
явная принятая гарантия достаточности горизонта для заявленного scope. Engine
не доказывает полноту произвольно обрезанного источника. Городского лимита 4 часа
или его замены большим числом нет. Resource limit считает expanded labels;
при срабатывании сохраняются best-known, но optimum/frequency не объявляются
доказанными. Pareto front может быть большим: production SLA/RAM не измерялись.

Первая посадка только в origin transport node, финал только arrival с разрешённым
dropoff в destination. Ни доступа от жилья, ни initial walk для уменьшения W0,
ни финального walk tail, ни чисто пешего общественного межгорода нет. Внутренние
walk paths подтверждены и направлены. `pickup`/`dropoff` ограничивают посадку и
высадку; stay-onboard через остановку не создаёт новую посадку и учитывает dwell
один раз. Riding_seconds включает время внутри транспортного средства со стоянкой.
Transfer minimum сохраняет семантику **alight_to_departure**: проверяется интервал
от предыдущей высадки, но не прибавляется поверх уже учтённых walk+wait.
Unknown minimum/permission не становится 0; False — подтверждённый запрет.
Optional BoardingReadiness ограничивает допустимость первой посадки по принятому
ready_at, без универсального буфера и без переноса этого времени внутрь T_path.

W0=first departure−request; T_path=final arrival−first departure;
total=W0+T_path. Промежуточные ride/walk/wait_transfer сохраняются отдельными steps.
Пешая пересадка между поездами остаётся rail. Mixed означает несколько видов
**движения**, а не несколько рейсов или наличие пешего участка. Дополнительных
штрафов за пересадки/виды/частоту нет. AcceptedIntercityCar проверяется в том же
comparison scope и оценивается прежней intercity_car_index, без общественного
максимума; автобусные времена повторно на traffic factor не умножаются.

### Физические отправления и независимая полнота

PhysicalService описывает конкретное физическое исполнение (не линию или номер
рейса): physical_service_id, alias run IDs, event-sequence → PhysicalDeparture ID
и evidence. Alias group принимается только при одинаковых нормализованных
node/sequence/arrival/departure/pickup/dropoff/service date/calendar state и
согласованных видах. Несовместимые частичные alias feeds сначала согласует ML2;
engine их не склеивает. Группа превращается в один canonical run; run-specific
transfer rules переназначаются и конфликтующие правила отклоняются. Смена только
маркетингового alias не создаёт boarding/transfer. Identity evidence не заменяет
отсутствующие event/calendar evidence.

Frequency scope: **уникальные первые физические departure events из origin,
для которых найдено допустимое продолжение с высадкой в destination** за явный
полуоткрытый период `[frequency_start_at, frequency_end_at)`. Это не число плеч
и не число пересадочных комбинаций. Повторный визит run в origin различается по
конкретному event sequence/ID. Разные реальные отправления в одно время имеют
разные physical IDs. Один physical ID в rail и mixed учитывается один раз в public
union, не суммой групп. Missing identity → count=None и known_departure_ids как
подтверждённое подмножество, без заявления точного количества.

Если период частоты входит в first window, используется уже найденный frontier;
иначе выполняется отдельный event search за этим периодом с тем же continuation
horizon и правилами. `frequency_search` хранится отдельно; resource_limit применяется
к каждому поиску. Наличие score100 не завершает перечисление отправлений.
Частота не зависит от доступности A и не вводит штраф в индекс.

Результат раздельно хранит known admissible candidates (`chain_complete`),
physical_boardings_confirmed, scalar PassengerResult completeness, A completeness,
timetable_complete, modes_complete, public/by_mode optimum_proven,
frequency.complete/reasons, два search results с normalized Timetable/versions/horizon,
исходные bindings/physical supplements и full_ranking_eligible для
общественного score. При неизвестной identity operational boardings/transfers
сохранены, но не подписываются подтверждёнными физическими сменами транспорта.
Неполнота frequency не уничтожает подтверждённый индекс цепочки.

Partial A сохраняет численный preliminary score и PARTIAL_REFERENCE, но не полный
рейтинг. Неизвестный/неприменимый A оставляет raw times/waiting index и candidates,
а best_known **по индексу** остаётся None: earliest arrival не переименовывается
в score winner. A>actual сохраняет cap100/REFERENCE_INCONSISTENCY. Empty public
имеет connection_state=confirmed_absent при полной принятой сети или unknown при
неполноте/прерывании; ни численного max(empty), ни фиктивной цепочки 0/Infinity,
ни замены общественного результата автомобилем нет.

### Mapping и handoff

Прочитаны опубликованные BACKEND `6ce41795572b3708dc09f49868a3cefaa7e77fb3`
и ML2 `0e6dbca005674f86c5cde7c2f2087477d509e7df` через fetch/show, без merge.
Canonical contracts и чужие модули не изменялись.

| Canonical JourneyResult / Leg | Engine mapping |
|---|---|
| journey_id, scope | route.chain_id, intercity |
| origin_node_id/destination_node_id | route.path actual boundary nodes |
| requested_departure_at | analysis.requested_at |
| departure_at / arrival_at | first boarding timestamp / route.path.arrival_at |
| total_seconds / initial_wait_seconds / path_seconds | IntercityChainResult total/W0/T_path |
| total_wait_seconds | W0 + transfer_wait_seconds |
| legs | route.path.steps, включая wait_initial до departure; transport type → explicit mode binding; physical canonical service IDs сохраняются с alias map |
| time_kind, traffic_mode | complete_chain, static для расписанных времён (без дополнительного road multiplier) |
| reference_id, completeness, evidence_refs | evaluation.reference ID; полноту analysis/chain/A сохранять отдельно; analysis evidence |
| geometry / geometry_kind | None / none при отсутствии принятой реальной геометрии |
| status/reason | known actual chain available независимо от nullable score; score unknown reason из evaluation |

TimetablePath.departure_at внутри routing равен requested_at (и включает W0),
поэтому **нельзя** бездумно копировать его в canonical departure_at: там требуется
первое фактическое отправление. Времена и legs соответствуют canonical intercity
invariants. Отсутствующие geo не заменяются нарисованным рейсом.

ScheduledService/ServiceStopTime DTO не выражают полную event physical identity,
alias groups, run-specific transfer minimum/permission, readiness, region-capital
binding и все coverage/proof states. Поле trip_identity_confirmed само по себе
не заменяет alias mapping. BACKEND должен хранить supplement/rich analysis и
предоставить handler, который не выдаёт scalar completeness за полноту поиска.
ML2 должен передать согласованные executions/calendars/exceptions/UTC times,
mode mapping, physical identities, направленные walk paths и alight_to_departure
rules с evidence, scope/coverage/continuation horizon. В проверенном ML2 adapter
calendar_version остаётся not_collected; реальный E2E не выполнялся.
DEVOPS: worker CPU/RAM/budget, согласованные versions и достаточный accepted
horizon; измерить performance на реальном графе, не заменять неполноту таймаутом
с ложным full result. HTTP/executor ingestion этим этапом не реализуются.

### Исполнимый пример и проверки

```sh
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_intercity.py
```

<!-- executable-example -->
```python
import runpy

result = runpy.run_path("packages/engine/examples/synthetic_intercity.py")["run_demo"]()
best = result.public.best_known
assert best.initial_wait_seconds == 3600 and best.path_seconds == 1800
assert abs(best.evaluation.index.value - 97.15319411536) < 1e-9
assert result.frequency[-1].count == 2 and result.public.optimum_proven
```

Контроли: R1=70.710678 / R2=97.153194, выигрывает поздний R2; цепочка
12:00→12:30→walk600→wait1200→13:00→14:00 даёт W0=43200/path=7200/total=50400
и индексы50/50/50 при A3600. Черезостановочная поездка даёт path3600,
boardings1/transfers0. Кодшеринг и несколько продолжений не увеличивают count;
два независимых физических отправления в один момент дают count2.
Independent exhaustive oracle на 35 малых конечных расписаниях проверяет максимум
score, by-mode winners, фактические времена и physical frequency union.
Проверены boundary microseconds, >7d continuations, >4h waits, unknown/forbidden,
DST/UTC, normalized >24h dates, alias/calendar evidence, partial/reference/context,
resource interruption, cap/ties, permutations и fresh-process hash seeds.

Сохранены все прежние 183 exports/signatures/fields/defaults/frozen (snapshot снят
на исходном HEAD, прежние 141/168 snapshots оставлены). Добавлено 18, всего 201.
Intercity разрешены ровно необходимые routing reference.*, scheduled,
timetable_models, road (hash); обратные routing → intercity/urban запрещены.
Все новые импорты абсолютные. Старый solve_timetable и urban не изменены.


Итоговая проверка этого этапа: **782 passed** (исходные742), Ruff check/format
111 files clean, strict mypy src/tests/examples — 108 files clean; 12 README
controls и 8 standalone examples; lock/pip/product-config/diff checks прошли.
Точные команды и handoff записаны в docs/tasks/ml1.md.


## Грузовой вычислительный слой: три самостоятельных анализа — 09.11.2026

Текущая готовность: нормализованные принятые данные → вычисление городской
доступности, полной доставки и отдельных pipeline connections. Старые scalar
freight/urban.py, freight/intercity.py и pipeline/utilization.py не изменены.
Ни реальные грузовые перевозки РФ, ни подключение российских территорий к трубам
на основании этих синтетических примеров не подтверждаются. HTTP, jobs, ingestion,
production deployment и совместный BACKEND E2E остаются внешней интеграции.

### Общий профиль и подтверждения

`CargoProfile` хранит profile ID/version, food/nonfood, независимую urgency,
структурированный `CargoTemperature(controlled,min_c,max_c,condition_id)`,
handling/access requirements и `CargoVehicle` (class, kg/metres, access class,
restrictions). Допустимы отрицательные Celsius; диапазон и конечность проверяются.
Food не означает urgent/chilled. `requirements_hash` включает всё физическое
содержимое и версию, но исключает urgency: изменение только D не меняет физическую
совместимость. `CargoContext` фиксирует snapshot/graph/build/calendar/rules/profile
и v3. Контекст и требования включены в stable IDs.

`CargoCompatibility` связывает context, profile ID/version/hash, направленные
from/to IDs, service ID, inbound/outbound/delivery, период и evidence со статусом
confirmed/forbidden/unknown. Проверяется весь binding, не только boolean:
подмена профиля/ID/версий отвергается, непокрытый период становится unknown.
Правдивость документа и приёмка внешнего источника остаются внешним условием.
Подтверждения движения, обработки и **всего интервала ожидания** проверяются
отдельно; chilled constraints нельзя молча потерять на терминале или стоянке.
Новых сроков годности, коэффициентов потерь и временных допусков нет.

Длительности нормализованы в целые микросекунды и aware UTC. Непредставимые
дробные времена отклоняются, не округляются при поиске. Конечные времена
сравниваются как timestamps, а не отображаемые score. Input collections
замораживаются/сортируются; входные записи не меняются.

### Городская инфраструктура

```python
analyze_urban_cargo(
    profile, requested_at, sites, nodes, routes, receipts, coverage,
    selected_node_types, area_km2, *, config,
) -> UrbanCargoAnalysis
```

Модели: CargoSite, CargoNode, UrbanCargoCoverage, TruckRoute, TruckSearchReceipt;
результаты UrbanCargoSiteAnalysis/TypeAnalysis/Analysis. `expected_site_ids` —
независимый принятый roster физических площадок выбранной территории. Одинаковые
повторные записи одной площадки дедуплицируются, конфликтующие отвергаются.
Missing expected site создаёт unknown row и блокирует полный равновесный агрегат;
неподтверждённый тоннаж/население/юридические адреса веса не заменяют.
Incomplete roster при пустом списке — unknown, а не подтверждённое ZERO_WEIGHT.

У каждого CargoNode ровно один explicit analysis_type с mapping evidence:
port→port, rail_terminal→rail_freight_station; logistics_terminal требует
принятого выбора **terminal либо logistics_center**, без двойной классификации
одного node ID. Airport/pipeline_terminal не переименовываются в эти городские
категории. Отсутствующую классификацию ML2 передаёт как ограничение каталога,
а не как доказанную пустую категорию. Запрос оценивает каждый тип отдельно.

Outbound — site→node, inbound — node→site. Route endpoints, direction и requested
instant сверяются; обратное время не выводится. TruckRoute — новый грузовой тип
с costing=truck, vehicle/access requirements, fixed graph/build/profile, физическими
network+origin-access+destination-access seconds и evidence. Каждый подъезд задан
явно, включая подтверждённый 0. Passenger RoadResult/RoadBuild auto не принимаются
как freight proof. Receipt привязан к route fingerprint и query scope hash:
`content_hash((context, profile.requirements_hash, from_id, to_id, requested_at, direction))`.
Его complete/minimum flags являются принятым доказательством **внешнего truck
executor**, не результатом собственного поиска engine по неизвестным road edges.
Поддельную согласованность ID/hash engine проверяет; источник receipt не удостоверяет.

Среди разрешённых кандидатов выбирается минимум времени к совместимому узлу.
Unknown alternative, incomplete node catalogue или unproven per-pair minimum
сохраняют best-known route и observed infrastructure times, но не полный site index.
Подтверждённый запрет отличается от unknown. Полный no-route требует полного
применимого roster/catalogue и подтверждений поиска по всем необходимым парам.
Только тогда прежний scalar получает confirmed_no_route=True. H берётся из
административной площади и текущих bands через shared/area.py; прежняя публичная
urban.select_h_seconds сохранила owner/signature/поведение. freight→urban нет.
Готовые site indices агрегируются прежним urban_cargo_territory_index равновесно.

### Доставка до завершения конечных операций

```python
analyze_intercity_cargo(
    scope, started_at, profile, states, actions, readiness,
    *, config, rules, resource_limit=None,
) -> IntercityCargoAnalysis
```

`CargoDeliveryRules` / `load_cargo_delivery_rules(path=None)` /
`cargo_delivery_rules_from_mapping` читают departure_window_days из approved config.
Старый FreightConfig не менялся. Первое movement departure из [start,start+31d)
для всех urgent/ordinary/nonurgent; продолжения и completion могут быть позже.
D=24/72/744h — half-life, не дедлайн и не отдельное окно; A не участвует в cargo score.

CargoState сохраняет node, stage, execution и conditions ID. Начальная readiness
подтверждается в начальном unprepared state на общий started_at. Unknown readiness
или missing initial-state roster не подтверждает отсутствие доставки. Из unprepared
нельзя сразу двигаться: явная загрузка (или принятая not_required zero operation)
переводит в prepared конкретного execution. Movement требует совпадения execution
на обоих концах; пересадка/смена execution проходит через принятую обработку.
Финиш только в completed state destination, достигаемом handling, включая явное
подтверждение отсутствия необходимости операции. Passenger footpaths не грузовые arcs.

`CargoAction` — конкретное принятое исполнение/обязательство, с уникальным ID,
calendar active True/False/None и evidence, необходимыми выполненными operations,
physical seconds и compatibility. Movement имеет **фиксированное подтверждённое
scheduled_at**, включая road: наличие road path не создаёт мгновенный truck departure.
Handling с scheduled_at — фиксированный appointment; без него — **обязательная
немедленная обработка при готовности**, не свободно выбираемое continuous window.
Другие допустимые appointments/wait policies должны быть представлены принятыми
альтернативными actions. Engine не дискретизирует непрерывную доступность и не
заявляет точность для неописанного temporal model.

Положительный gap до action допускается лишь при явной CargoWaitPolicy с
initial_wait/transfer_wait и подтверждением условий на всём gap. После начала
движения initial_wait не используется. Loading входит в handling, поэтому
initial_wait **не** равен first departure−start при уже учтённой погрузке.
Все CargoInterval идут подряд без пересечений; complete total совпадает с
completed_at−started_at. Неизвестный gap/операция/условие не становится ожиданием/0.
Known movement и префикс остаются в incomplete_paths, но total/index unknown;
полный найденный маршрут при partial search сохраняет корректный preliminary score.

Алгоритм — exact priority search конечного execution/operation graph. Метка хранит
state, **точный ready timestamp**, origin, использованные конкретные action IDs,
mode set и first departure. Более ранний ready не доминирует поздний без проверки
воспроизводимости ожидания; поэтому такие метки сохраняются. Один execution/action
записи используется не более раза (повторное реальное событие требует другого ID),
что делает поиск конечным и безопасным на zero cycles. Completed candidates
сохраняются при обнаружении, даже до resource stop. Выбор — минимум completion
для каждого mode/mixed и overall, затем число actions и stable IDs. Все допустимые
ветви изучаются; ранний приезд в терминал не завершает поиск.

Это точный алгоритм для **переданного конечного графа**; frontier может быть
экспоненциальным. Произвольных time/transfer cutoffs нет. Resource limit прерывает
расширение states, сохраняет best-known и никогда не выдаёт полный победитель.
Finite accepted horizon и coverage хранятся отдельно; выход за него — unknown.
Подтверждение глобальной полноты зависит от services/calendars/operations/
compatibility/modes coverage, а не от полноты одного найденного пути.

### Трубы: отдельные принятые end-to-end connections

```python
analyze_pipeline_connections(scope, observations, *, config) -> PipelineConnectionsAnalysis
```

PipelineContext/AnalysisScope фиксируют snapshot/graph/registry, территории,
направленные boundary sets, продукт и точный период. PipelineEndpoint отдельно
подтверждает intake/delivery node, territory, product, period и evidence.
PipelineConnection — принятая **end-to-end** связь, не геометрический участок трубы;
транзитный регион не становится endpoint. Query не зеркалит направление и не
переносит oil на gas. При A→X и X→B совпадение X ничего не композирует.
`limitations` явно содержат ACCEPTED_END_TO_END_CONNECTIONS_ONLY,
NO_CONNECTION_COMPOSITION и NO_AGGREGATE_UTILIZATION. Для будущей композиции нужны
принятые правила стыковки/продукта/направления/приёма-сдачи; гидравлика/max-flow и
коммерческая доступность не выдумываются. Full absence касается заявленного
каталога end-to-end connections, не гипотетической физической прокачки по линиям.

PipelineMeasurement хранит nullable value, прежний PipelineBasis, context и
фактические period timestamps. V/Q проверяются и между собой, и против конкретного
connection/product/direction/period/query. Даже одинаковые системные bases не
подставляются на выбранную пару. Конверсия годовой Q или единиц автоматически не
делается: нужно принятое нормализованное значение мощности за тот же период.
Сопоставимые observations вызывают прежний pipeline_utilization. Несопоставимые
дают INCOMPARABLE_PIPELINE_DATA, Q<=0 отвергается, U>100 сохраняется с QA.
U разных связей не усредняется и не суммируется; общего U у анализа нет.
Q−V не считается свободной коммерческой мощностью. Pipeline не проходит cargo index.
Confirmed connection + unknown V/Q сохраняет связь; V=0 даёт U=0, не отсутствие.
Empty partial catalogue → unknown, полный empty catalogue → confirmed_absent.

### Canonical mapping и конкретные зависимости

Прочитаны BACKEND `6ce41795572b3708dc09f49868a3cefaa7e77fb3` и ML2
`0e6dbca005674f86c5cde7c2f2087477d509e7df`; contracts и чужие модули не менялись.

| Canonical input/output | Нормализация / обязательное дополнение |
|---|---|
| DeliveryCargoProfile.food_class/urgency | CargoProfile.goods/urgency независимо |
| temperature_requirements controlled/min/max | CargoTemperature, preserving Celsius range + accepted condition ID |
| vehicle_constraints height/weight/restrictions | CargoVehicle metres / normalized kg / restrictions; недостающие actual width/length/class/access требуют supplement, без придуманных defaults |
| FreightSite.site_id/access_node_id | физический site ID; truck access seconds сохраняются отдельно, expected roster из accepted scope |
| FreightNode.kind | явный node-specific mapping в request freight_node_types, см. ограничения выше |
| FreightCompatibility | дополнить context/profile version/hash/service/time binding и three-state provenance; raw compatible bool не подставлять напрямую |
| CargoDeliveryResult / canonical Metric, Score | known raw movement/components отдельно; full total/index только completed chain; analysis proof/coverage передавать отдельно от scalar value |
| PipelineConnection, Quantity | явные confirmed intake/delivery endpoints/territories и period context; Quantity basis connection ID/direction/product/unit/period привязать к выбранной записи |
| SubjectRef + evidence | site/node/connection и direction/profile scope из request + accepted data; сохранять rich chosen path/conditions/snapshot/limitations |

**ML2:** физические площадки и полный expected roster, node classification proof,
совместимость по profile contents, truck connectors, cargo service executions,
readiness/handling/wait conditions и calendar/coverage. Для pipeline — принятые
end-to-end connections с intake/delivery и сопоставимыми V/Q. В опубликованном
adapter freight_compatibility/pipeline_connections пока пусты; календарь not_collected.
Это не блокировало синтетические вычисления, но реальную готовность не подтверждает.
**BACKEND:** адаптеры профилей/units/periods и недостающие supplements, богатые
результаты/nullable/proof states, expected rosters, отдельные directions/profiles,
правильное unknown vs absent, никаких full ratings по known-only minima.
**DEVOPS/executor:** грузовой routing profile с проверенными actual vehicle/access
constraints, закреплёнными build/graph/options и scoped minimum receipts; accepted
temporal model/horizon и worker resource limits. Нужны реальные CPU/RAM измерения,
исполняемый truck provider и joint E2E; HTTP/deployment сюда не добавлены.

### Примеры и проверки

```sh
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_urban_cargo.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_delivery.py
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_pipeline.py
```

<!-- executable-example -->
```python
import runpy

urban = runpy.run_path("packages/engine/examples/synthetic_urban_cargo.py")["run_demo"]()
assert urban.h_seconds.value == 1200
assert [s.evaluation.index.value for s in urban.by_type[0].sites] == [50, 25]
assert urban.by_type[0].index.value == 37.5
```

<!-- executable-example -->
```python
import runpy

delivery = runpy.run_path("packages/engine/examples/synthetic_delivery.py")["run_demo"]()
assert delivery.overall.best_known.evaluation.total_seconds.value == 86400
assert delivery.overall.best_known.evaluation.index.value == 50
assert delivery.overall.best_known.known_movement_seconds == 57600
```

<!-- executable-example -->
```python
import runpy

pipeline = runpy.run_path("packages/engine/examples/synthetic_pipeline.py")["run_demo"]()
assert pipeline.state == "connected"
assert pipeline.connections[0].utilization.utilization_percent.value == 120
assert pipeline.connections[0].utilization.diagnostics == ("UTILIZATION_ABOVE_100",)
```

Контроли: urban H1200 → site50/site25/mean37.5; unknown faster port не даёт
полного минимума. Delivery: wait3600 + move57600 + handling18000 + transfer wait7200
=86400, urgent50. Arrival10h+unload8h проигрывает 12h+1h=13h. D/2D дают50/25
для всех срочностей при неизменном31d окне. Oil A→B U120% с QA, unknown quantities
и zero volume не удаляют connection; reverse/transit/gas/несопоставимые данные
не создают ложных связей и процентов.

Полный suite **830 passed** (исходные782), strict mypy src/tests/examples
**124 files clean**, Ruff check/format. Проверены все прежние **201** exports,
signatures/defaults/fields/frozen, добавлено36, всего237. Historical snapshots
141/168/183 сохранены. Прежние scalar outputs/static controls сохранены.
Independent raw-action DFS oracle (25 малых сетей), direction/roster/compatibility,
loading readiness/terminal handling/unknown gaps, half-open31d, fractional UTC/DST,
zero operations/cycles, invalid numbers/IDs/versions, permutations и subprocess
hash seeds покрыты тестами. Full command list и handoff — docs/tasks/ml1.md.


Wire conventions: readiness receipt использует from_id=to_id initial node,
service_id=`readiness:<state_id>`, direction=delivery, ready_at=started_at. Wait
receipt использует from_id=to_id текущего узла, service_id будущего action и
покрывает весь gap; operation/movement receipt связывает фактические from/to
nodes этого action. Это normalized supplements, не утверждение, что canonical
DTO уже содержит данные поля.

## Scenario Engine v3 — 09.11.2026

`a2_engine.scenario` — чистый детерминированный слой над существующими solvers:
`create_scenario_baseline(inputs)` → `simulate_scenario(baseline, operation,
feasibility=...)` → `validate_scenario_result(...)` перед публикацией. IO,
провайдеров и LLM внутри нет. Старые237 root exports сохранены в исходном порядке,
с прежними signatures/defaults/fields/frozen; добавлен31, всего268.

### Разрешённые изменения

Allowlist сверен с `apps/api/src/a2_api/analysis_service.py:DEFAULT_TEMPLATES`
опубликованного BACKEND `6ce41795572b3708dc09f49868a3cefaa7e77fb3`:
`coordinate_transfer`, `additional_departure`. Это проверенные имена server-side
шаблонов; engine дополнительно проверяет содержание операции. Проверенная ML2
ветка: `0e6dbca005674f86c5cde7c2f2087477d509e7df`. Чужие ветки не слиты.

`ScenarioOperation` связывает baseline ID, operation/template ID, target kind и
network/job ID, конкретный execution ID, service date, stop sequence/action ID,
ожидаемый execution hash, целочисленный ненулевой signed shift, incoming execution
и event, evidence. Один trip в разные service dates не является одним execution.

- `coordinate_transfer`, timetable: проверяются реальная пара рейсов, разрешённые
  pickup/dropoff, принятые stop/calendar/transfer/walk proofs и достаточный gap
  после изменения. `whole_execution` сдвигает все времена выбранного исполнения;
  `departure_tail` сохраняет предшествующие события и arrival выбранной остановки,
  сдвигает её departure и все последующие arrival/departure. Существующий validator
  отвергает отрицательную стоянку, нарушенный порядок и интервалы. Принятые физические
  aliases меняются вместе, все их копии в анализируемой области согласованы.
- `coordinate_transfer`, cargo: явный CargoExecution связывает дату и принадлежащие
  ему CargoAction; сдвигаются scheduled departure actions конкретного execution.
  Новый поиск должен доказать реальную цепь incoming→outgoing, включая обработку,
  готовность, совместимость и конечное завершение. Durations/profile/urgency не
  редактируются. Это расписание, не новый шаблон изменения длительности обработки.
- `additional_departure`, timetable: новая принятая физическая идентичность,
  evidence потребности, точная копия маршрута и stop pattern с объявленным сдвигом,
  активный календарь, полные physical departure IDs, ресурсы и оборот. Engine
  принимает её только при `feasibility=feasible`. Сам по себе большой headway
  доказательством не является. Run-specific transfer permissions не клонируются
  по догадке: нужны применимые уже принятые generic rules.

Обработка грузов с изменением duration, создание терминалов, изменение truck road
inputs, трубопроводов и cargo additional departure отклоняются: отдельных
утверждённых шаблонов/исполнений для них нет. Одно допустимое строковое имя не
разрешает произвольную подмену входов.

### Входы, неизменность и повторный расчёт

`ScenarioInputs` содержит отсортированные именованные `ScenarioTimetable` и jobs:
`UrbanScenarioInput`, `IntercityScenarioInput`, `CargoScenarioInput`,
`UrbanCargoScenarioInput`, `PipelineScenarioInput`. Для timetable execution hash
используйте `execution_hash(baseline, "timetable", network_id, run_id)`, для cargo —
`execution_hash(baseline, "cargo", job_id, execution_id)`.

Baseline включает полные normalized inputs, дорассчитанный before и хеши обоих.
Зафиксированы normalized request/context, scope, time/timezone, snapshot/graph/
calendar/build identities, configs/methodology, population/cells/district shares,
категории/приоритеты, H, τ thresholds/исходный UrbanTauResult, reference A,
readiness/physical services и routing rules/resource limit. TrafficSnapshot также
можно сохранить в `traffic_snapshots`: никакой live refresh не выполняется.
Сценарное расписание получает отдельный timetable version; graph, calendar и
traffic identities остаются прежними. Baseline проверяется по входам и реальному
повторному before, а не только по supplied result hash.

Overlay создаётся через immutable replacements. Он содержит changed events и
affected execution IDs; исходные события, соседние даты и входы не мутируют.
Повтор с теми же входами даёт тот же результат; overlay нельзя передать вместо
baseline для незаметного удвоения сдвига. Разные PYTHONHASHSEED и перестановки
записей проверены. `scenario_hash` — engine content identity, не canonical wire ID.

Полностью пересчитывается **вся переданная ограниченная область**:

- город: `search_urban_transit` по всем point/category/mode/destination,
  `analyze_urban_candidates`, `analyze_urban_districts` и опциональный
  `search_urban_last_return` с вновь выбранным объектом. H и τ не переоцениваются;
- межгород: `analyze_intercity_passenger`, максимум индекса целой цепочки, W0,
  T_path/total, режимы/public, physical frequency; прежний common reference A;
- грузовой межгород: `analyze_intercity_cargo`, минимум полного времени доставки,
  включая конечную разгрузку, пересадки и условия ожидания;
- городские грузы: `analyze_urban_cargo`; трубы:
  `analyze_pipeline_connections`. При текущих расписательных операциях их входы
  неизменны, и их повторно рассчитанные результаты остаются прежними.

Принятые road/private-car candidates разрешены как `fixed_candidates`, потому что
операции не изменяют road inputs и момент самостоятельной поездки. ОТ/пешеходные
candidates подставить готовыми нельзя. Пересчёт bus travel times из traffic profile
не придумывается: расписание остаётся принятым расписанием исполнения.

### Полнота эффектов и охвата

`ScenarioMeasure.metric_id` основан на semantic key (job, subject, category/mode,
metric name), единице, направлении улучшения и замороженном scope hash.
Конкретный journey ID/выбранный объект не участвует в сопоставимости. Поэтому
смена лучшего маршрута или объекта не прячет прежнюю метрику.

Engine сам строит все `ScenarioEffect`: improved/worsened/unchanged, доказанные
new_connection/lost_connection и unknown. Index delta = after−before; time saved =
before−after, через прежние comparison functions. Потеря/появление связи имеют
nullable delta, отсутствие маршрута не превращается в нулевое время или Infinity.
Unknown→known и known→unknown не считаются появлением/потерей связи. Неполный
best-known сохраняется в богатом result, но эффект неизвестен. `complete`
означает полноту эффектов, независимо от feasibility и population completeness.

`validate_effect_coverage` сравнивает полный roster, metric IDs/units/scope и все
дельты/worsened IDs. Для публикации нужен **`validate_scenario_result`**: он заново
воспроизводит after/effects/impact из baseline + операции + constraints и отвергает
подмену известного ухудшения на null/unknown даже с согласованным фальшивым delta.
Цена этой защиты — повторный полный расчёт; отдельная оптимизация не заявлена.

`ScenarioImpact` считает потенциально затронутых жителей по уникальным исходным
(city, original cell, population version), один раз для нескольких категорий/jobs.
Это не пассажиропоток. При неизвестном населении/неполном coverage или только
долях без original-cell population выдаются known lower bound, unknown cell IDs и
`population_complete=False`. Для грузов — уникальные physical site IDs, не
терминалы, юрлица или тонны. Межгородской cargo job должен передать принятый
`physical_sites`; без него изменившийся job отражён в `unmapped_site_job_ids`, и
`sites_complete=False`. Все scope job IDs и версии сохраняются в полном результате.

### Реализуемость отдельно от эффекта

`ScenarioFeasibilityInputs` — принятые baseline/operation-bound resource blocks,
covered execution IDs, validity period, resource/calendar/infrastructure coverage,
availability, явные turnaround links и minimum before/after. Универсальных норм
нет; отсутствующее minimum неизвестно, принятый явный0 допустим.

Проверяются фактические окна занятости, availability, соседние исполнения и
минимальные интервалы, включая объединение одного resource ID из разных network
extracts и physical aliases. Cargo occupancy извлекается из настоящего полного
поиска, без выдуманной длительности недоказанной обработки. Недостаточное покрытие,
неизвестная обработка/календарь или устаревший evidence дают unverified. Подтверждённый
конфликт даёт infeasible, достаточные принятые ограничения — feasible.
Без constraints модельный эффект разрешён, но label:
**«Модельный эксперимент — реализуемость не подтверждена»**.

### Исполнимый пример

```sh
PYTHONPATH=packages/engine/src uv run --frozen python packages/engine/examples/synthetic_scenario.py
```

<!-- executable-example -->
```python
import runpy

scenario = runpy.run_path("packages/engine/examples/synthetic_scenario.py")["run_demo"]()
assert scenario.complete and scenario.feasibility.state == "unverified"
assert scenario.impact.known_affected_population == 100
assert any(effect.outcome == "new_connection" for effect in scenario.effects)
assert any(
    effect.outcome == "worsened" and effect.delta.value == -180 for effect in scenario.effects
)
```

Контроль: incoming прибывает08:10, minimum transfer5мин, outgoing08:12→08:15;
для p1 настоящий solver находит новую поездку1500с, для p2 время1320→1500с,
ухудшение−180с сохранено. Население90+10, по двум категориям без удвоения.
Отдельные тесты: downstream connection lost с null временем; H=τ=1200,
1500→900 и count0→1; fixed-A межгород; груз24ч→23ч с конечной обработкой;
полные независимые reruns вручную изменённых малых расписаний; publication attacks,
перестановки/hash seeds и unchanged static/typical snapshots/τ.

### Точный handoff

**BACKEND — `apps/api` и `packages/contracts`:** wire Scenarios v2 из проверенной
ветки содержит два operation_type, service_ids/shift_seconds и resource evidence,
но не полный engine supplement: нужны конкретные service_date, stop/event ID,
expected execution hash, incoming event/execution, явный timing model и accepted
physical/resource bindings. DTO ServiceChange whole_trip/subsequent_stops_updated
нужно согласовать с departure_tail без ложного whole_trip=True. Feasibility DTO
сейчас verified/unverified: engine требует отдельный infeasible с причинами; feasible
можно отображать в verified только в принятом constraint scope. Не превращать
new/lost nullable delta в0. Mapper должен сохранить rich before/after, semantic keys,
все worsening/unknown и отдельные coverage/feasibility/impact flags; canonical IDs
выдаются по backend rules с сохранением engine IDs как provenance. Хранить inputs,
operation и constraints для `validate_scenario_result` перед атомарной публикацией;
не публиковать урезанный frontend список эффектов как полный. Acceptance: пропуск,
null/unknown подмена или смена route ID не скрывают подтверждённое ухудшение.

**ML2 — `packages/data_pipeline`:** полные dated executions с событиями, pickup/
dropoff, физическими aliases, accepted transfer/walk permissions/calendars;
CargoExecution→CargoAction/date bindings, loading/compatibility/processing/wait
conditions и physical site roster. Resource/vehicle blocks должны содержать все
соседние исполнения в области, scope/validity/evidence, availability и явные
turnaround/boundary minimums. Пустые данные не заполнять guessed permissions или
нулевыми нормами. Acceptance: реальные accepted snapshots проходят baseline и
feasibility binding; недостаточность остаётся unverified/partial.

**DEVOPS — `infra`/worker:** ограничить область и CPU/RAM/time budget, сохранять
resource-limit incompleteness, повторять worker с тем же immutable input bundle,
замерить стоимость baseline + full recompute + publication guard. Нужны реальные
performance/E2E и атомарное backend хранение; SLA на страну этим этапом не заявлен.
Реальные транспортные данные и production интеграция здесь не проверялись.

Проверки этапа: самостоятельно запущенная исходная регрессия830 → итоговая872;
40 scenario cases и2 compatibility/architecture cases. Ruff check/format clean
(138 files), strict mypy src/tests/examples135 files clean; frozen lock/pip/product
config/diff checks пройдены. Все16 README controls и12 standalone examples
исполнены. Прежнее pytest-asyncio fixture-scope warning не изменялось.
