# BACKEND

Ветка work/backend. Пути: apps/api/, packages/contracts/, packages/assistant/.

Источник: ../PRODUCT_SPEC.md; контракты ../../packages/contracts/specs/README.md.

DTO четырёх видов анализа, отдельные Metric и Score, nullable значения, jobs/cache. Серверный allowlist сценариев; AI только объясняет факты. Первым шагом DTO/fixtures, затем настоящий OpenAPI, worker и интеграции.

Зависимости: ML1: engine interface; ML2: snapshots.

Приёмка: Контрактные тесты missing != zero, methodology_pending, conditional fields; воспроизводимый экспорт схем.

Передача: commit, пути, команды/результаты проверок, контрактные изменения и ограничения. Формулы реализуются по FORMULAS.md и config/product.json; данные проверяются отдельно.

## Финализация 08.11.2026
Обязательны PRODUCT_SPEC.md, FORMULAS.md, IMPLEMENTATION.md и ASSISTANT.md. Начать с этапа 1 таблицы IMPLEMENTATION, сохранить существующие изменения коллег. Контракты версии 2: численные методики утверждены, readiness данных отдельно. Передать в PR фактические проверки и зависимости.

## Состояние реализации до новых требований

Следующая запись фиксирует сделанное по прежнему контракту. Это не подтверждение соответствия PRODUCT_SPEC 2026-11-08.1: существующие DTO/API/jobs и документацию нужно сверить с целевым контрактом v2 перед дальнейшей разработкой.

## Контрактный выпуск 0.1.0 — 07.11.2026

Реализованы канонические модели, общие валидаторы, HTTP boundary, три synthetic fixtures, детерминированные OpenAPI/JSON Schema и загрузка каталога из JSON. Первый регион по актуальному указанию пользователя — Ленинградская область; полный список городов задаётся конфигурацией отдельно от демонстрационного поднабора. Реальные города/учреждения/услуги BACKEND самостоятельно не выбирает.

Команды и фактические ограничения: [apps/api/README.md](../../apps/api/README.md). Передача №4/№5/FRONTEND/DEVOPS: [handoff](../../apps/api/docs/handoff-ml1-ml2.md). 132 теста, ruff, strict mypy и проверка воспроизводимости экспорта пройдены. Отдельный процесс API проверен на явно синтетическом каталоге. Без адаптеров расчётные/data endpoints возвращают 503. PostgreSQL jobs/worker, миграции, реальные региональные расчёты и агент ещё требуют следующего этапа; контракт не является заявлением об их готовности.

## Выпуск jobs/worker 0.2.0 — 07.11.2026

Следующий технический этап реализован: Alembic/PostgreSQL 17, постоянная очередь и runs, идемпотентность, кэш по версиям, один worker с отдельным процессом расчёта и hard timeout, восстановление после сбоев, polling, JSON/CSV экспорт. Проверено 194 тестами с реальной изолированной БД, включая отдельные процессы API/worker, сигналы и потерю PG lease. Ruff, strict mypy, pip check и экспорт схем прошли.

Передача и дальнейшие действия: [stage2-handoff.md](../../apps/api/docs/stage2-handoff.md). DEVOPS требуется подключить `sqlalchemy[asyncio]` и обновить lock (greenlet пока установлен только локально). Реальные accepted bundle №5 и функции №4 подключаются через доверенные BACKEND adapters; без них новые расчёты остаются 503. Города/объекты/сценарии backend не выбирал. Агент и платная API-приёмка остаются следующим этапом.

## Контракт 2.0.0 — адаптация к PRODUCT_SPEC 2026-11-08.1

Текущая реализация использует `a2_contracts.v2`, `a2_api.api_v2` и production factory v2 по умолчанию. Исторические записи выше относятся только к прежним выпускам; их региональные/профильные правила не являются текущим scope. Конфигурация продукта и утверждённые методики берутся из `config/product.json` и FORMULAS.

