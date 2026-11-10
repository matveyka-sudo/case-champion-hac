# A2 — транспортная связанность

Городская пассажирская доступность, пассажирский межгород, доступ предприятий к грузовым узлам и межгородские грузы (включая трубопроводы). Три города для детального анализа: Москва, Калининград, Новороссийск. Межгород — пары городов/субъектов России.

**Итоговое ТЗ и формулы приняты для реализации. Модель — Alice AI LLM Flash.** Числа рассчитывает engine; неизвестные данные не подменяются нулями. [Формулы](docs/FORMULAS.md) · [План реализации](docs/IMPLEMENTATION.md) · [Ассистент](docs/ASSISTANT.md). [Действующее ТЗ](docs/PRODUCT_SPEC.md) · [Решения](docs/ML_PLAN.md) · [Задачи ролей](docs/tasks/) · [Контракты](packages/contracts/specs/README.md).

React/Vite-приложение Waydex находится в `apps/web/`, backend — в `apps/api/`, библиотеки — в `packages/`. Demo fixtures сохранены и изолированы от API-режима; готовность данных и общего E2E проверяется отдельно.

## Окружение

Используйте Python 3.12 и uv 0.12.17, Node.js 24.22.2 LTS с npm 11.19.1 и Docker Compose. `.python-version` задаёт версию Python для uv. Для пользователей nvm версия Node записана в `.nvmrc`. Команды Python и Compose выполняются из корня репозитория.

```bash
uv lock --check
uv sync --frozen --all-packages --group dev
uv pip check
```

Дополнительные группы Python подключаются по задаче. `uv sync` приводит `.venv` к выбранному набору, поэтому для обеих групп используйте последнюю команду.

```bash
uv sync --frozen --all-packages --group dev --group notebooks
uv sync --frozen --all-packages --group dev --group ml
uv sync --frozen --all-packages --group dev --group notebooks --group ml
```

Следующие команды выполняются из `apps/web/` под Node.js 24.

```bash
npm ci
npm ls --depth=0
npm run dev
```

Compose можно проверить с безопасным примером окружения.

```bash
docker compose --env-file .env.example config --quiet
```

Compose описывает PostgreSQL 17 с локальным портом и собственным томом. Для нового worktree создайте свой `.env` по `.env.example`, выберите свободный `POSTGRES_PORT`, согласуйте с ним `DATABASE_URL`, задайте уникальное имя Compose-проекта и свои порты API и SPA. `DATABASE_SCHEMA` можно не задавать: backend использует `a2_backend`. Из корня репозитория запустите `docker compose up -d postgres`, затем миграции и API по [инструкции backend](apps/api/README.md). API, worker, Alembic и бюджетный CLI читают локальный `.env`; переменные процесса имеют приоритет. Секреты хранятся только на backend. `LLM_ENABLED=false` и `ALICE_BILLING_CURRENCY_CONFIRMED=false` оставляют Alice AI выключенной. Лимиты 10 RUB и 50 000 токенов записываются в PostgreSQL только явной командой `a2_api.budget_cli set-limit`; копирование шаблона не создаёт и не сбрасывает ledger. RUB здесь — внутренняя учётная валюта, а не подтверждение валюты организаторов. Настройка web API-режима и same-origin proxy — в [интеграционной инструкции](docs/FRONTEND_INTEGRATION.md).

### PostgreSQL с PostGIS

Для пространственных запросов подключите `compose.postgis.yaml`. Он использует отдельный том `postgis_data` и инициализирует PostGIS в базе при первом старте. Пример поднимает отдельный Compose-проект на порту `55433`. Выберите своё имя проекта и свободный порт, если на машине работает несколько копий.

