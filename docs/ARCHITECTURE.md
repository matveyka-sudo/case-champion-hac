# Архитектура
Продукт: [PRODUCT_SPEC.md](PRODUCT_SPEC.md). Четыре вида анализа, без старых regular/medical.
| Путь | Владелец | Граница |
|---|---|---|
| apps/web/ | FRONTEND | Активный React/Vite проект, карта, формы, исходные метрики, состояния |
| apps/api/ | BACKEND | HTTP, jobs, worker, кеш, миграции |
| packages/contracts/ | BACKEND | Независимые DTO, схемы, единицы и статусы |
| packages/engine/ | ML1 | Временные цепочки и сценарии; индексы по FORMULAS.md и config/product.json |
| packages/data_pipeline/, data/, ml/ | ML2 | Источники, геометрии, население, расписания, грузовые узлы, QA |
| packages/assistant/ | BACKEND | Объяснение сохранённых результатов и разрешённые сценарии |
| infra/, .github/, config/ | DEVOPS | Окружение, CI, конфигурация и выпуск |

apps/web/ — единственная активная точка React/Vite-разработки: карта, фильтры, API adapters и изолированные demo fixtures. Локальные npm manifest/lock и src/shared остаются внутри приложения. Прежний scaffold сохранён в Git history и описан в docs/archive/2026-11-09/web-scaffold.md. Общий API-handoff — docs/FRONTEND_INTEGRATION.md; canonical OpenAPI — contracts/generated/openapi.json. Общие scripts, CI и инфраструктура остаются на уровне репозитория.
Python workspace, lock и PostgreSQL 17 сохраняются. Точки входа API/worker и команды запуска описаны в apps/api/README.md. Большие задания выполняются worker; кеш включает параметры, версии источников, геометрию, профиль и методику. RAG используется для текстового контекста; численные вычисления выполняются по структурированным данным. Ключи API только на сервере.
Сценарное изменение расписания требует локальной изменяемой модели или поддерживающего API; внешний готовый маршрут не позволяет доказать эффект сдвига рейса. База и сценарий неизменяемы, ухудшения также показываются.

Принятый runtime-план: PostgreSQL/PostGIS, jobs worker, Valhalla и OTP 2; Alice AI LLM Flash. Детали и порядок подключения — IMPLEMENTATION.md, tools — ASSISTANT.md. Не заменять действующий PostgreSQL-образ до проверки совместимости PostGIS владельцем infra.

## Backend module boundaries

В apps/api/src/a2_api выделены api/v2 (HTTP routers), services (application
orchestration), db/repositories (asyncpg/raw SQL), workers (single worker/spawn),
core (settings/config/internal job types), assistant (backend Alice adapters),
providers (external HTTP) и exports. app.py остаётся стабильным ASGI entrypoint;
старые import paths работают через тонкие facades. Canonical Pydantic v2 остаются
в packages/contracts. SQLAlchemy используется только миграциями. Транзакции,
lease fencing и atomic publication сохранены; полная карта —
[backend README](../apps/api/README.md#module-boundaries-after-the-fix-fullstack-refactor).
