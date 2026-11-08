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