- [x] DTO четырёх видов, tagged requests, отдельные Metric/Score, nullable states, availability/completeness и cross-field validators.
- [x] Accepted snapshot schema: QA/evidence/FK, registry ownership, grid/sample/population, service dates/night times, freight/pipeline/reference/traffic.
- [x] Явно synthetic fixtures четырёх запросов/результатов и полного snapshot; без автоматического подключения к production.
- [x] ProductRegistry с принятыми ID, версиями, датами, cargo profiles и отдельной readiness каждого вида; dynamic traffic не подменяется static.
- [x] Production HTTP: analyses/jobs/cancel/results/capabilities/territories/explanation/saved scenarios/scenario runs; scope сессии и единый request_id.
- [x] V2 durable queue/worker/proposals: PostgreSQL, heartbeat/lease, fenced publication, cancellation, scoped cache и миграция 0002; legacy изолирован.
- [x] Flash adapter и восемь read-only tools, grounded numeric claims, template fallback и bounded calls/tokens/retries. Live выключен без внешнего durable deployment budget.
- [x] Ограниченный адаптер Яндекс Расписаний с explicit partial/direct-only coverage, paging/quota/rate/retry/cache; реальные источники автоматически не подключаются.
- [x] Воспроизводимый экспорт OpenAPI/JSON Schema, Ruff/format, strict mypy и полная регрессия с PostgreSQL.

Передача №4/№5/FRONTEND/DEVOPS обновлена в существующем [handoff](../../apps/api/docs/handoff-ml1-ml2.md). Не выполнены: реальные ML1 functions и accepted ML2 catalogs/snapshots, предметный GIS/transport QA, PostGIS deployment/CI, production packaging/lock, durable monetary ledger и paid Flash capability smoke. Наличие DTO и synthetic green tests не закрывает эти зависимости. Прогресс и окончательные результаты проверок дописываются здесь.

Фактическая итоговая проверка: **465 passed, 0 skipped**, включая отдельные процессы API/worker для v2 и legacy, scoped idempotency/cache/cancel, атомарные server proposals и неизменность baseline после сценария. Исправлены найденные review проблемы: versioned template ID отделён от operation enum; calendar tool ограничен территорией и принятым периодом; эквивалентные offset timestamps сравниваются как один instant; scope симметрично изолирует legacy/v2; cache hit не продлевает source TTL.

Команды из README: full pytest с `A2_TEST_DATABASE_URL` и `PYTHONPATH` трёх src-пакетов; Ruff check/format — 80 файлов; strict mypy — 39 source files; `a2_api.export --check`; `scripts/check_product_config.py`; pinned uv pip check — 57 compatible packages; `git diff --check`. Все перечисленные проверки прошли. Paid/provider live вызовы не выполнялись. Автоматическое планирование отдельного τ-расчёта требует согласования реального ML1 adapter; DTO позволяет pending и отдельный dependency job, но выдуманный τ не создаётся.

После fetch проверена `origin/work/ml1` (`a117340`): математический слой уже есть; он не содержит маршрутизацию/эталонный граф/применение сценария и пока не входит в текущий dev checkout. Не дублировать его формулы; подключить после согласованного слияния и поставки accepted данных.

## Глубокий аудит PR #8 перед интеграцией — 08.11.2026

Проверены текущий `work/backend` (`d2cfe9a`) и diff `origin/dev...HEAD`; origin/dev после fetch — `c783435`. Источники: PRODUCT_SPEC, FORMULAS, IMPLEMENTATION, ASSISTANT, ACCEPTANCE, текущий handoff. ТЗ и формулы в этих ветках совпадают. Работа ограничена BACKEND. Push, создание PR и merge не выполнялись; правки ниже локальные и ещё не входят в опубликованный PR #8.

Изначально корректны: четыре формы API, server-side validation, scoped sessions и same-origin mutations, параметризованный runtime SQL, идемпотентность и snapshot/code/config cache, immutable baseline, запрет arbitrary scenario operations, killable worker/claim TTL/cancellation, typed provider failures и partial direct-only Rasp, запрет live Flash без deployment budget. Исходная полная регрессия: **465 passed** с PostgreSQL. Отсутствие ML1 routing/ML2 production-данных дефектом BACKEND не считалось.

