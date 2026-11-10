# Передача BACKEND ↔ ML1 ↔ ML2

Актуальный контракт: `a2_contracts.v2`, схема `"2"`, методика `a2-methodology-v3`, PRODUCT_SPEC `2026-11-08.1`. Основные источники: `docs/PRODUCT_SPEC.md`, `docs/FORMULAS.md`, `docs/IMPLEMENTATION.md`, `config/product.json`. Исторические профили и окна посещения больше не определяют production API.

BACKEND отвечает за DTO, HTTP, очередь, миграции, доступ, кэш, сценарный allowlist, tools и агент. ML1 реализует формулы и транспортные расчёты; ML2 принимает источники и публикует immutable snapshots с QA. Backend не выбирает реальные учреждения и не назначает значения метрик. Утверждённый городской scope берётся из конфигурации продукта; межгородской каталог расширяется принятыми территориями. Демонстрационный набор не заменяет национальный каталог.

## Участнику №5 — ML2

Передать JSON, проходящий `a2_contracts.v2.dataset.AcceptedData.model_validate_json`. Пример структуры: `packages/contracts/fixtures/v2/accepted-data.json`; он явно синтетический и не является принятым набором реального города.

| Данные | Обязательные сведения |
| --- | --- |
| Snapshot | `snapshot_id`, `source_versions`, geographic scope, закрытый период, IANA timezone, graph/geometry/calendar/population versions |
| QA/evidence | Явный accepted state, кто/когда принял, проверки, evidence IDs, source/version, URL, retrieval time, период, лицензия, hash |
| География | Территории, границы/версии геометрии, здания/точки, сетка 1000 м, население и год/метод; исключённые ячейки сохраняются с причиной |
| Учреждения | Девять опубликованных категорий, подходы/координаты; для образования и поликлиник подтверждённое государственное/муниципальное владение с registry evidence |
| Транспорт | Access nodes, явные сервисные даты после исключений, подтверждённая идентичность рейса, полная цепочка stop times, transfers/minimum transfers |
| Эталон | `reference_edges`: направленные рёбра, режим, геометрия/длина, время и подтверждённая калибровка; без вымышленных скоростей |
| Грузы | Физические площадки и реальные грузовые узлы, ограничения совместимости, handling/vehicle/temperature отдельно; трубопроводные продукты и сопоставимые V/Q отдельно |
| Покрытие | `coverage_by_mode_date_category`, `confirmed_absence_scope`; пустой каталог не является доказательством отсутствия маршрута |
| Трафик | Явный режим, graph/edge/time-bin, тип дня/сезон, период/наблюдения; typical минимум пять сопоставимых дней; live observed/expiry |

Приёмка: уникальные IDs/FK, координаты lon/lat, timezone и aware timestamps, последовательные ночные события с секундами >86400, registry links, один sample на включённую ячейку, явные partial/unknown. Структурная валидация не заменяет геопространственный и предметный QA. Для первого сквозного среза нужны реальные границы/здания/учреждения/население одного утверждённого города и walk/static coverage. Нужны команда воспроизведения, версия данных и отчёт об ограничениях.

Инвентаризация передаётся в `ProductRegistry(territories=..., areas=..., snapshots=..., cargo_profiles=..., ready_kinds=...)`. Имена/IDs территорий не угадываются из frontend aliases. Неизвестная конечная дата не означает бессрочность. Данные для других регионов добавляются через тот же интерфейс. Readiness отдельного вида требует явного разрешения после интеграционных проверок, наличие одного snapshot не включает все виды анализа.

## Участнику №4 — ML1

Расчёт получает `ClaimedJob` с `V2JobContext`:

```python
from a2_api.core.jobs import ClaimedJob, CalculationOutput, V2JobContext
from a2_contracts.v2 import AnalysisResult, ScenarioResult

def calculate(claim: ClaimedJob) -> AnalysisResult | ScenarioResult | CalculationOutput:
    # Trusted BACKEND adapter resolves immutable data by context.snapshot_id/hash.
    # Calls public engine functions, does not depend on FastAPI.
    ...
```

