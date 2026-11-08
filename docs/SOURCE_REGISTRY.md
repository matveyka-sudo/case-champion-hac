# Реестр всех источников A2
Все строки, отмеченные A2 в списке пользователя, сохранены, включая повторные назначения одного сайта. Ни одному источнику не присваивается фиктивный коэффициент индекса. Сайты без API импортируются как файлы/таблицы/документы с датой и URL; автоматический API не предполагается.
Это назначение источников, не утверждение, что каждый файл уже загружен и проверен. Точные dataset ID, лицензия, период, хэш и QA заполняются в SourceManifest при импорте. До этого status=registered, не accepted.

| ID | Источник | Что получаем | Применение/ограничение |
|---|---|---|---|
| tochno_connectivity | [tochno_connectivity](https://tochno.st/datasets/intercity_connectivity) | Связность: расстояния/расчётные времена | Контроль порядка величин, не расписание |
| tochno_regions_transport | [tochno_regions_transport](https://tochno.st/datasets/regions_collection) | Перевозки, плотность дорог, годовая динамика | Контекст субъекта, не конкретный рейс |
| asop_ports | [asop_ports](https://www.morport.com/rus/content/statistika-0) | Грузооборот и мощности портов | Узел/период; не поток выбранной пары |
| asop_basins | [asop_basins](https://www.morport.com/rus/content/statistika-0) | Грузооборот бассейна и направления | Грузовой контекст, без подмены мощности порта |
| rosmorport | [rosmorport](https://www.rosmorport.ru/services/seaports/) | Характеристики и функции портов | Подтверждение узла и совместимости |
| osm_geofabrik | [osm_geofabrik](https://download.geofabrik.de/russia.html) | Дорожная сеть, здания, вода, POI, транспортные узлы | Граф/выборка; не расписания и не собственность |
| tochno_settlements | [tochno_settlements](https://tochno.st/datasets/allsettlements) | Населённые пункты и население | Справочник и контроль итогов населения |
| tochno_bdmo | [tochno_bdmo](https://tochno.st/datasets/bdmo) | Регулярность сообщения, нормативность муниципальных дорог | Показатели территории; не качество конкретного ребра |
| ourairports | [ourairports](https://ourairports.com/data/) | Координаты, типы, коды аэропортов | Сведение узлов, не доказательство пассажирских рейсов |
| favt_registry | [favt_registry](https://favt.gov.ru/dejatelnost-ajeroporty-i-ajerodromy-reestr-grajdanskih-ajerodromov-rf/) | Гражданские аэродромы | Официальная проверка типа/статуса узла |
| geonames | [geonames](https://download.geonames.org/export/dump/) | Названия и координаты | Разрешение топонимов, не границы и не рейсы |
| tochno_grp | [tochno_grp](https://tochno.st/datasets/regions_collection) | ВРП и его динамика | Контекст экономики; не коэффициент доступности |
| favt_stats | [favt_stats](https://www.favt.gov.ru/opendata/) | Перевозки/показатели аэропортов | Контекст нагрузки при совпадении периода |
| emiss | [emiss](https://www.fedstat.ru/) | Население, доходы, занятость и другие показатели | Выбрать конкретные indicator IDs при ingest; агрегаты не спрос пары |
| rosstat_regions | [rosstat_regions](https://rosstat.gov.ru/regional_statistics) | Социально-экономические показатели субъектов | Контекст и контроль региональных итогов |
| rosstat_demography | [rosstat_demography](https://rosstat.gov.ru/folder/12781) | Население и перепись | Итоги/год для населения, не жители каждого здания |
| fns | [fns](https://www.nalog.gov.ru/opendata) | Юридические лица/ИП | Подтверждение организации, юридический адрес не производственная площадка |
| fias | [fias](https://fias.nalog.ru/) | Адресный реестр | Нормализация адресов; геокодирование отдельным шагом |
| transport_strategy | [transport_strategy](https://government.ru/) | Стратегия: 3363-р от 27.11.2021 | Документальный контекст целей; точный документ фиксируется при ingest |
| government_projects | [government_projects](https://www.government.ru/) | Инфраструктурные проекты | Основания сценария; проектная дорога не действующая |
| budget | [budget](https://budget.gov.ru/) | Бюджетные программы | Контекст реализации проектов, без цены поездки |
| rosinfra | [rosinfra](https://rosinfra.ru/) | ГЧП/концессии | Контекст инфраструктуры; доступность выгрузки проверяет ML2 |
| mnr | [mnr](https://www.mnr.gov.ru/) | Экологические доклады | Контекст ограничений, без выдуманных выбросов конкретной поездки |
| meteorf | [meteorf](https://www.meteorf.gov.ru/) | Мониторинг загрязнений | Территориальный экологический контекст, не дорожная скорость |

## Дополнительные технические источники
- Yandex Расписания: серверный API с ключом, узлы/рейсы межгорода; [документация](https://yandex.ru/dev/rasp/doc/ru/). Прямые ответы и поддерживаемые пересадки не гарантируют полный multimodal graph; перенос узлов и допустимость стыковок проверяет engine. Квоты/права кеширования задаются по условиям ключа, не по выдуманному безлимиту.
- Официальные перевозчики/GTFS: маршруты трёх городов, метро и электрички отдельными покрытиями. См. проверенные ограничения TRAFFIC_AND_PROVIDERS.md.
- GHSL/WorldPop: растр постоянного населения; версия/год/метод, разрешение и лицензия в manifest. Это оценка, не посетители объектов.
- isDayOff: производственный календарь; не расписание перевозчика. [Документация](https://isdayoff.ru/).
- Open-Meteo/Росгидромет: погода как контекст; подключать только для соответствующего объяснения, без множителя задержки.
- Операторы трубопроводов/терминалов: конкретные соединения, продукты, допустимость, периоды и операции. Карта трубы без подтверждения не достаточна.
- Quasar: кандидат live ETA; demo-проверка не разрешение production. Нет обязательной платной зависимости от 2ГИС.

## SourceManifest для ML2
source_id, source_url, retrieved_at, publication_date, valid_from/to, geography, license, access_method (api/file/manual), checksum, schema_mapping, coverage, limitations, status (registered/fetched/quarantined/accepted), qa_report.
Статистику разных лет не складывать; хранить единицы и территориальные коды. Контекст в RAG всегда со ссылкой и годом. В pipeline приоритет источника задаётся для поля: официальный реестр статуса учреждения > OSM; календарь оператора > производственный календарь; региональная мощность ≠ мощность направления.
Старые агрегаты допустимы как датированный контекст, не как свежая частота рейсов. Условия хранения/перераспространения проверяются до публикации raw; в git только manifest и допустимые fixtures.