```bash
export COMPOSE_FILE=compose.yaml:compose.postgis.yaml
export COMPOSE_PROJECT_NAME=a2-postgis-local
export POSTGRES_PORT=55433
docker compose --env-file .env.example up -d --wait postgres
docker compose --env-file .env.example exec -T postgres psql -X -v ON_ERROR_STOP=1 -U a2 -d a2 \
  -c 'SELECT PostGIS_Full_Version(), ST_AsText(ST_MakePoint(20.5, 54.7));'
```

Для миграций в этом примере задайте `A2_DATABASE_URL=postgresql://a2:a2_local_dev@127.0.0.1:55433/a2` и следуйте [инструкции API](apps/api/README.md#run). На Apple Silicon для этого образа нужна эмуляция `linux/amd64` в Docker Desktop.

## Работа команды

У каждой роли своя постоянная ветка `work/<role>`. Общая ветка `dev` служит для интеграции через PR, `main` хранит проверенные версии. Точные ветки, границы ролей и порядок синхронизации описаны в [правилах командной работы](docs/OWNERSHIP.md).

На одном компьютере откройте отдельный worktree своей роли и выполняйте команды установки выше в нём. На другом компьютере создайте свой клон и выберите опубликованную ветку роли. Пример для FRONTEND.

```bash
git clone --branch work/frontend https://github.com/matveyka-sudo/case-champion-hac.git
cd case-champion-hac
```

Замените `work/frontend` своей веткой из таблицы ролей. Окружение устанавливается в рабочем каталоге участника. Перед работой прочитайте корневой `AGENTS.md`, локальные инструкции и стартовую задачу. [FRONTEND](docs/tasks/frontend.md) · [DEVOPS](docs/tasks/devops.md) · [BACKEND](docs/tasks/backend.md) · [ML1](docs/tasks/ml1.md) · [ML2](docs/tasks/ml2.md). Claude Code начинает с `CLAUDE.md`.

- [Архитектура](docs/ARCHITECTURE.md) описывает зависимости и переход к импортируемым Python-пакетам.
- [Спецификации](packages/contracts/specs/README.md) определяют общие поля и смысл статусов.
- [Данные](docs/DATA.md) описывают доступные материалы и пробелы расписаний.
- [План расчёта и оценки ИИ](docs/ML_PLAN.md) фиксирует принятые решения и их владельцев.
- [Приёмка](docs/ACCEPTANCE.md) разделяет проверки окружения и будущие проверки продукта.
- [Указатель источников](docs/reference/README.md) отделяет исходное ТЗ от финальной постановки 08.11.2026 и материалов организатора/исторического исследования.

## Проверка постановки
```bash
python3 scripts/check_product_config.py
cd apps/web
npm ci
npm run build
npm run lint
npm run api:check
npm run typecheck
npm test
npm run test:e2e
```

Старые regular/medical и коридор Калининград–Зеленоградск–Балтийск архивированы в docs/archive/2026-11-06. Рабочие ветки не удаляются; участники вливают обновления origin/dev по docs/OWNERSHIP.md.

## Расположение frontend

Единственный web npm-пакет: `apps/web/package.json` и его `package-lock.json`;
корневых npm workspaces нет. `src/`, `public/`, `tests/`, `e2e/`, Vite/TS и
локальный генератор относятся к приложению. Общие DTO/OpenAPI остаются в
`packages/contracts/` и `contracts/generated/`, CI — в `.github/workflows/`,
инфраструктура — в `infra/`. Общие документы: [API-handoff](docs/FRONTEND_INTEGRATION.md)
и [сверка UI с ТЗ](docs/FRONTEND_UI_SCOPE_REVIEW.md). Фото и visual QA — в `apps/web/docs/`.

Старый scaffold сохранён в Git history и [архивном описании](docs/archive/2026-11-09/web-scaffold.md).
Из корня `node scripts/check_web_layout.mjs` проверяет отсутствие tracked `frontend/`,
согласованность web manifest/lock, пути CI и актуальность generated API.
Web-команды и proxy: [apps/web/README.md](apps/web/README.md).