Это интерфейс BACKEND adapter, не требование импортировать `a2_api` внутри engine. ML1 передаёт чистые публичные функции с `AnalysisRequest`, `AcceptedData`, `VersionRefs`, `AnalysisResult`/`ScenarioResult`; BACKEND связывает их с worker. Расчёт выполняется в отдельном процессе, не в HTTP. `A2_CALCULATION_HANDLER` выбирается оператором, никогда пользователем.

Контекст содержит исходный вид анализа через `normalized_request`, resolved snapshot/methodology, snapshot hash, graph/calendar/population/config/code versions, серверный `analysis_id`, scope и при сценарии сохранённый `scenario_proposal`. Все результаты должны точно соответствовать контексту. Backend проверяет соответствие и рассчитывает финальные hashes при атомарной публикации. Не менять baseline и не подставлять синтетические данные при отсутствии источников.

| Возвращаемый объект | Семантика и приёмка |
| --- | --- |
| `AnalysisResult` | Четыре вида раздельно, Metric и Score раздельно, null != 0, availability != completeness; partial не входит в полный рейтинг |
| `JourneyResult` | Непересекающаяся цепочка, initial wait отдельно от path, UTC duration, выбранные узлы/point/object, фактическая или явно schematic geometry |
| `ReferenceResult` | Версия графа/profile, конкретные boundary sets, подтверждённые времена/операции/evidence; неполный reference не даёт полного индекса |
| `LastReturn`/counts | Связь с point/object/journey; τ может быть pending с отдельным dependency job, не блокируя готовый H-score |
| Cargo | Входящее/исходящее направления; полный delivery score требует complete-chain time, movement-only остаётся raw metric; pipeline не получает transport score |
| `ScenarioResult` | Только сохранённый server proposal, отдельный overlay graph, whole-trip changes, сопоставимые before/after, видимые ухудшения; выигрыш только из engine |

Методики реализуются ровно по FORMULAS/config. Backend/LLM не считают формулы. Полный ноль допустим для подтверждённого отсутствия пути с evidence полноты; ошибка провайдера и пустой неполный ответ остаются unknown. Для regional passenger требуются столицы субъектов; для cargo — реальные грузовые узлы. Не отождествлять эти опорные точки.

Для предложений handler может вернуть `CalculationOutput(analysis=result, proposals=[...])`. В предложении фиксируются `template_id`, operation type `coordinate_transfer|additional_departure`, service IDs/shift, demand/resource/turnaround evidence, assumptions и feasibility. Backend назначает финальный scenario ID и baseline hash при публикации; клиент не задаёт операции. Нет turnaround evidence — только unverified с явной причиной. Нет editable graph — предложение может объяснять ограничения, запуск отклоняется. `GET scenarios` читает сохранённые предложения и не запускает новые расчёты. AI только рекомендует зарегистрированный template; запуск — отдельный HTTP-запрос пользователя.

Проверен `origin/work/ml1` на `a117340`: чистые математические функции уже опубликованы (`category_index`, `point_index`, `territory_index`, passenger/freight/pipeline и delta). Они ещё не входят в текущую dev-базу; маршрутизация, построение эталона и применение изменений графа в этой ветке явно не реализованы. Поэтому они не включают readiness полного анализа. После интеграции координатором использовать существующие функции, не дублировать формулы в backend.

Минимальная передача ML1 для сквозного анализа: публичный callable interface, точные DTO, детерминированные regression fixtures полного/неполного/недоступного случаев и сценарий с ухудшением; команда тестов. Отдельно подтвердить invariants coverage, complete-chain и неизменность baseline. BACKEND fixtures проверяют проводку и не доказывают корректность формул или реальных маршрутов.

## FRONTEND

`contracts/generated/openapi.json` — текущий API, `v2.*.schema.json` — DTO. Fixtures четырёх запросов/результатов лежат в `packages/contracts/fixtures/v2/` и помечены `synthetic: true`. Схема запроса — discriminated union по `analysis_kind`. Поля/aliases старого mockGateway не являются каноническими IDs.

