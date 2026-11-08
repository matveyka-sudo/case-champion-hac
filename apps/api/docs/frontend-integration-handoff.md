# Waydex → API v2: передача FRONTEND

Основание: PRODUCT_SPEC 2026-11-08.1, ASSISTANT, IMPLEMENTATION; текущий API `a2_api.api_v2`, `contracts/generated/openapi.json`. Ветки для чтения: `origin/dev=c783435`, `origin/work/frontend=cddfa34`; проверены README, AtlasPage, AgentChat, FiltersPanel, gateway, state, model. Эти файлы не изменялись. Дизайн остаётся прежним; требуется frontend gateway и привязка существующих widgets к DTO.

## 1. Последовательность

1. Получить `/api/v1/capabilities`, сохранить cookie, затем `/api/v1/territories?level=city` или `region`. Readiness contract не означает calculation readiness. Даты snapshot — inventory, не обещание покрытия любого режима.
2. Выбрать реальные IDs/профиль/режим; сохранить draft/applied и revision guard. Нажатие «Рассчитать» отправляет `POST /analyses`; изменение ползунка само анализ не запускает.
3. Сохранить job_id, опрашивать `/jobs/{job_id}` через `poll_after_seconds` (сейчас 2). По succeeded получить `analysis_id` и отдельно `/analyses/{analysis_id}`. По failed показать безопасную ошибку; cancelled — отдельное UI-состояние.
4. Карта/карточка используют saved Metric/Score/subjects/map_data/journeys. Чат отправляет вопрос только с этим saved analysis_id. По сценарию сначала читать `/analyses/{id}/scenarios`, затем запускать выбранный server scenario_id отдельной кнопкой.
5. Scenario run имеет собственный job, после succeeded — `scenario_run_id` и `/scenario-runs/{id}`. Baseline не перезаписывать; base/scenario view сохранять раздельно.

Все девять маршрутов задачи и `POST /jobs/{id}/cancel` реализованы. Новый chat/map функционал добавлен в существующие DTO/маршруты; дополнительных endpoints нет.

## 2. Точная таблица UI → API

| Waydex (`Params`) | API v2 | Правило |
| --- | --- | --- |
| `mode=city` | `analysis_kind=urban_passenger` | `city_id` из принятого каталога |
| `mode=pax` | `intercity_passenger` | Направленная пара `origin_id/destination_id` |
| `mode=cargo,cargoScale=city` | `urban_cargo` | Требует выбранного `city_id`; старый hardcode Новороссийска не становится API-правилом |
| `mode=cargo,cargoScale=inter` | `intercity_cargo` | delivery/multimodal и pipeline — отдельные запуски |
| `travel=pt/walk/car` | `passenger_mode=public_transport/walk/private_car` | Режимы не усреднять |
| `paxModes air/rail/bus/car/sea` | `passenger_modes air/rail/road/private_car/water` | Список выбранных режимов; автомобиль показывать отдельно; mixed — подтверждённые смешанные цепочки |
| `paxScale=city/subj` | `territory_level=city/region` | Для subj нужен region ID; пассажирский результат подписать «через столицы субъектов» |
| `cargoModes road/rail/air/water` | `cargo_modes` с теми же значениями | `cargo_transport=multimodal`; pipe сюда не включать |
| `cargoModes.pipe` | `cargo_transport=pipeline`, `cargo_modes=[pipeline]` | Отдельный intercity_cargo request; не транспортный delivery score |
| `pipeProduct oil/oilprod/gas` | `pipeline_product oil/oil_products/gas` | Профиль должен иметь тот же pipeline product |
| `cargoDir out/in` | `direction outbound/inbound` | Поле urban_cargo, направления независимы |
| `cats[cXX]=1/2/3` | `priorities[category]=low/medium/high` | `categories` — непустой список включённых; priorities ровно по нему |
| `profile.group food/nonfood` | Поле зарегистрированного delivery profile | Отправлять `cargo_profile_id` из capabilities, не создавать профиль в браузере |
| `profile.group=raw` | Прямого соответствия нет | Выбор принятого профиля, без автоматического raw→nonfood |
| `handling container/bulk/liquid/general`, `temp/perish/urgent` | Совокупность полей принятого профиля | Boolean temp не задаёт диапазон; food не означает urgent; не угадывать совместимость |
| Нет traffic control | Явный `traffic_mode=static`, если поддержан | Другие режимы только по capabilities/новому явному выбору; никакого silent fallback |
| `date`, `t∈[0,95]` | `departure_at` ISO8601 с offset | `hour=floor(t/4)`, minute=`(t%4)*15`, интерпретировать в IANA-зоне origin |
| Выбранный район/округ | `area_id` для urban | Только из `capabilities.areas` и для выбранного city_id; пустой список означает отсутствие accepted area inventory |
| `metric`, layers, theme, zoom | Локальные presentation параметры | Не добавлять в расчётный request; zoom не перестраивает сетку |
| selection `cell/obj/wh/node` | chat `selection.entity_kind/entity_id` | cell→cell, obj→destination, wh→freight_site, node→freight_node/access_node по фактическому feature |