| Приоритет | Подтверждённая проблема | Исправление и проверка |
| --- | --- | --- |
| P0 | Не обнаружено | — |
| P1 | search_evidence искал только по ID, игнорировал территориальный фильтр внутри разрешённой области, не имел source/year/period | Immutable publication контракт ML2 + Russian PostgreSQL FTS, scope/hash/period filters; synthetic PG retrieval и RAG integration tests |
| P1 | Числа словами обходили regex AI-прозы | Только серверные качественные формулировки с проверкой применимости; числа исключительно из saved numeric claims; regression «двести минут» |
| P1 | Count/LastReturn/ScenarioValues могли противоречить canonical metric/journey/dependency graph | Согласованность counts, реальной обратной цепочки/времён, shared score dependency/cycle checks и top-level scenario availability; nested regressions |
| P2 | Template fallback падал на 51 evidence reference/длинных строках | Bounded server rendering и refs; regression корректного большого результата |
| P2 | Потеря advisory connection во время publication не всегда запрещала commit | Publication использует ту же PostgreSQL session, что держит worker lock; real PG tests завершают её до tx и после INSERT, artifacts не сохраняются |
| P2 | Только saved departures без безопасного provider boundary | RaspDepartureGateway, verified ML2 place bindings, explicit partial provider_observation; MockTransport tests и проверка неизменности baseline/snapshot |
| P2 | Большие корректные tool payloads превышали 12 KB и ломали объяснение | UTF-8 byte budgets, bounded excerpts/summary, explicit truncation и fail-closed TOOL_PAYLOAD_LIMIT; кириллические regression tests |
| P2 | Duplicate keys в JSON модели принимались неоднозначно | JSON object_pairs_hook отклоняет duplicate keys до DTO validation |

Новые документы не выдумываются: индекс пуст до trusted публикации reviewed nonpersonal chunks ML2. Нет принятых документов — typed unknown, не фиктивная выдача и не доказательство отсутствия связи. `Explanation.retrieved_evidence` заполняется сервером только из validated tool hits; неизвестные/чужие/подменённые источники отклоняются. Числа из документов не становятся рассчитанными метриками. Prompt injection не превращается в команду или численное обещание; функциональность произвольной AI-прозы намеренно ограничена approved формулировками.

Read-only tools сохраняют восемь разрешённых имён. `resolve_places` ограничен разрешёнными территориями и даёт explicit partial/unknown; `get_calendar` использует explicit dates и только связанные узлы, даты вне периода — unknown. `get_intercity_departures` разделяет saved journeys и direct provider observations, не меняет accepted data и не строит маршруты за ML1. Серверные snapshot/graph/calendar/population/hash проверяются относительно сохранённого run.

Фактические проверки после исправлений: **532 passed, 0 skipped** с PostgreSQL, включая отдельные процессы API/worker v2/legacy, tools/RAG, prompt injection, malformed/duplicate JSON, numeric claims, permissions, cache, cancellation, worker loss, publication race, scenarios, baseline immutability, миграции/guarded downgrade. Ruff/format, strict mypy (42 source files), воспроизводимый OpenAPI/JSON Schema проверяются финальным прогоном. Платных/provider live вызовов не было.

Отдельная проверка в новом окружении из неизменённого lock: pinned `uv sync --frozen --all-packages --group dev` установил 56 пакетов, но **2 migration tests завершились setup ERROR** из-за отсутствия greenlet. Это воспроизводимый блокер DevOps, а не PASS. Точное изменение `sqlalchemy[asyncio]` + lock и критерий новой проверки — в handoff. Рабочая `.venv` имеет greenlet дополнительно; её зелёный suite не заменяет frozen deployment. Python manifests, lock, frontend, CI и инфраструктура не изменялись.

Остаётся BACKEND: подключение реального engine adapter после ML1/ML2 handoff; отдельное планирование τ с реальным callable; подключение durable deployment spending guard и paid capability smoke после бюджета/доступа владельца API; совместный frontend E2E первого walk/static среза. ML2 должен передать accepted snapshot, verified mapping и reviewed EvidenceChunk; ML1 — routing/reference/scenario callable с DTO; DevOps — frozen dependencies/source packaging, PostGIS/CI и секреты/deployment. Эти зависимости не подменялись собственной реализацией формул, источников или GIS.

Готовность: локальные исправления проверены, но PR #8 пока содержит предыдущую опубликованную версию; публикация требует отдельного разрешения пользователя. После публикации остаётся устранить подтверждённый frozen dependency blocker и повторить PG/process suite в чистой среде. Dry-run сравнение текущей dev-базы не выявило текстовых конфликтов; ветки не сливались. Live Flash и реальные транспортные расчёты не объявляются принятыми.