Входное время aware и кратно 15 минутам. Traffic mode выбирается явно; unsupported режим не превращается в static. Категории непустые, priorities точно по выбранным категориям. Cargo profile — зарегистрированный ID с независимыми температурой/срочностью/handling. Polling каждые две секунды; terminal states succeeded/failed/cancelled. У succeeded возвращается `analysis_id` либо `scenario_run_id`. Кнопка сценария отправляет только server `scenario_id` и `{"schema_version":"2"}`.

Сначала получить capabilities; сохранять HttpOnly cookie сессии, использовать same-origin gateway/reverse proxy. ID из другой сессии возвращает 404. Ошибки: `error.code/message/field_errors/request_id/retryable`, header `X-Request-ID`. Template explanation не маркируется как успешный вызов модели. Partial и unknown показываются отдельно от полного результата/рейтинга.

## DEVOPS

Все manifests/lock остаются у DEVOPS. Нужны declared dependencies/source packaging для `a2-contracts`, `a2-api`, `a2-assistant`, прямой `pydantic` для assistant, `sqlalchemy[asyncio]>=2,<3` и обновление lock. Пока package=false, команды требуют `PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src`. Нужны PostgreSQL/PostGIS, миграции, отдельные схемы/порты участников и CI с реальными PG tests. HTTPS: `A2_SECURE_COOKIES=true`.

Для live Flash интеграции нужен durable deployment budget protocol `reserve/record_usage`, валюта и положительный лимит, секреты и отдельный capability smoke. Глобальный платёжный ledger в текущем модуле не реализован; memory budget допустим только с fake transport в тестах. Paid smoke не запускался. Адаптер расписаний тестируется через MockTransport, не является принятой транспортной сетью. Состояние реализации и фактические результаты проверок фиксируются в `docs/tasks/backend.md`.

## Аудит перед PR #8: документы и read-only tools

Это локальные изменения `work/backend`, проверенные относительно `origin/dev` (`c783435`). Push и merge в рамках аудита не выполняются. Актуальная постановка не изменилась; методика остаётся v3.

### Контракт документов для ML2

BACKEND предоставляет `a2_contracts.v2.retrieval.EvidenceChunk` и `a2_api.db.repositories.evidence.EvidenceRepository.publish(snapshot, chunks)`. Это доверенный серверный вход, не HTTP endpoint и не загрузчик URL. ML2 передаёт уже полученный, проверенный публичный текст без персональных данных:

- `chunk_id`, accepted `snapshot_id`, `evidence_id`, `source_id/source_version`;
- URL и `retrieved_at`, точно совпадающие с verified Evidence принятого snapshot;
- `title`, `section`, `document_date` (nullable), закрытые `valid_from/valid_to`, подтверждённые `territory_ids`;
- `text` до 12000 символов, `content_hash=canonical_hash(text)`, явный `public_nonpersonal_reviewed=true` после проверки ML2.

Период документального показателя независим от периода транспортного графа. Период чанка должен входить в подтверждённый период его Evidence; год поиска — год применимости показателя, не обязательно год публикации документа. Права, достоверность текста, территориальная атрибуция и PII review остаются ответственностью ML2. BACKEND не создаёт production-текст, не скачивает документы и не подтверждает их содержание автоматически.

Миграция `0003_evidence_search` добавляет immutable `evidence_chunks`, generated Russian tsvector и GIN. Публикация атомарная, повтор идентичного чанка допустим, изменение hash/метаданных под тем же ID отклоняется. Батч 1–200 чанков. Принятый snapshot целиком связан hash; замена его содержимого под прежним ID не даёт tools читать новый индекс от имени старого результата. Downgrade с сохранёнными чанками отказывает вместо удаления документов.

`search_evidence` принимает `query`, `territory_ids`, опциональные `source_ids`, `year` и пару `start_date/end_date`. Источники и территории ограничиваются серверным scope сохранённого анализа. Поиск — PostgreSQL `plainto_tsquery('russian', ...)`, без произвольного SQL. Ответ `EvidenceSearchResult`: `status`, `completeness=partial`, `reason_code`, `hits` (до 5 и с общим byte budget), `truncated`. Каждый hit содержит chunk/evidence/snapshot/source IDs, source version, URL, retrieval/document dates, section, период, разрешённые территории, excerpt и hash. Нет принятого индекса — `unknown/EVIDENCE_INDEX_UNAVAILABLE`; отсутствие совпадений в существующем индексе не доказывает отсутствия транспорта.