Девять категорий (порядок проверен по demo index):

| UI | Canonical category |
| --- | --- |
| c01 | adult_polyclinic |
| c02 | child_polyclinic |
| c03 | kindergarten |
| c04 | school |
| c05 | college_university |
| c06 | rail_terminal |
| c07 | bus_terminal |
| c08 | airport |
| c09 | shopping_center |

`msk/kgd/nvr/spb/kzn/ekb/nsk/vvo` — mock IDs. Production ID не определяется из этих строк или названия: сохранять ID выбранного `/territories` option. Для city и region это разные записи, даже если demo использует один ключ. Число записей национального каталога не ограничивается тремя urban-городами. Catalog сообщает `timezone`, `parent_region_id`, `capital_city_id`.

Не приклеивать `Z` к local time. Moscow/Novorossiysk обычно отображаются в Europe/Moscow, Kaliningrad — Europe/Kaliningrad, но runtime берёт IANA zone из каталога. Смещение рассчитывается для выбранной даты с корректной обработкой ambiguous/nonexistent local time. Дату/время назначения отображать в его зоне. Demo baseline 10.11.2026 08:30 и жёсткий ноябрьский gate из state.ts не являются production-ограничениями.

## 3. HTTP, cookie, idempotency и polling

Работать через same-origin `/api/v1` (Vite/reverse proxy на стороне FRONTEND/DevOps). Приложение не включает wildcard credentialed CORS. `fetch(...,{credentials:'same-origin'})`; сначала дождаться capabilities, затем параллельных мутаций. Cross-origin POST отклоняется. Cookie `a2_session` — opaque HttpOnly/SameSite=Strict; JavaScript её не читает, не хранит в localStorage и не превращает в user ID. На HTTPS оператор включает Secure. Потеря cookie означает потерю доступа к прежней анонимной сессии; неизвестный и чужой ID дают одинаковый 404.

`Idempotency-Key` обязателен для POST analyses/scenario runs. Сгенерировать один UUID на логическое нажатие, повторять **тот же ключ с тем же payload** при сетевом retry. Новый payload/новое нажатие — новый ключ; несовпадающий payload под старым ключом →409. Не генерировать новый ключ на каждый retry. Explanation — scoped read-only POST без idempotency key и без записи/запуска сценария.

Poll queued/running, stop terminal succeeded/failed/cancelled. Жёсткой гарантии duration/процентов progress нет. UI может предложить «открыть позже» через 90 секунд, сохранив job_id. После смены revision игнорировать поздний ответ; AbortController отменяет клиентское ожидание, **не durable job**. Для прекращения вычисления отдельно отправить `POST /api/v1/jobs/{job_id}/cancel`. Если публикация успела раньше отмены, отмена возвращает terminal succeeded; UI использует фактический ответ.

Примеры ниже синтетические. Нужен явно настроенный test adapter/catalog; IDs `city-1` и значения не production-данные. В обычном unconfigured runtime capabilities работает, расчёт возвращает 503.

```http
GET /api/v1/capabilities
GET /api/v1/territories?level=city&query=Москва
```

