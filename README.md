# A2 — транспортная связанность

Городская пассажирская доступность, пассажирский межгород, доступ предприятий к грузовым узлам и межгородские грузы (включая трубопроводы). Три города для детального анализа: Москва, Калининград, Новороссийск. Межгород — пары городов/субъектов России.

**Итоговое ТЗ и формулы приняты для реализации. Модель — Alice AI LLM Flash.** Числа рассчитывает engine; неизвестные данные не подменяются нулями. [Формулы](docs/FORMULAS.md) · [План реализации](docs/IMPLEMENTATION.md) · [Ассистент](docs/ASSISTANT.md). [Действующее ТЗ](docs/PRODUCT_SPEC.md) · [Решения](docs/ML_PLAN.md) · [Задачи ролей](docs/tasks/) · [Контракты](packages/contracts/specs/README.md).

Подготовлены ТЗ, формулы, конфигурация и контракты. frontend/ содержит интерфейс команды и демонстрационные fixtures; они сохранены. Backend, engine и реальные адаптеры ещё предстоит реализовать.

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

Следующие команды выполняются из `frontend/` под Node.js 24.

```bash
npm ci
npm ls --depth=0
```

Compose можно проверить с безопасным примером окружения.

```bash
docker compose --env-file .env.example config --quiet
```

Compose описывает PostgreSQL 17 с локальным портом и собственным томом. Скопируйте `.env.example` в `.env`, задайте свободный `POSTGRES_PORT`, согласуйте с ним `DATABASE_URL` и заполните секреты только в локальном файле. `DATABASE_SCHEMA` можно не задавать: backend использует `a2_backend`. Из корня репозитория запустите `docker compose up -d postgres`, затем миграции и API по [инструкции backend](apps/api/README.md). API, worker, Alembic и бюджетный CLI читают локальный `.env`; переменные процесса имеют приоритет. `LLM_ENABLED=false` и `ALICE_BILLING_CURRENCY_CONFIRMED=false` оставляют Alice AI выключенной. Лимиты 10 RUB и 50 000 токенов записываются в PostgreSQL только явной командой `a2_api.budget_cli set-limit`; копирование шаблона не создаёт и не сбрасывает ledger. RUB здесь — внутренняя учётная валюта, а не подтверждение валюты организаторов.

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
cd frontend
npm ci
npm run build
npm run lint
```

Старые regular/medical и коридор Калининград–Зеленоградск–Балтийск архивированы в docs/archive/2026-11-06. Рабочие ветки не удаляются; участники вливают обновления origin/dev по docs/OWNERSHIP.md.