Численные факты по-прежнему берутся только из сохранённых metric_id. Результат модели не может самостоятельно добавить retrieval provenance: `Explanation.retrieved_evidence` заполняется сервером из validated tool hits. Произвольная проза модели не публикуется: она выбирает разрешённые русские формулировки, применимые к статусу метрики, и проверяемые ссылки. Это намеренно ограничивает свободный пересказ документов, но устраняет обход проверки чисел словами. Текст документа передаётся как untrusted data. Неверный JSON, duplicate keys, чужие IDs и неизвестные команды приводят к безопасному fallback; сценарий через tools не запускается.

### Контракт mapping для ML2 и провайдера расписаний

`a2_api.providers.departures.RaspPlaceBinding` содержит `territory_id`, `snapshot_id`, `provider_code` (`s...`/`c...`), IANA timezone и `evidence_id` подтверждённого соответствия. Передать по одному однозначному binding для каждой подключаемой территории; namespace provider code не должен отождествлять разные территории. Evidence должен быть verified, соответствовать source/version принятого snapshot и покрывать запрошенную местную дату. Код города/станции нельзя угадывать по названию или frontend alias.

`RaspDepartureGateway(provider, bindings).get_departures(origin_id, destination_id, departure_at, snapshots=..., source_ids=...)` принимает канонические IDs, разрешённые snapshot и source IDs. Missing/ambiguous/unverified mapping и дата вне покрытия возвращают typed unknown без HTTP-запроса. Gateway внедряется явно: `SavedTools(service, departures=gateway)` в доверенном assistant hook. Оператор владеет жизненным циклом HTTP-клиента, API-ключом и общей квотой экземпляра провайдера.

Tool сохраняет `departures` из immutable результата отдельно от `provider_observation`. Наблюдение маркируется `source=yandex_rasp`, `accepted_snapshot=false`, `coverage=completeness=partial`, содержит fetched/expiry/hash, причины и безопасные provider errors. Оно не меняет baseline, snapshot или индекс; пустой direct-ответ не означает NO_FEASIBLE_ROUTE. При отсутствии gateway остаётся явный `PROVIDER_NOT_CONFIGURED`. ML2 отдельно принимает полученные источники; ML1 отдельно строит маршруты/смешанные цепочки.

`resolve_places` возвращает ограниченный разрешёнными территориями справочник с available/unknown, partial, reason и truncated. `get_calendar` использует только подтверждённый snapshot сохранённого анализа, локальные explicit service dates и рейсы, затрагивающие разрешённые узлы. Период вне принятого календаря — unknown, а не пустое полное расписание. Truncation указывается явно. Tool outputs ограничены также фактическими UTF-8 байтами.

### Подтверждённый блокер DevOps

В новом временном окружении выполнен pinned `uv sync --frozen --all-packages --group dev`: установлено 56 пакетов. Два теста миграций завершились setup ERROR: `ModuleNotFoundError: greenlet` / `The SQLAlchemy asyncio module requires ... greenlet`. Рабочая `.venv` содержит дополнительный greenlet; её зелёная регрессия не доказывает воспроизводимость frozen deployment.

Требуемое изменение владельца: `apps/api/pyproject.toml`, заменить `sqlalchemy>=2,<3` на `sqlalchemy[asyncio]>=2,<3`, обновить `uv.lock` закреплённым uv. Не менять manifest вручную только ради локального PASS. Критерий: новая среда из lock применяет все миграции и проходит PG/process tests без дополнительного pip install. Прямые зависимости API/contracts/assistant, source packaging/PYTHONPATH, PostGIS и CI также остаются у DevOps. BACKEND эти файлы не менял.

Следующая совместная приёмка: реальный walk/static snapshot одного утверждённого города + engine adapter → четыре формы продолжают отдавать честную readiness, первый расчёт/cache, карточка и сохранённый сценарий проверяются через текущий OpenAPI. Отдельное планирование τ и реальные transport/GIS результаты не подменяются RAG или ответом расписаний. Платный Flash capability smoke и deployment spending ledger требуют владельца бюджета и отдельного разрешения; во время аудита live-вызовов не было.

