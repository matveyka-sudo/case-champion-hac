# Передача данных и расчётов BACKEND ↔ ML1 ↔ ML2

Контракт 0.1.0, первый выпуск схем. Ветка `work/backend`, исходная база `d23fb07`. Канонический источник — модели `packages/contracts/src/a2_contracts/`, структурные схемы — `contracts/generated/`. Этот документ предназначен для передачи коллегам участником команды; автоматической отправки сообщений нет.

## Границы ответственности

| Решение/работа | Владелец | Что делает №3 BACKEND |
| --- | --- | --- |
| Регион, полный набор городов, цели и общий объём | №2 с №5 и №4 | Принимает версионированную конфигурацию, проверяет связи и параметры |
| Первый регион: Ленинградская область | Актуальное указание пользователя 07.11 | Поддерживает его через конфигурацию, без списка городов в коде |
| Объекты work/study, услуги, входы, часы, расписания, тарифы, evidence | №5 ML2, принятие с №2 | Описывает DTO, отклоняет некорректные ссылки/типы, показывает unknown |
| Правила профилей, веса и сценарные изменения транспорта | №2 с №4; данные проверяет №5 | Валидирует запрос, версии и лимиты, передаёт в engine |
| Поиск, транспортная выполнимость, три оценки, пересчёт | №4 ML1 | Вызывает публичные функции через адаптер/worker, не дублирует формулы |
| API, jobs, канонические схемы, tools, агент и журнал | №3 BACKEND | Реализует и тестирует |

Согласованный перечень городов Ленобласти в проверенных локальных материалах не найден. В старом CSV перечислены города Калининградской области; использовать их для нового scope запрещено. «Минимум три территории» — приёмочный демонстрационный минимум, а не ограничение охвата первого региона. Backend не должен самостоятельно выбирать реальные учреждения, тип медицинской помощи или сценарий изменения расписания.

## Региональная конфигурация

`CatalogConfig` загружается через `a2_api.configuration.load_catalog(path)` из JSON. Содержит `study_areas`, `profiles`, `task_sets`. `A2_CATALOG_PATH` выбирает файл для фабрики `a2_api.app:create_configured_app`.

- `StudyArea`: `area_id`, положительный integer `area_version`, `region_ids`, полный `city_ids`, отдельный `demo_city_ids`, `timezone`, `basis`, `evidence_ids`.
- `demo_city_ids ⊆ city_ids`, дубли запрещены, верхнего ограничения на число городов нет. Демонстрационный поднабор не подменяет полный.
- `ProfileConfig.study_area_id/study_area_version` связывают профиль с областью. Версии профиля, области и набора данных независимы от `/api/v1`.
- Другой регион получает новую конфигурацию и принятый bundle. Имена городов/услуг не входят в алгоритмы backend. IDs должны быть глобально различимыми или namespaced. Межрегиональные связи сохраняются явными trips/transfers; граница региона сама их не создаёт и не удаляет.

`apps/api/examples/catalog.synthetic.json` — только учебная конфигурация с `demo-*` и четырьмя вымышленными городами. Её нельзя принимать за географический охват или evidence. Реальная конфигурация появится после передачи решений №2/№5; отсутствие конфигурации не запускает учебную автоматически.

## От №5 ML2: AcceptedData и evidence

Публичная функция:

```python
from a2_contracts import AcceptedData

def load_bundle(bundle_id: str) -> AcceptedData:
    ...
```

№5 реализует её в `packages/data_pipeline`, выполняет QA, сохраняет raw и публикует immutable bundle. Запросы API не должны сами скачивать и «принимать» источники. При несуществующем ID — типизированная ошибка `unknown_id`, при непринятой/неподходящей версии — явная ошибка, без автоматического исправления.