Следующий конкретный этап BACKEND: после DevOps lock fix повторить clean frozen миграции/PG/process tests, затем принять один реальный walk/static snapshot и подключить ML1 adapter к существующим API/worker без изменения формул. До этого endpoint readiness остаётся честной.

Финальные статические проверки прошли: Ruff check; format — 89 файлов; strict mypy — 42 source files; экспорт схем --check; product config; pip check рабочей среды — 57 compatible packages; git diff --check. Отдельный свежий frozen migration ERROR выше остаётся открытым, не заменён этими PASS.

Локальный git status перед передачей: work/backend, HEAD совпадает с origin/work/backend; 40 изменённых/новых файлов, staged изменений нет. Полный перечень:

```text
 M apps/api/README.md
 M apps/api/docs/handoff-ml1-ml2.md
 M apps/api/src/a2_api/analysis_service.py
 M apps/api/src/a2_api/export.py
 M apps/api/src/a2_api/runtime.py
 M apps/api/src/a2_api/storage.py
 M apps/api/src/a2_api/tool_services.py
 M apps/api/tests/test_migrations.py
 M apps/api/tests/test_v2_tools.py
 M contracts/generated/README.md
 M contracts/generated/manifest.json
 M contracts/generated/openapi.json
 M contracts/generated/v2.Explanation.serialization.schema.json
 M contracts/generated/v2.Explanation.validation.schema.json
 M docs/tasks/backend.md
 M packages/assistant/src/a2_assistant/core.py
 M packages/assistant/src/a2_assistant/tools.py
 M packages/assistant/tests/test_assistant.py
 M packages/contracts/src/a2_contracts/v2/assistant.py
 M packages/contracts/src/a2_contracts/v2/results.py
 M packages/contracts/src/a2_contracts/v2/scenarios.py
?? apps/api/migrations/versions/0003_evidence_search.py
?? apps/api/src/a2_api/evidence_search.py
?? apps/api/src/a2_api/providers/departures.py
?? apps/api/tests/test_audit_worker_publication.py
?? apps/api/tests/test_departure_gateway.py
?? apps/api/tests/test_evidence_search.py
?? apps/api/tests/test_rag_integration.py
?? contracts/generated/v2.EvidenceChunk.serialization.schema.json
?? contracts/generated/v2.EvidenceChunk.validation.schema.json
?? contracts/generated/v2.EvidenceHit.serialization.schema.json
?? contracts/generated/v2.EvidenceHit.validation.schema.json
?? contracts/generated/v2.EvidenceProvenance.serialization.schema.json
?? contracts/generated/v2.EvidenceProvenance.validation.schema.json
?? contracts/generated/v2.EvidenceSearchRequest.serialization.schema.json
?? contracts/generated/v2.EvidenceSearchRequest.validation.schema.json
?? contracts/generated/v2.EvidenceSearchResult.serialization.schema.json
?? contracts/generated/v2.EvidenceSearchResult.validation.schema.json
?? packages/contracts/src/a2_contracts/v2/retrieval.py
?? packages/contracts/tests/test_v2_nested_regressions.py
```

## Waydex integration readiness и исправление frozen-зависимости

По прямому поручению пользователя разрешены два dependency-файла: `apps/api/pyproject.toml` и `uv.lock`, а также обычный push в существующий `work/backend` после проверок. Новый PR, merge, force push, frontend/CI/Compose и остальные manifests запрещены. Ранее неопубликованные изменения аудита включаются в тот же backend-коммит; история предыдущего аудита выше сохранена.

Создан [frontend-integration-handoff.md](../../apps/api/docs/frontend-integration-handoff.md): точная таблица трёх UI modes → четырёх analysis_kind, режимов/9 категорий/приоритетов, canonical IDs, timezone/15-minute slots, cargo profiles/pipeline, traffic и статусов; HTTP/examples, cookies/idempotency/polling/cancel, карты, чат и сценарии. Frontend исходники прочитаны в текущем checkout и сверены с origin/dev/work/frontend; не изменялись.

Минимальные additive расширения, без новых endpoints:

- `AnalysisResult.map_data`: stable entity_kind/entity_id, GeoJSON Point/Polygon/MultiPolygon, point/cell links, category/object metadata, population nullable +year/method, evidence links и explicit completeness/geometry_kind. Geometry не создаётся из bbox и не ремонтируется backend. Default unknown опущен из сериализации, чтобы сохранить исторический wire/result_hash; supplied map сохраняется и хешируется вместе с результатом.
- Optional `ExplanationRequest` в существующем POST explanation: question до 2000 символов и selection. ID selection проверяется по scoped saved result до обращения к модели. Вопрос — untrusted context, без запуска расчётов/сценариев; no-body вызов совместим.
- `Capabilities.areas`: только настроенные AreaOption IDs; без выдуманных районов.

Frozen blocker устранён строго разрешённым исключением: `sqlalchemy[asyncio]>=2,<3`; pinned uv 0.12.17 добавил только greenlet 3.5.6 и asyncio dependency metadata SQLAlchemy/API. Существующие версии остальных пакетов сохранены. Выполнены `uv lock --check`, новая установка `uv sync --frozen --all-packages --group dev` в ранее не существовавшую temporary environment, `uv pip check` (57 compatible packages), import greenlet. Ранее падавшие migration tests прошли; затем полный suite в этой чистой среде: **560 passed, 0 skipped** (включая PostgreSQL и реальные процессы API/worker на synthetic fixtures). Ruff/format: 91 files; strict mypy: 43 source files; OpenAPI/JSON Schema воспроизводимы. Live/paid Alice и браузерный React↔real-data E2E не выполнялись.

Новые regression tests: bounded chat body и запрет операций, selected saved entity vs foreign ID, untrusted question forwarding, HTTP chat, HTTP saved map/population unknown, GeoJSON/reference validation, historical hash preservation и configured area inventory. Существующие tests аудита также включены. Приёмка ML1 routing/τ/scenario effects, ML2 production datasets/map metadata и совместный gateway E2E остаются зависимостями. Подробности для frontend-разработчика находятся в новом handoff; дизайн не переделывался.

План фиксации по поручению: `fix(backend): address audit findings and frontend integration readiness` (BACKEND код/docs/tests/generated), затем `fix(deps): include SQLAlchemy asyncio support` (ровно manifest API +root lock). Публикация — обычный push work/backend, без слияния dev/main. Окончательные SHA и исход push сообщаются в ответе пользователю.

## PR #8: полнота эффектов ScenarioResult

Исправлен P1 из независимого REQUEST_CHANGES review: проверка только переданных `deltas` позволяла пропустить ухудшившийся маршрут при наличии улучшения другого маршрута. Теперь полнота проверяется независимо по `before`/`after`: каждый сопоставимый изменившийся time/index требует проверенного эффекта, числовая разница и outcome сверяются с исходными значениями, ухудшившиеся raw Metric IDs должны присутствовать в `worsened_metric_ids`.

Сопоставление использует stable IDs, затем явные однозначные связи эффектов или уникальную семантику показателя (subject, unit, time_kind, completeness, population/site/aggregation basis, methodology). Порядок массивов и префиксы ID не определяют соответствие; дубли, перекрёстные stable IDs и неоднозначные изменившиеся пары отклоняются. Unknown/partial и несопоставимые значения сохраняют nullable effects без выдуманных чисел. `new_connection` остаётся событием без числовой экономии: подтверждённое отсутствие пути не может содержать числовое время существовавшего маршрута; для индекса допустим подтверждённый ноль.

Добавлены 30 contract regression cases и PostgreSQL regression: даже изменённый после создания DTO повторно валидируется в `JobRepository.succeed` до INSERT, некорректный сценарий не сохраняется, baseline не меняется. Исходный пример ревьюера и дополнительный обход через `new_connection` воспроизведены падающими тестами до исправлений.

Проверки текущего backend: **591 passed, 0 skipped**, с PostgreSQL и существующими process smoke tests API/worker на synthetic fixtures; Ruff PASS, format PASS (93 files), strict mypy PASS (43 source files), OpenAPI/JSON Schema export `--check` PASS, `git diff --check` PASS. Публичные поля и схемы не менялись. Проверка merge-кандидата dev, live providers и реальные данные ML1/ML2 в этот прогон не входят; merge не выполнялся. Frontend, формулы ML1 и DevOps не изменялись.
