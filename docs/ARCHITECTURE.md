# Архитектура A2

A2 оценивает дневные межгородские поездки жителей без личного автомобиля. Региональный транспортный аналитик сравнивает доступность шести городов Калининградской области, разбирает проблемный маршрут и смотрит, как изменение расписания влияет на поездки. Стартовый профиль и гипотеза описаны в [ML_PLAN.md](ML_PLAN.md).

## Компоненты

| Компонент | Интерфейс и граница |
| --- | --- |
| `packages/contracts` | Канонические DTO и спецификации. Зависимости от HTTP, БД, engine и LLM отсутствуют. |
| `packages/data_pipeline` | Вне веб-запроса извлекает и проверяет источники, публикует неизменяемый `DatasetSnapshot` по контракту. |
| `packages/engine` | Принимает `DatasetSnapshot` и `AnalysisProfile`. Возвращает маршруты, вклады, индексы, чувствительность и сценарии. Расчёт не требует API, БД или модели. |
| `packages/assistant` | С помощью OpenAI Python SDK и Responses API выбирает разрешённые типизированные инструменты. Инструменты работают с каноническими DTO и публичными функциями engine. |
| `apps/api` | Один FastAPI backend с Python-пакетами в его процессе. API связывает contracts, engine и assistant, сохраняет версии и результаты в PostgreSQL. |
| `apps/web` | React SPA с Vite, Tailwind 4, shadcn/ui, TanStack Query, MapLibre и Recharts. HTTP через `openapi-fetch`, типы из `openapi-typescript`. |

API открывает ограниченный пул asyncpg в lifespan, задаёт таймауты и транзакционные границы записи результатов, явно кодирует/декодирует JSON и JSONB. Runtime использует параметризованный raw SQL. SQLAlchemy 2 с asyncpg служит только для Alembic. Тяжёлые расчёты запускаются через ограниченный executor вне async event loop. Синхронный CPU-код не получает соединение из async-пула. Число расчётов и размер входов ограничиваются отдельно от HTTP concurrency. Готовые артефакты публикуются атомарно после валидации.

Нормализованные таблицы живут в Parquet, исходные копии неизменяемы. В PostgreSQL храним каталог версий и результаты. Результат идентифицирует исходный набор, профиль и версию алгоритма. Локальные выгрузки складываем в `artifacts/`. Nginx для SPA и проксирования API появится после рабочих точек запуска.

## Первый интерфейс интеграции

BACKEND уточняет маршруты HTTP и публикует OpenAPI в `contracts/generated/`. Таблица фиксирует стартовое соглашение для задач команды.

| HTTP | Результат |
| --- | --- |
| `GET /api/v1/profiles/{profile_id}/versions/{profile_version}` | `AnalysisProfile` |
| `GET /api/v1/runs/{run_id}/accessibility` | `AccessibilityResult` |
| `POST /api/v1/routes` | `RouteResult` для версии snapshot, версии профиля, пары городов и времени отправления |
| `POST /api/v1/scenarios` | `ScenarioResult` для валидного `ScenarioRequest` |
| `POST /api/v1/assistant` | `AssistantResult` с ответом, уточнением либо машинным статусом |

Первый сценарий интеграции использует `get_profile` и `simulate_scenario` как инструменты ассистента. Их аргументы сопоставляются с `AnalysisProfile` и `ScenarioRequest`. HTTP ошибки описывает BACKEND поверх общего формата ошибок из [спецификаций](../packages/contracts/specs/README.md). FRONTEND генерирует типы из опубликованной схемы и использует серверные численные результаты.

## Переход к импортируемым пакетам

Текущие workspace members устанавливают внешние зависимости. В каждом из пяти manifests стоит `package = false`. После появления реализации владелец создаёт пакет в указанном src-layout, передаёт DEVOPS зависимости и проверяемый способ импорта. DEVOPS согласует backend сборки, включает упаковку, объявляет workspace sources для реальных зависимостей и обновляет один `uv.lock`.

| Проект | Будущий src-layout | Локальные зависимости после реализации |
| --- | --- | --- |
| `a2-contracts` | `packages/contracts/src/a2_contracts` | Нет |
| `a2-data-pipeline` | `packages/data_pipeline/src/a2_data_pipeline` | `a2-contracts` |
| `a2-engine` | `packages/engine/src/a2_engine` | `a2-contracts` |
| `a2-assistant` | `packages/assistant/src/a2_assistant` | `a2-contracts`, `a2-engine` |
| `a2-api` | `apps/api/src/a2_api` | `a2-contracts`, `a2-engine`, `a2-assistant` |

По мере появления пакетов DEVOPS проверяет установку из чистого checkout и существующие entrypoints. Модуль данных остаётся отдельным потребителем contracts и поставщиком snapshot. API читает опубликованный snapshot по контракту.
