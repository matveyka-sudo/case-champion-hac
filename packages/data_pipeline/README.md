# a2_data_pipeline (ML2, №5)

Реестр источников, загрузка raw с хэшами, QA и принятые bundle данных для engine и BACKEND.
Действующая постановка — `docs/PRODUCT_SPEC.md` 2026-11-08.1.

## Что есть

| Что | Где |
| --- | --- |
| Реестр всех источников A2 (S01–S22) и технических дополнений: роль, что даёт, ограничения, лицензия, статус | `data/manifests/sources.json` |
| SourceManifest каждого загруженного файла: URL, дата, sha256, лицензия, покрытие | `data/manifests/datasets/*.json` |
| Принятые bundle (небольшие, ODbL / CC BY 4.0, можно хранить в git) | `data/bundles/<bundle_id>/` |
| Raw и промежуточные файлы (не в git) | `data/raw/`, `data/processed/` |

Сейчас опубликован `kaliningrad-urban-2026-11-08.2` (accepted). Он заменил `.1`: тот был собран в прежнем формате, без входов на граф, и его никто не использовал. В QA-отчёте (`qa_report.json`) 12 проверок: 11 pass, 1 warn, fail нет. Заявленный период применимости — 01–30.11.2026.

| Коллекция | Строк | Покрытие |
| --- | ---: | --- |
| territories | 4 | complete: городской округ (224,668 км², геодезическая площадь по границе OSM) и три района |
| population_cells | 286 | complete: сетка 1 км в UTM 34N от начала (0, 0), обрезана по границе; население GHSL R2023A эпохи 2025 — 485 776 чел. |
| sample_points | 216 | complete: 70 ячеек исключены (59 без зданий, 11 вода), в них 277,8 чел. |
| access_nodes | 612 | complete для walk: вход каждой точки и каждого объекта на пешеходную сеть |
| destinations | 398 | airport complete; rail/bus terminal и ТЦ partial; 5 категорий медицины и образования unknown (карантин) |
| category_ownership | 398 | unknown: официальный реестр учреждений ещё не загружен |
| evidence | 1228 | записи OSM, OurAirports и GHSL с sha256 содержимого, плюс evidence на целые выгрузки |

Расписания, грузы и трубопроводы в этот bundle не входят. В `manifest.coverage` у них стоит `not_collected`. Пустой список не означает отсутствия.

## Правила, которые проверяет код

- **Неизвестное не равно нулю.** `population_status` бывает `estimated`, `zero` или `unknown`; при `unknown` поле `population` равно `null`.
- **Отсутствие требует scope.** `confirmed_absence` допускается только при `status=complete` своего scope.
- **Точки выборки.**
  - В каждой sampled-ячейке ровно одна точка, в excluded — ни одной, и у ячейки указана причина.
  - Точка стоит в центре квадрата, если центр попадает в здание. Иначе это ближайшая точка части здания внутри той же обрезанной ячейки. При равенстве выбирается меньший id здания.
  - Переноса между ячейками нет: точка и исходный центр лежат в рамке своего квадрата (`square_bbox`).
- **Вход на граф** (`access_nodes`, правила `osm-walk-network-v1`).
  - Это проекция точки на ближайшую пешеходную линию OSM из главной связной компоненты сети, поэтому вход не попадёт на оторванный кусок.
  - Ничья — в пределах 1 мм от минимума, её решает меньший id линии.
  - Расстояние до входа сохраняется в `access_distance_m` и не теряется из времени пути.
  - Аэропорт ставится на пассажирский терминал (`aeroway=terminal`), а не в центр лётного поля.
- **Население.** Население принадлежит исходной ячейке и считается по центроидам пикселей GHSL. Доли районов берутся из тех же пикселей и не превышают население ячейки (QA `district_population_not_doubled`).
- **Публичные категории.** Поликлиника, детсад, школа, колледж или вуз могут быть `accepted` только с `category_ownership.basis=registry` и `verification_state=verified`. Пока такой сверки нет, они в карантине с причиной `OWNERSHIP_UNVERIFIED`.
- **Объекты за границей.** Объект за границей города разрешён и помечается `inside_city_boundary=false`. Пример — аэропорт Храброво.
- **Неизменяемость.** Bundle публикуется атомарно (staging → rename) и не перезаписывается. `load_bundle` при каждом чтении сверяет sha256 всех коллекций и QA-отчёта, число строк и все ссылки.

## Как пользоваться