## Обновление Waydex integration readiness

Полная передача FRONTEND теперь в [frontend-integration-handoff.md](frontend-integration-handoff.md). Added DTO: optional immutable `AnalysisResult.map_data` и optional `ExplanationRequest(question,selection)` в существующем POST, configured `Capabilities.areas`; маршруты не расширены. ML2/BACKEND adapter должен передать реальную геометрию и stable cell/point/destination/site/node IDs с evidence; backend не строит полигоны из bbox. ML1 должен сохранять point_id/destination_object_id и субъекты метрик для привязки карточек. Исторические результаты без map_data сохраняют wire/hash; frontend считает отсутствующее map_data unknown.

Прежний frozen blocker выше **исправлен по отдельному разрешению пользователя**: API SQLAlchemy asyncio extra +greenlet в корневом lock; других dependency-файлов/версий не меняли. Чистая frozen environment: 57 packages, pip check/import greenlet PASS, миграции и полный PG/process suite **560 passed**. Поле «требуется sqlalchemy[asyncio]» выше описывает историческое обнаружение; оно больше не открытая зависимость. Остальные source packaging/PostGIS/CI/runtime секреты остаются у DevOps. Paid capability и реальный React↔командные данные не проверялись.

### Обязательные зависимости полного индекса (backend review 2026-11-09)

`Score.reference_ids` — additive список stable `ReferenceResult.reference_id` использованных знаменателей. ML1 передаёт все использованные эталоны, включая эталоны без прямого `Metric.journey_id`. Значение по умолчанию `[]` при сериализации опускается: прежние payload/hash не меняются только из-за добавления поля. Каждая объявленная ссылка должна разрешаться в `AnalysisResult.references`; дубли запрещены.

Для существующих результатов сохраняется проверка цепочки `Score.components → Metric.journey_id → JourneyResult.reference_id → ReferenceResult`. Полный численный score запрещён, если обязательный journey или reference — unknown либо partial; проверка распространяется на score-компоненты через уже существующий граф зависимостей. DTO не вычисляет `I_path`, `I_wait` или итоговый индекс. Новые явно объявленные reference IDs не заменяют и не отключают проверку сохранённых цепочек.

Полнота population/site basis проверяется независимо от `ranking_eligible`. Numeric full запрещён при coverage < 1, неизвестном population или `unknown_positive_weight=true`; нулевой denominator остаётся `not_applicable/ZERO_WEIGHT`, не числом. Предварительные известные значения можно сохранить как `available` score с `completeness=partial`, `ranking_eligible=false` внутри `AnalysisResult.status=partial`. Известные raw времена сохраняются независимо от полноты эталона: частичный reference сам по себе не делает измеренное время unknown.

Сохранённые противоречивые результаты теперь отклоняются при канонической повторной валидации, включая чтение/кеш, с HTTP 409 `stored_result_incompatible` (`Stored result requires recalculation`), а не переинтерпретируются как полные. Кеш не создаёт новый succeeded job для такого payload; API не раскрывает детали валидации или содержимое результата. Исправлять их полноту автоматически backend не может: ML1 должен выдать новый честно размеченный результат по immutable входам. Миграция, переписывающая результаты или result_hash, не вводится. Это усиление семантической валидации v2, а не изменение формул.

### Mandatory operands: follow-up independent review

Existing `components`, `Metric.journey_id`, `JourneyResult.reference_id` and `Score.reference_ids` express links but do not state whether a missing link is optional. They are retained. One optional `Score.calculation_kind` now distinguishes intercity `reference_ratio`, `initial_wait`, `aggregate`, and `delivery_deadline`; no generic operand framework or name-based inference is introduced.

ML1 must supply this field for numeric/full intercity scores. `reference_ratio` needs raw metric components plus resolvable full Reference operands (direct reference_ids or metric→journey→reference). A known independent raw time plus an explicit full reference is supported without a journey link. `initial_wait` requires its journey but no Reference. `aggregate` requires score components, whose own mandatory operands remain validated. Cargo `delivery_deadline` uses the complete-chain operand and D, not a compulsory Reference. The DTO checks metadata, links and completeness, never computes these formulas.