Фрагмент принятого синтетического каталога:
```json
[{"territory_id":"city-1","level":"city","name":"Москва","timezone":"Europe/Moscow","parent_region_id":null,"capital_city_id":null}]
```

```http
POST /api/v1/analyses
Content-Type: application/json
Idempotency-Key: 63cf909f-6c27-40a3-8559-73b96b84c314
```
```json
{"schema_version":"2","analysis_kind":"urban_passenger","city_id":"city-1","departure_at":"2026-11-09T08:15:00+03:00","traffic_mode":"static","passenger_mode":"walk","categories":["school"],"priorities":{"school":"high"}}
```
```json
{"job_id":"<job_id>","status":"queued","request_id":"<request_id>","poll_after_seconds":2}
```

```http
GET /api/v1/jobs/<job_id>
```
Пример полного job response после успеха:
```json
{"job_id":"<job_id>","status":"succeeded","request_id":"<submission_request_id>","created_at":"2026-11-08T12:00:00Z","started_at":"2026-11-08T12:00:01Z","finished_at":"2026-11-08T12:00:02Z","analysis_id":"<analysis_id>","scenario_run_id":null,"error":null,"poll_after_seconds":2}
```

```http
GET /api/v1/analyses/<analysis_id>
POST /api/v1/jobs/<job_id>/cancel
```

Result содержит normalized request, snapshot/config/methodology/graph/calendar/population/code versions, `result_hash`, metrics/scores/evidence/journeys/counts/references/last_returns и optional map_data. Точная полная форма — OpenAPI; backend возвращает численные значения, браузер только преобразует единицы и форматирует. `result_hash` — серверный hash сохранённого результата, не пересчитывать из произвольно пересобранного view model.

## 4. Статусы и ошибки

| API | UI adaptation |
| --- | --- |
| queued/running | run |
| result available / partial / unknown | ok / partial / nd |
| metric pending + dependency_job_id | Дополнительный показатель вычисляется; не скрывать готовый H-score |
| methodology_pending | Отличать от pending; текущая v3 утверждена, старая подпись «формула ожидает утверждения» не универсальна |
| NO_FEASIBLE_ROUTE с полным scope/evidence | noroute только для подтверждённого соответствующего показателя |
| provider_unavailable / engine_unavailable / typed source errors | srcerr, сохранить предыдущий result; не ноль и не отсутствие связи |
| incompatible snapshot/date | baddate только при явной причине/покрытии; не по demo-календарю |
| explanation template + reason_code | Безопасный fallback/agentoff, численные результаты доступны |
| 404 | ID неизвестен/недоступен; не пытаться получить другую сессию |
| 409 | Idempotency conflict или несовместимые версии; не бесконечный retry |
| 422 | Привязать field_errors к input, сохранить draft |
| 429, retryable 503 | Ограниченный retry с backoff и тем же idempotency key |

Ошибки содержат только безопасные поля:
```json
{"error":{"code":"invalid_request","message":"Request validation failed","field_errors":[{"location":["body","urban_passenger","categories"],"code":"too_short","message":"Invalid request field"}],"request_id":"<request_id>","retryable":false}}
```
`X-Request-ID` относится к текущему HTTP-запросу. Job.request_id/error.request_id относится к созданию задания; это разные допустимые correlation IDs. Не показывать raw provider body/ключи/stacktrace.

Полный территориальный рейтинг допустим только для `ranking_eligible=true` и сопоставимых full данных. Не использовать старые frontend aggregate/cellScore для production: исключение unknown из знаменателя даёт ложный полный результат. Null не преобразовывать в 0; pending/unknown/partial отображать отдельно. Не обещать полную сеть по metadata accepted snapshot.

## 5. Карта и карточки

Минимальное расширение существующего AnalysisResult — optional `map_data`. Оно сохраняется вместе с immutable result и наследует его scope. Отсутствующий map_data интерпретируется как unknown/partial/MAP_DATA_UNAVAILABLE; его отсутствие сохраняет исторический wire/hash. Новые готовые geometry-данные должен передать trusted adapter из accepted источников ML2.

