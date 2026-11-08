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
from a2_api.jobs import ClaimedJob, CalculationOutput, V2JobContext
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