Urban H indices derive methodology from analysis_kind; they need no new field and no intercity Reference. Full numeric territorial scores need PopulationBasis for urban_passenger and SiteBasis for urban_cargo, regardless of ranking_eligible. Missing/wrong/incomplete basis and known incomplete component weights forbid full. Honest partial values and independent raw times remain available; zero denominator remains not_applicable/ZERO_WEIGHT.

Compatibility: calculation_kind is structurally additive (optional nullable field, None omitted on serialization), but semantically breaking for old full intercity scores lacking declared calculation semantics. Those immutable payloads are not relabelled or rehashed: reads/cache reuse return 409 stored_result_incompatible. Partial legacy intercity values and valid urban H results remain readable. ML1 must issue a new result with declared operands. FRONTEND regenerates response types, preserves null/partial, and handles the existing409 recalculation path. No new request field or endpoint is introduced. lost_connection and reference_ids are retained.

The OpenAPI before/after patch is [review-openapi-score-semantics.diff](review-openapi-score-semantics.diff). It changes only Score.calculation_kind; generated nested schemas are exports of the canonical DTO. Fullness/weight checks are additional semantic validation, not extra JSON fields.

Scenario publication additionally resolves mandatory operands against the immutable baseline. Changing a journey ID may retain the same task, but cannot replace its original Reference with another complete baseline journey/reference. Both implicit metric→journey→reference and directly declared score reference_ids remain obligations, including partial legacy scores upgraded to typed semantics. Known foreign endpoints are rejected; correct task-scoped journey-ID changes remain supported. No after-Reference payload or new scenario field was added. The same calculation/weight context validation is reused for AnalysisResult and scenario publication.

For passenger `aggregate`, the score subtree must contain a path (`reference_ratio`) operand. An intermediate aggregate or deletion of the path score cannot convert an initial-wait-only subtree into a complete transport index. A standalone `initial_wait` still requires no Reference. Cargo aggregate→delivery score→complete-chain metric is traversed without computing formulas. These are strengthened validation rules, not new fields or enum entries.

### Fourth-review: applicable Reference and historical scenario reuse

No new DTO field, enum, endpoint or schema version was introduced. A full index may use a Reference only if its directed scope can be proven. With fixed intercity origin_node_ids/destination_node_ids, both Reference boundary sets must equal the requested sets (order irrelevant; no subset/superset substitution), and any evaluated Journey must have endpoints inside those sets. Without fixed sets, proof comes only from Journeys reached through that Score's operands: an explicit Journey→Reference link with matching endpoints, or exact singleton directed bounds matching the evaluated Journey. City IDs are never equated to graph-node IDs. Full reference indices with neither proof are rejected; urban H, standalone raw data and honest partial/unknown do not acquire a compulsory Reference.

Scenario semantic compatibility is now one reusable repository validator for publication, read and cache reuse, separate from lease/claim fencing. A row's access scope is checked before parsing or loading its baseline. Correct historical urban/partial scenarios remain readable; old scenarios that violate current semantics return safe409 stored_result_incompatible. Reads use a coherent snapshot; cache/publication hold shared locks on the validated immutable inputs until transaction completion. Payload/result_hash and proposal/content_hash are verified without rewriting them. replay, job reads, input export, expired-TTL idempotency and terminal cancel cannot return succeeded without the same validation. Publication validates the canonical saved DTO rather than a concurrently mutable original object.

ML1 must retain verifiable directed operand provenance; a full raw-time/explicit-Reference result without Journey must declare the fixed request node sets. FRONTEND keeps the existing wire types but must handle stored_result_incompatible also during polling/replay/cancel, request a fresh calculation and preserve honest partial/raw values. No regeneration is required by this cycle: canonical OpenAPI/JSON Schema exports are byte-identical to be98b68. Historical regression tests execute pinned git archive2c193054 with its actual Repository.succeed; exported test trees use test-only A2_TEST_HISTORICAL_REPOSITORY to identify a Git source containing that commit. CI owners need that history available for this test; runtime does not load old code.