Форма фрагмента (явно synthetic):
```json
{"map_data":{"status":"available","completeness":"partial","reason_code":"SYNTHETIC_ONLY","features":[{"entity_kind":"sample_point","entity_id":"demo-point","territory_id":"city-1","cell_id":"synthetic-cell","point_id":null,"categories":[],"name":null,"geometry":{"type":"Point","coordinates":[37.005,55.001]},"geometry_kind":"actual","geometry_ref":null,"reason_code":null,"population":null,"population_year":null,"population_method":null,"source_refs":["e-1"]}]}}
```
Это иллюстративный фрагмент массива: linked cell feature также присутствует в полном payload. Стабильный ключ — `(entity_kind, entity_id)`, не имя, индекс массива или mock ID. `cell_id` связывает sample point с исходной ячейкой; `point_id` может связать ячейку с sample point. Cell population относится к исходной ячейке; null — неизвестно, ноль — подтверждённый ноль. Известное население содержит год/метод. Territory features/Score.subject.territory_id связывают район/город и агрегаты без среднего районных средних.

GeoJSON всегда **[lon,lat]**, существующий Leaflet view model часто **[lat,lon]**: менять порядок только на границе renderer. Polygon/MultiPolygon приходят от источника; не строить «реальную ячейку» из bbox/dLat/dLon или координаты точки. Неизвестная геометрия: null + geometry_kind=none + ref/reason, карта partial. Схема подписи geometry_kind=schematic обязательна.

Метрики/Score → subject.point_id/site_id/node_id/territory_id; feature.entity_id совпадает с этим ID. Ближайший объект — `JourneyResult.destination_object_id`, исходная точка — `point_id`; маршрут содержит явные legs/durations и actual/schematic/none LineString. Не вычислять nearest по расстоянию и не создавать линию из воздуха. Нет данных для объекта/полигона — честная пустая/partial карта при сохранённом численном результате. Метки/геометрия предприятий, реальные пути и population inventory остаются ML2/ML1 dependency.

## 6. Чат сохранённого анализа

Existing POST принимает optional ExplanationRequest; вызов без тела сохраняет прежнее поведение:
```http
POST /api/v1/analyses/<analysis_id>/explanation
Content-Type: application/json
```
```json
{"schema_version":"2","question":"Почему здесь низкая доступность?","selection":{"entity_kind":"sample_point","entity_id":"demo-point"}}
```
Question — непустой текст до 2000 символов, selection optional. Поддержаны cell/sample_point/destination/freight_site/freight_node/access_node/territory. ID должен принадлежать сохранённым features/subjects/journeys; чужая selection →404 до обращения к модели. Вопрос и selection не запускают анализ/сценарий, не меняют result и передаются модели как untrusted question context. Не передавать персональные данные/секреты в чат; не сериализовать весь raw frontend state.

Ответ `Explanation`: `analysis_id`, `analysis_result_hash`, `request_id`, `status=generated|template`, `model_id`, `reason_code`, `findings`, `proposals`, `evidence_refs`, `limitations`, `retrieved_evidence`. Finding ссылается на `metric_ids` и содержит exact numeric claims; числа рендерить из saved Metric, не парсить произвольную прозу. Ссылка finding→metric→subject позволяет подсветить существующий объект карты; несколько subjects — несколько доступных целей, не новый геопоиск. Raw HTML не вставлять.

Fallback пример (фрагмент):
```json
{"status":"template","model_id":null,"reason_code":"DISABLED","analysis_id":"<analysis_id>","analysis_result_hash":"<result_hash>","findings":[{"text":"Synthetic elapsed travel time","metric_ids":["demo-time"],"evidence_refs":["e-1"],"claims":[{"metric_id":"demo-time","value":1800.0,"unit":"seconds"}]}],"proposals":[],"retrieved_evidence":[]}
```
Generated qualitative text ограничен серверными формулировками; свободный multi-turn диалог с памятью не реализован и не нужен для read-only объяснения сохранённого анализа. История беседы остаётся UI; каждый вопрос явно связан с analysis_id/revision. API не делает платных вызовов автоматически без бюджета/ключей/guard; ни subscription, ни статус generated в fake-тестах не доказывают live capability.

