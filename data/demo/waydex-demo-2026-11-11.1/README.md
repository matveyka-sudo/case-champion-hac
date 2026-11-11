# waydex-demo-2026-11-11.1 — синтетический демо-набор (МОДЕЛЬ)

Отдельный versioned набор для демо-стенда. Это **модельные данные**: реальные только узлы, координаты, население и
наблюдения, перечисленные ниже. Рейтингом городов это не является (`NOT_A_REAL_CITY_RANKING`).

- Срез: 2026-11-11 08:30 по местному времени — Калининград 2026-11-11T06:30:00Z, Москва и Новороссийск 2026-11-11T05:30:00Z.
  Другие даты, время и режимы в набор не входят: `DATE_OR_TIME_OUTSIDE_DEMO_SLICE`.
- Генератор: `python -m a2_data_pipeline.demo_dataset build`, seed 20261111, правила `waydex-demo-rules-v1`. Повторный запуск даёт те же байты.
- Метка синтетики: источник `DEMO_SYNTHETIC_TRANSPORT_MODEL` в `qa.checks`, `source_versions` и evidence каждого модельного ряда.
- Матрица: 24 базовых пресетов и 21 сравнений до/после; трубопровод — отдельный профиль `demo-novorossiysk-pipeline`, не входит в индекс доставки.
- QA: все проверки пройдены (21 проверок, `qa.json`).

## Что реальное, что модельное

| Город | Реальные входы | Модель | Что запускается на проверенной поставке |
| --- | --- | --- | --- |
| moscow | снимок `moscow-urban-2026-11-10.4`: 6 точек старта с населением GHSL, 18 объектов категорий, остановки (mos_ground_transport GTFS stops (transit_stops.json)), 3 грузовые площадки | 0 модельных объектов, времена пешком/авто, 2 маршрута ОТ с рейсами, спрос/ресурс/оборот, грузовые узлы-модели и обработка | moscow-real-adult-clinic-transit-nov11-v1 (public_transport, 1 кат.); moscow-observed-all-categories-20261111-v2 (walk, 9 кат.); грузовик: площадки без узлов назначения |
| kaliningrad | снимок `kaliningrad-urban-2026-11-10.5`: 6 точек старта с населением GHSL, 17 объектов категорий, остановки (gortrans39 stop lists (stop_frequencies.json)), 3 грузовые площадки, порт и наблюдения грузовика | 1 модельных объектов, времена пешком/авто, 2 маршрута ОТ с рейсами, спрос/ресурс/оборот, грузовые узлы-модели и обработка | canonical-access-airport-2026-11-10 (walk, 1 кат.); kaliningrad-observed-categories-20261111-v1 (walk, 8 кат.); грузовик: да |
| novorossiysk | снимок `novorossiysk-urban-2026-11-10.3`: 6 точек старта с населением GHSL, 15 объектов категорий, остановки (real walk-network projections (no accepted stop table)), 3 грузовые площадки, порт и наблюдения грузовика | 3 модельных объектов, времена пешком/авто, 2 маршрута ОТ с рейсами, спрос/ресурс/оборот, грузовые узлы-модели и обработка | novorossiysk-observed-categories-20261111-v1 (walk, 8 кат.); грузовик: да |

Межгород: узлы из каталога `russia-intercity-nodes-2026-11-09.2` (реальные), дорожные наблюдения Valhalla freeflow для 4 направлений (реальные),
Калининград↔Новороссийск — наблюдения нет (лимит провайдера 1500 км), показывается причина. Все расписания самолётов, поездов, автобусов,
грузовые цепочки и трубопровод — модель. Таблица применимости: `tables/intercity/applicability.json`, сводка: `coverage.md`.

## Файлы

- `manifest.json` — для Искандера: 3 города, 6 пар, снимки (id, sha256, content hash), receipts, 24 пресета, входы 21 сравнения, реальные версии и суммы.
- `snapshots/*.json` — AcceptedData v2 (`demo-<город>-2026-11-11.1`, `demo-intercity-…`, `demo-pipeline-…`).
- `receipts/*-transport.json` — `waydex-demo-closed-transport/1`: закрытая модель расписания (рейсы, события, подходы, пересадки) с evidence на каждый ряд.
- `tables/<город>/*.csv`, `tables/intercity/*`, `control/*.json` — для Матвея: исходные таблицы и контрольные входы/ожидаемые значения обеих операций.
- `presets.json` — для Марии: пресеты, которые будет отдавать API (подписи, дата/время, операции, ограничения).
- `coverage.csv` / `coverage.md` — наличие по каждому городу и направлению; `fields.json` — генератор, seed, единица и шаблон каждого поля.

## Ограничения для API

- DemoTransportReceipt and demo map/scenario guards accept only snapshot ids starting with demo-moscow-; demo-kaliningrad- and demo-novorossiysk- need the same opt-in (BACKEND)
- scenarios v2 operation_type is coordinate_transfer | additional_departure for urban public transport; intercity passenger and cargo operations need a contract and engine change
- A2_DEMO_TRANSPORT_ENABLED=true on a separately labelled stand only

## Покрытие (модельное)