```python
from a2_data_pipeline import load_bundle

data = load_bundle("kaliningrad-urban-2026-11-08.2")  # BundleData, формат ML2
cells = data.population_cells  # население, district_shares
points = data.sample_points  # одна точка на sampled-ячейку + вход на граф
city = data.territories[0].area_km2  # для H по FORMULAS.md §2
usable = [d for d in data.destinations if d.status == "accepted"]
```

Для BACKEND то же самое в каноническом контракте `a2_contracts.v2.AcceptedData` (PR #8):

```python
from a2_data_pipeline.contract_v2 import load_accepted_data, registry_options, to_accepted_data_v2

accepted = load_accepted_data("kaliningrad-urban-2026-11-08.2")  # AcceptedData, validated
payload = to_accepted_data_v2(load_bundle("kaliningrad-urban-2026-11-08.2"))  # тот же JSON без валидации
options = registry_options(load_bundle("kaliningrad-urban-2026-11-08.2"))  # TerritoryOption / AreaOption
```

Ошибки имеют поле `.code`:
- `BundleNotFoundError` — `unknown_id`;
- `BundleNotAcceptedError` — `bundle_not_accepted`;
- `BundleIntegrityError` — `bundle_integrity`.

Без DEVOPS-манифеста запускать с `PYTHONPATH=packages/data_pipeline/src`. Чтение bundle требует только pydantic, `load_accepted_data` — ещё и `a2_contracts.v2`.

## Пересборка и проверки

Гео-зависимостей пока нет в манифесте пакета (см. handoff DEVOPS ниже), поэтому они подключаются оверлеем без изменения lock:

```bash
PYTHONPATH=packages/data_pipeline/src uvx --from uv==0.12.17 uv run --frozen \
  --with 'osmium>=4,<5' --with 'shapely>=2,<3' --with 'pyproj>=3.6,<4' --with 'rasterio>=1.4,<2' \
  python -m a2_data_pipeline.build_city kaliningrad --bundle-id <новый-id> --accept
```

```bash
PYTHONPATH=packages/data_pipeline/src uvx --from uv==0.12.17 uv run --frozen \
  --with 'osmium>=4,<5' --with 'shapely>=2,<3' --with 'pyproj>=3.6,<4' --with 'rasterio>=1.4,<2' \
  python -m pytest packages/data_pipeline/tests
```

`test_contract_v2::test_backend_model_accepts_payloads` запускается, когда импортируется `a2_contracts.v2`. До слияния PR #8 в dev для этого нужно добавить `packages/contracts/src` из `work/backend` в `PYTHONPATH`.

Raw, которые использованы для сборки, перечислены в `data/manifests/datasets/`. Raw-файлы в git не хранятся. Ссылки и sha256 лежат в этих манифестах; повторная загрузка того же URL должна совпасть по хэшу.
- Выгрузка Geofabrik `russia/kaliningrad-261107.osm.pbf`, md5 сверен с Geofabrik.
- Тайл GHSL `GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R3_C20.zip`.
- OurAirports `airports.csv`.

Сборка детерминирована: две сборки подряд дают одинаковые sha256 всех коллекций.

## Ограничения текущей версии

- **Границы.** Взяты из OSM (relation 331414 и районы 331411–331413), это не официальный реестр границ МО.
- **Население.** Это модельная оценка GHSL на 2025 год. Для сверки есть тег OSM `population=459560` без даты. Официальную сверку с Росстатом (`rosstat_demography`) ещё предстоит сделать.
- **Правила категорий** `osm-category-rules-v1`:
  - ж/д вокзал — объект со словом «вокзал» или пассажирская станция «<Город>-…»;
  - автовокзал — `amenity=bus_station` со словом «автовокзал/автостанция»;
  - ТЦ — `shop=mall`, безымянные ТЦ уходят в карантин;
  - школы без дополнительного образования (спорт, музыка, танцы и т. п.);
  - стационары не считаются поликлиниками;
  - поиск объектов — в пределах 10 км от границы города, для аэропортов — 60 км.
- **Вход на граф.**
  - Это проекция на пешеходную сеть, а не размеченный вход здания.
  - Главная компонента сети покрывает 96,5 % её длины.
  - До входа: у точек медиана 19,7 м и максимум 838,5 м, у объектов медиана 29,9 м.
  - Три точки на северной окраине (ряд `n6069`) дальше 500 м — это QA warn `access_distance_points`.
  - Подъезд по дорожной сети для `private_car` ещё не построен, поэтому покрытие в v2 заявлено только для `walk`.
- **Не сделаны:** реестр собственности учреждений, расписания, грузовые данные.

## Handoff

### BACKEND (№3)

`load_accepted_data("kaliningrad-urban-2026-11-08.2")` проходит `a2_contracts.v2.AcceptedData` из PR #8 (`work/backend`, d2cfe9a), и `model_validate`, и `model_validate_json`.

Что в нём:
- 1 территория, 286 ячеек, 216 точек;
- 39 объектов: 34 ТЦ, 2 ж/д вокзала, 2 автовокзала, аэропорт;
- 255 входов на граф, 1228 evidence;
- два `CoverageScope` по `walk`: аэропорт `full`; вокзалы, автовокзалы и ТЦ `partial`.

Негативная проверка: если вручную добавить школу из карантина, модель v2 отклоняет её с ошибкой «Public medical/education category requires public ownership».

Нужно решить BACKEND:

1. **Районы.** `TerritoryLevel` допускает только `city|region`, а у `PopulationCell` нет долей районов. Районы ML2 отдаёт как `AreaOption` через `registry_options()`. Но для агрегатов районов без удвоения (PRODUCT_SPEC §2) нужны доли населения ячеек по районам.
   - Предложение: поле `district_shares: [{area_id, population}]` у ячейки, где сумма долей не больше `population`. Готовая модель — `DistrictShare` в `bundle.py`.
   - Критерий: агрегат района считается из исходных ячеек и долей, а не средним районов.
2. **Категория без scope.** Медицина и образование в карантине в snapshot не попадают, и `CoverageScope` по ним нет. Нужно подтвердить: отсутствие scope по категории означает `DATA_UNAVAILABLE`, а не `NO_FEASIBLE_ROUTE`.
3. **Расстояние до входа у объекта.** У `SamplePoint` есть `access_distance_m`, у `Destination` нет. ML2 хранит это расстояние у объекта в своём bundle.
   - Предложение: `Destination.access_distance_m`.
   - Критерий: путь до объекта включает расстояние от входа до объекта.
4. **`calendar_version` обязателен,** а расписаний в этом snapshot нет. Сейчас ML2 передаёт `not_collected`. Предложение: сделать поле nullable или явно договориться о значении `not_collected`.

Отдельно: в первом handoff BACKEND первым регионом названа Ленинградская область, а по PRODUCT_SPEC это Москва, Калининград и Новороссийск.

### ML1 (№4)

Для городского пассажирского индекса уже есть:
- население исходных ячеек со статусом, методом и годом, плюс доли районов;
- точки выборки с исходным центром;
- вход на пешеходную сеть: `access_node_id` и `access_distance_m` у точек и у объектов;
- площадь административной границы с `boundary_version` (для H);
- объекты категорий: использовать только `status=accepted`.

Вход — это не вершина графа, а проекция на линию OSM. В `access_nodes` есть `osm_way_ref` и `offset_along_way_m`. Расстояние до входа нужно добавлять к пешему участку один раз.

**Не 0, а unknown.** Индекс категории с `coverage.status=unknown` (5 публичных категорий, пока нет реестра) нужно считать unknown. Для partial-категорий (вокзалы, автовокзалы, ТЦ) отсутствие найденного объекта не доказывает недостижимость.

**Чего ещё нет:** расписаний ОТ и подъезда по дорожной сети для `private_car`.

### DEVOPS (№2)

**Где.** `packages/data_pipeline/pyproject.toml`, `uv.lock`.

**Что добавить:**
- в dependencies: `pydantic>=2,<3`, `numpy>=2,<3`, `shapely>=2,<3`, `pyproj>=3.6,<4`, `rasterio>=1.4,<2`, `osmium>=4,<5`;
- в группу dev, по желанию: `types-shapely`.

Проверено оверлеем, версии на 08.11: shapely 2.2.0, pyproj 3.8.0, rasterio 1.5.2 (GDAL 3.12.2), osmium 4.x. Колёса есть для macOS arm64 и Linux, Python 3.12.

**Критерий приёмки:**
- `uv sync --frozen --all-packages --group dev` ставит пакеты;
- `PYTHONPATH=packages/data_pipeline/src pytest packages/data_pipeline/tests` проходит без skip гео-тестов. Сейчас с оверлеем и контрактами PR #8 — 59 passed.

Отдельно: CI сейчас не запускает pytest пакетов.