## 7. Сценарии

```http
GET /api/v1/analyses/<analysis_id>/scenarios
```
Ответ — массив ScenarioProposal; пустой массив допустим. Пример полей: `scenario_id`, `template_id`, `operation_type`, `fixed_parameters`, `baseline_analysis_id`, `baseline_result_hash`, versions, feasibility, assumptions/limitations/evidence. Не выводить кнопку по hardcoded Moscow/PT/c01; использовать полученные proposals. `editable_local_graph=false` запрещает run, unverified feasibility требует подписи «модельный эксперимент, реализуемость не подтверждена».

```http
POST /api/v1/scenarios/<scenario_id>/runs
Content-Type: application/json
Idempotency-Key: af568b98-47ca-4846-9bcf-71306ba48aca
```
```json
{"schema_version":"2"}
```
Ответ — 202 JobAccepted; polling аналогичен анализу. По succeeded:
```http
GET /api/v1/scenario-runs/<scenario_run_id>
```
ScenarioResult содержит before/after metrics/scores, deltas с единицами/sign_convention, ухудшения, affected_population либо affected_sites, feasibility, overlay graph и hashes. ΔT=before−after, Δscore=after−before; отрицательные эффекты видимы, new_connection не Infinity. Не подменять число жителей пассажиропотоком. Template ID не обязан совпадать со строкой operation_type; сервер назначает scenario ID. Операции/сдвиги/численные обещания из браузера не принимаются.

## 8. Проверено и что должен сделать FRONTEND

На synthetic fixtures проверяется весь API/PG/worker путь, cookie isolation, same-key retries/conflicts, cancel, immutable results/baseline, server proposals/run, scoped chat questions/selections, saved map metadata/unknown population, typed errors и RAG/provider MockTransport. Реальные production-данные, UI↔API браузерный E2E и paid Flash не заявляются проверенными.

FRONTEND: сгенерировать типы из актуального OpenAPI; заменить mockGateway на backend gateway; хранить job/result IDs и DTO в state; сохранить revision guard; заменить mock IDs/catalog/date gates; сопоставлять cargo profiles; разделить pipeline; убрать production расчёты из presentation selectors; подключить карту/карточку к stable features/subjects/journeys; чат к scoped explanation; сценарии к server proposals и run endpoints. Это работа FRONTEND, backend её не редактировал. Нужен same-origin proxy от FRONTEND/DevOps без изменения дизайна.

ML1: routing/reference/scenario callable, real effects и τ scheduler interface. ML2: принятые catalog IDs, snapshots, map geometries/population/object metadata, verified provider mappings и reviewed document chunks. DevOps: оставшееся source packaging, PostGIS/CI, секреты, runtime/service lifecycle; SQLAlchemy asyncio/greenlet исправляется строго отдельно по разрешению пользователя. Владелец API задаёт durable spending policy/лимит; live smoke отдельно после разрешения.

Следующая командная приёмка: один принятый реальный walk/static город → API/worker → existing Waydex gateway → карта/карточка → объяснение → предложенный сценарий. До этого demo-markers снимаются только для действительно принятых данных.

Фактический финальный прогон: clean frozen **560 passed, 0 skipped** с PostgreSQL; Ruff/format 91 файлов, strict mypy 43 source files, schema export --check, uv lock --check, pip check и import greenlet прошли. Командные данные/платный Flash/React браузерный E2E не проверялись.

## Review 09.11.2026: contract changes

`ScenarioDelta.outcome=lost_connection` represents confirmed connection loss with delta=null; unknown does not prove loss or creation. Every prior task remains represented in before/after; journey ID may change. Optional `Score.reference_ids` declares required references; the empty default is omitted from serialization. Full indices require full dependencies and aggregation basis irrespective of ranking eligibility. Preliminary values remain partial.

Historical contradictory results are immutable: reads and cache reuse return safe 409 `stored_result_incompatible` requiring recalculation. Regenerate frontend types from OpenAPI, handle the additive outcome and typed error. Frontend files were not changed.