| Территория / направление | Вкладка | Режим | Статус | Реальное | Модельное | Примечание |
| --- | --- | --- | --- | --- | --- | --- |
| moscow | in_city | walk | model | 6 origins (GHSL cells), 18 destinations | 0 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| moscow | in_city | private_car | model | same origins/destinations | 108 edge times, 24.0 km/h | no traffic data; static |
| moscow | in_city | public_transport | model | stop positions: mos_ground_transport GTFS stops (transit_stops.json) | 2 routes, 40 trips, 42 footpaths, 2 transfer rules | OD reached 65/108 |
| moscow | cargo_in_city | road | model | 3 sites | logistics_terminal node, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| moscow | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_PROFILE |
| kaliningrad | in_city | walk | model | 6 origins (GHSL cells), 17 destinations | 1 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| kaliningrad | in_city | private_car | model | same origins/destinations | 108 edge times, 30.0 km/h | no traffic data; static |
| kaliningrad | in_city | public_transport | model | stop positions: gortrans39 stop lists (stop_frequencies.json) | 2 routes, 40 trips, 46 footpaths, 2 transfer rules | OD reached 80/108 |
| kaliningrad | cargo_in_city | road | real+model | 3 sites, port location, truck observations | port operations, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| kaliningrad | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_PROFILE |
| novorossiysk | in_city | walk | model | 6 origins (GHSL cells), 15 destinations | 3 destinations, 108 edge times | speed 5 km/h from the accepted walking profile |
| novorossiysk | in_city | private_car | model | same origins/destinations | 108 edge times, 30.0 km/h | no traffic data; static |
| novorossiysk | in_city | public_transport | model | stop positions: real walk-network projections (no accepted stop table) | 2 routes, 41 trips, 42 footpaths, 2 transfer rules | OD reached 68/108 |
| novorossiysk | cargo_in_city | road | real+model | 3 sites, port location, truck observations | port operations, rail_terminal node, handling, resource | truck-model-20t-container-nonfood-ordinary |
| novorossiysk | cargo_between | pipeline | model | pipeline terminal locations (OSM) | connections, direction, capacity/volume per day | separate profile, not in the delivery index |
| moscow→kaliningrad | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→kaliningrad | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→kaliningrad | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| moscow→kaliningrad | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| moscow→kaliningrad | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| moscow→kaliningrad | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| moscow→kaliningrad | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| moscow→kaliningrad | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| moscow→kaliningrad | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| moscow→kaliningrad | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_LINK |
| kaliningrad→moscow | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→moscow | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→moscow | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| kaliningrad→moscow | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| kaliningrad→moscow | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| kaliningrad→moscow | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| kaliningrad→moscow | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| kaliningrad→moscow | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| kaliningrad→moscow | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| kaliningrad→moscow | cargo_between | pipeline | not_applicable |  |  | NO_PIPELINE_LINK |
| moscow→novorossiysk | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| moscow→novorossiysk | passenger_links | road_bus | model_schedule | nodes (intercity directory) | schedule/chain |  |
| moscow→novorossiysk | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| moscow→novorossiysk | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| moscow→novorossiysk | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| moscow→novorossiysk | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| moscow→novorossiysk | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| moscow→novorossiysk | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| novorossiysk→moscow | passenger_links | air | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | rail | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | road_car | real_observation | Valhalla freeflow observation |  |  |
| novorossiysk→moscow | passenger_links | road_bus | model_schedule | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→moscow | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| novorossiysk→moscow | cargo_between | road | not_applicable |  |  | LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN |
| novorossiysk→moscow | cargo_between | rail | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| novorossiysk→moscow | cargo_between | water | not_applicable |  |  | NO_WATER_LINK_MODELLED |
| novorossiysk→moscow | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| novorossiysk→moscow | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| kaliningrad→novorossiysk | passenger_links | air | model_schedule_via_moscow | nodes (intercity directory) | schedule/chain |  |
| kaliningrad→novorossiysk | passenger_links | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK_AND_CROSS_TERMINAL_TRANSFER_IN_MOSCOW_NOT_MODELLED |
| kaliningrad→novorossiysk | passenger_links | road_car | ROAD_OBSERVATION_UNAVAILABLE |  |  | 154:Path distance exceeds the max distance limit: 1500000 meters |
| kaliningrad→novorossiysk | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| kaliningrad→novorossiysk | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| kaliningrad→novorossiysk | cargo_between | road | unknown |  |  | ROAD_OBSERVATION_UNAVAILABLE |
| kaliningrad→novorossiysk | cargo_between | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK |
| kaliningrad→novorossiysk | cargo_between | water | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| kaliningrad→novorossiysk | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| kaliningrad→novorossiysk | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
| novorossiysk→kaliningrad | passenger_links | air | model_schedule_via_moscow | nodes (intercity directory) | schedule/chain |  |
| novorossiysk→kaliningrad | passenger_links | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK_AND_CROSS_TERMINAL_TRANSFER_IN_MOSCOW_NOT_MODELLED |
| novorossiysk→kaliningrad | passenger_links | road_car | ROAD_OBSERVATION_UNAVAILABLE |  |  | 154:Path distance exceeds the max distance limit: 1500000 meters |
| novorossiysk→kaliningrad | passenger_links | road_bus | not_applicable |  |  | INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED |
| novorossiysk→kaliningrad | passenger_links | water | not_applicable |  |  | NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES |
| novorossiysk→kaliningrad | cargo_between | road | unknown |  |  | ROAD_OBSERVATION_UNAVAILABLE |
| novorossiysk→kaliningrad | cargo_between | rail | not_applicable |  |  | NO_DIRECT_RAIL_LINK |
| novorossiysk→kaliningrad | cargo_between | water | model_chain | sites and ports (accepted bundles) | schedule/chain |  |
| novorossiysk→kaliningrad | cargo_between | air | not_applicable |  |  | AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE |
| novorossiysk→kaliningrad | cargo_between | pipeline | separate_profile |  |  | demo-novorossiysk-pipeline |
