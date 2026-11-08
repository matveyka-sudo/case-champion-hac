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

Сейчас опубликован `kaliningrad-urban-2026-11-08.1` (accepted). Его QA-отчёт лежит в `qa_report.json`, все 9 проверок пройдены.

| Коллекция | Строк | Покрытие |
| --- | ---: | --- |
| territories | 4 | complete: городской округ (224,668 км², геодезическая площадь по границе OSM) и три района |
| population_cells | 286 | complete: сетка 1 км в UTM 34N от начала (0, 0), обрезана по границе; население GHSL R2023A эпохи 2025 — 485 776 чел. |
| sample_points | 216 | complete: 70 ячеек исключены (59 без зданий, 11 вода), в них 277,8 чел. |
| destinations | 398 | airport complete; rail/bus terminal и ТЦ partial; 5 категорий медицины и образования unknown (карантин) |
| category_ownership | 398 | unknown: официальный реестр учреждений ещё не загружен |
| evidence | 428 | ссылки на записи OSM и OurAirports |

Расписания, входы на граф, грузы и трубопроводы в этот bundle не входят. В `manifest.coverage` у них стоит `not_collected`. Пустой список не означает отсутствия.

## Правила, которые проверяет код

- **Неизвестное не равно нулю.** `population_status` бывает `estimated`, `zero` или `unknown`; при `unknown` поле `population` равно `null`.
- **Отсутствие требует scope.** `confirmed_absence` допускается только при `status=complete` своего scope.
- **Точки выборки.** В каждой sampled-ячейке ровно одна точка, в excluded — ни одной, и у неё указана причина. Точка стоит в центре квадрата, если он попадает в здание; иначе это ближайшая точка части здания внутри той же обрезанной ячейки. При равенстве выбирается меньший id здания. Переноса между ячейками нет.
- **Население.** Население принадлежит исходной ячейке и считается по центроидам пикселей GHSL. Доли районов берутся из тех же пикселей и не превышают население ячейки (QA `district_population_not_doubled`).
- **Публичные категории.** Поликлиника, детсад, школа, колледж или вуз могут быть `accepted` только с `category_ownership.basis=registry` и `verification_state=verified`. Пока такой сверки нет, они в карантине с причиной `OWNERSHIP_UNVERIFIED`.
- **Объекты за границей.** Объект за границей города разрешён и помечается `inside_city_boundary=false`. Пример — аэропорт Храброво.
- **Неизменяемость.** Bundle публикуется атомарно (staging → rename) и не перезаписывается. `load_bundle` при каждом чтении сверяет sha256, число строк и все ссылки.

## Как пользоваться

```python
from a2_data_pipeline import load_bundle

data = load_bundle("kaliningrad-urban-2026-11-08.1")  # BundleData
cells = data.population_cells  # население, district_shares
points = data.sample_points  # одна точка на sampled-ячейку
city = data.territories[0].area_km2  # для H по FORMULAS.md §2
usable = [d for d in data.destinations if d.status == "accepted"]
```

Ошибки имеют поле `.code`:
- `BundleNotFoundError` — `unknown_id`;
- `BundleNotAcceptedError` — `bundle_not_accepted`;
- `BundleIntegrityError` — `bundle_integrity`.

Без DEVOPS-манифеста запускать с `PYTHONPATH=packages/data_pipeline/src`. Чтение bundle требует только pydantic.

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
- **Не сделаны:** `access_nodes` (входы на граф), реестр собственности учреждений, расписания.

## Handoff

### BACKEND (№3)

**Где и почему.** `packages/contracts/src/a2_contracts/dataset.py` в `work/backend`: модель `AcceptedData` 0.1.0 написана по прежней постановке P1 (medical/regular, Ленинградская область). В ней нет коллекций v2 из `packages/contracts/specs/dataset-snapshot.md`: territories, population_cells, sample_points, category_ownership, coverage по категориям и сеткам, freight_*, pipeline_connections, reference_edges, minimum_transfers, traffic_profiles.

**Предложение.** Взять за основу v2 модели из `packages/data_pipeline/src/a2_data_pipeline/bundle.py`: `BundleManifest`, `Territory`, `PopulationCell`, `DistrictShare`, `SamplePoint`, `Destination`, `CategoryOwnership`, `CoverageRecord`, `GridSpec`, `Evidence`. Либо подключить ML2 через адаптер `load_bundle(bundle_id) -> BundleData`. Канонический контракт остаётся за BACKEND, менять его ML2 не будет.

**Критерий приёмки:**
- адаптер загружает `kaliningrad-urban-2026-11-08.1`;
- `population=null` доходит до DTO как `null`;
- объекты со `status=quarantined` не попадают в расчёт;
- `coverage.status=unknown|not_collected` даёт `DATA_UNAVAILABLE`, а не `NO_FEASIBLE_ROUTE`.

Отдельно: в handoff BACKEND первый регион — Ленинградская область, а по PRODUCT_SPEC это Москва, Калининград и Новороссийск.

### ML1 (№4)

Для городского пассажирского индекса уже есть:
- население исходных ячеек со статусом, методом и годом, плюс доли районов;
- точки выборки;
- площадь административной границы с `boundary_version` (для H);
- объекты категорий: использовать только `status=accepted`.

**Не 0, а unknown.** Индекс категории с `coverage.status=unknown` (5 публичных категорий, пока нет реестра) нужно считать unknown. Для partial-категорий (вокзалы, автовокзалы, ТЦ) отсутствие найденного объекта не доказывает недостижимость.

**Чего ещё нет:** входов на граф (`access_nodes`) и расписаний ОТ. Следующий шаг ML2 — расписания gortrans39 Калининграда в trip/stop_time/calendar.

### DEVOPS (№2)

**Где.** `packages/data_pipeline/pyproject.toml`, `uv.lock`.

**Что добавить:**
- в dependencies: `pydantic>=2,<3`, `numpy>=2,<3`, `shapely>=2,<3`, `pyproj>=3.6,<4`, `rasterio>=1.4,<2`, `osmium>=4,<5`;
- в группу dev, по желанию: `types-shapely`.

Проверено оверлеем, версии на 08.11: shapely 2.2.0, pyproj 3.8.0, rasterio 1.5.2 (GDAL 3.12.2), osmium 4.x. Колёса есть для macOS arm64 и Linux, Python 3.12.

**Критерий приёмки:**
- `uv sync --frozen --all-packages --group dev` ставит пакеты;
- `PYTHONPATH=packages/data_pipeline/src pytest packages/data_pipeline/tests` проходит 40 тестов без skip.

Отдельно: CI сейчас не запускает pytest пакетов.