| Вход | Обязательное содержание |
| --- | --- |
| `sources`, `evidence` | Источник/издатель/URL/право использования; ID записи, fetched UTC, отдельно период действия, hash, состояние проверки |
| `bundle: SnapshotBundle` | Версии timetable/destinations/transfers и nullable fares, hashes таблиц, parser version, acceptance state; при accepted — кто и когда принял |
| coverage/completeness | Конкретные origin/destination IDs, режимы, даты, `is_complete`, missing fields и evidence полноты. Accepted не означает complete |
| `origins`, `destinations` | Собственные ID, city ID, anchor; lon/lat EPSG:4326, IANA timezone, тип, evidence. У цели — entrance ID и проверка |
| `service_offerings` | destination ID, service type ID, применимость, период, раздельные `service_verified` и `hours_verified`, evidence |
| `availability_windows` | Локальные start/end, end_day_offset 0/1, weekdays Monday=0, период и зона, verification state и evidence. Это часы услуги, не время условного визита пассажира |
| `applicability` | nullable passenger categories / requires_referral, ограничения и evidence. `null` означает неизвестность, а не отсутствие ограничений |
| `stops`, `trips`, `stop_times` | Namespaced ID, station/parent, operator/mode/service; sequence, arrival/departure seconds, pickup/dropoff. Секунды могут превышать 86400 |
| `calendars` | Сервисные даты, weekdays и add/remove exceptions, зона и evidence. Неизвестный valid_to не означает бессрочность |
| `transfers` | Направленные from/to, seconds, access state, basis, kind access/transfer/egress, ограничения и evidence |
| `fares` | Если известны: amount, currency, passenger category, даты, trip IDs, evidence. Пустые тарифы не равны бесплатному проезду |

`AcceptedData` проверяет уникальность ключей, внешние ссылки, соответствие service_id, последовательность событий и наличие явного accepted bundle. Он не доказывает достоверность записи и не заменяет предметный QA №5. `hours_verified=verified` требует фактические verified окна с evidence. Отсутствующие услуги/часы/тарифы не заполняются догадками.

Минимальная передача для интеграции: небольшой реальный принятый bundle выбранного региона, хотя бы один подтверждённый маршрут туда/обратно, один неполный случай, зарегистрированные evidence, отчёт QA и команда воспроизведения. Это интеграционный срез, а не автоматическое сокращение полного scope.

Приёмка №5: JSON проходит `AcceptedData.model_validate_json`; неизвестный FK отклоняется; ночное время сохраняется; отсутствие подтверждения дат/услуг/подходов остаётся видимым; source records восстанавливаются по evidence. Публичная публикация материалов требует проверки прав владельцем источников.

## От №4 ML1: три публичные функции

```python
from a2_contracts import (
    AcceptedData, Comparison, JourneyRequest, JourneyResult,
    ProfileConfig, ProfileResult, TaskSet,
)
from a2_contracts.scenarios import Operation

def find_activity_journeys(data: AcceptedData, request: JourneyRequest) -> JourneyResult:
    ...

def evaluate_cases(data: AcceptedData, profile: ProfileConfig, task_set: TaskSet) -> ProfileResult:
    ...

def compare_scenario(
    data: AcceptedData,
    baseline_runs: dict[str, ProfileResult],
    operations: list[Operation],
) -> Comparison:
    ...
```

Это договорённость о сигнатурах для согласования, а не утверждение, что функции уже существуют в ветке ML1. Передать имя импортируемого модуля, commit и реальные команды запуска/проверки. CPU-функции исполняются вне async HTTP/event loop; долгий анализ позже запускается одним worker. Внутри engine нет LLM, скачивания источников или записи в HTTP/БД.

- `JourneyRequest` содержит bundle/profile versions, origin, ровно одну цель, сервисную дату, earliest_departure, при деятельности — окна/буферы/возврат/basis, эффективные лимиты, budget и step-free. В первом контракте medical использует `service_type_id`, regular/intercity — `destination_id`; выбор учреждения без явной услуги не угадывается.
- ML1 проверяет применимость календаря, альтернативы по активным ограничениям, подходы и возврат после деятельности. Город и вокзал не отождествляются. Не найденный путь в неполной сети не означает fail.
- `JourneyResult`: status, routes, constraints, overall feasibility, gaps/reasons, assumptions/evidence, provenance и optimality. Pass требует пути и всех активных pass; fail требует полной принятой области и завершённого поиска. Unknown/ошибка не превращаются в ноль.
- `ProfileResult`: run/bundle/profile/task/code/params/result versions и hashes, provenance, единицы, отдельные города с score/lower/upper, task_count и resolved share. Точечный score допустим только при точном равенстве границ до округления. Insufficient_data содержит причину/пропуски и не содержит выдуманных оценок.
- `Comparison`: одна immutable база, совпадающие bundle и профильные/task/code versions, результаты по каждому запрошенному профилю, before/after и проверяемые разности/консервативный интервал; выигрыши, ухудшения и unresolved отдельно. Если база insufficient_data — причины и nullable результат этого профиля, сохранённые версии/hash/base_run_id.
- Вернуть типизированную доменную ошибку при техническом сбое, не пустой список и не fail задачи. BACKEND переводит её в HTTP через `DomainError`; пакет engine не зависит от FastAPI или `a2_api`.

Сценарные операции: `shift_trip(delta_seconds)` до ±1800, сервисные даты обязательны; `add_trip(new_trip_id)` — принятый template со сдвигом либо полная цепочка остановок, маршрут, service и mode. Не больше 10 операций. Новый ID обязателен даже там, где сокращённый PDF-пример его опускает. Контекстные ID/даты/версии проверяются сервисом, транспортные эффекты — ML1; эксплуатационная проверка пока `not_verified`.

Приёмка №4: по одному синтетическому/реальному примеру подтверждённого, неполного и невозможного в полной модели исхода; одинаковый ответ через REST/tools; детерминированный результат при одинаковых входах; два сценария и проверка неизменности базы. Три профиля не усредняются. Числа из fixtures не выдаются за результаты региона.

## Единый формат ошибок и согласованные инженерные решения

HTTP envelope: `error.code`, `message`, `field_errors[{location,code,message}]`, `retryable`, `trace_id`. Ошибки валидации не включают исходный пользовательский input. Ответ содержит `X-Trace-ID`.

| HTTP | code |
| --- | --- |
| 422 | invalid_request |
| 404 | unknown_id |
| 409 | incompatible_versions / idempotency_conflict |
| 429 | budget_exceeded |
| 503 | engine_unavailable |
| 500 | internal_error |

Решения BACKEND, которые нужно передать потребителям до принятия схем: положительный integer `profile_version`; машинные `metric_kind=activity_feasibility|time_decay`; `delta_seconds` у shift; `new_trip_id` у add; medical выбирает service type; region scope вынесен в StudyArea; `ServiceOffering` получил структурные окна/применимость. Это предложения версии 0.1.0 для интеграции, а не молчаливое изменение чужих веток.

Публичные времена ISO 8601 со смещением, сравнения конкретных событий в UTC, local times с IANA timezone. Hash строится из нормализованного `model_dump(mode="json")`, сортировки ключей и компактного JSON UTF-8 без NaN/Infinity; порядок списков/операций сохраняется. `result_hash` исключается из собственного вычисления. Сам алгоритм hash доступен как `canonical_hash`; ответственность за каноническую форму входа остаётся у вызывающего.

## Для FRONTEND и DEVOPS

FRONTEND получает `openapi.json`, три synthetic fixtures и методы каталога. `POST analyses/scenario-analyses` требует Idempotency-Key, контракт отвечает 202 JobAccepted; на этом этапе не настроенный сервис возвращает 503. После подключения worker polling GET jobs каждые 2 секунды, после 90 секунд UI предлагает открыть позже, terminal states succeeded/failed/cancelled. У succeeded обязателен run_id, у failed — error. Не изображать 503 как успешный пустой результат.

DEVOPS получает src-layout, `py.typed`, API factory и команды из `apps/api/README.md`. Все manifests/lock остаются у DEVOPS. Платный API и бюджет агента ещё не проверены; этот выпуск не обращается к модели и не закрывает F07. PostgreSQL/jobs/worker и их миграции — следующий технический этап; контракт Job не означает наличие работающего worker.
