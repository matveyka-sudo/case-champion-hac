# A2 — транспортная доступность

Атлас доступности для транспортного аналитика и пассажира. Профили `regular` (работа и учёба), `medical` (плановые визиты) и `intercity` дают самостоятельные оценки. Пилот D1 предлагает Калининград, Зеленоградск и Балтийск. Подтверждение точек, целей, календарей и параметров остаётся открытым. В репозитории подготовлены окружение, документальные контракты и задачи по ТЗ 1.1.

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
```

Compose можно проверить с безопасным примером окружения.

```bash
docker compose --env-file .env.example config --quiet
```

Compose описывает PostgreSQL 17 с локальным портом и собственным томом. Для нового worktree создайте свой `.env` по `.env.example`. Выберите свободный `POSTGRES_PORT`, согласуйте с ним `DATABASE_URL`, задайте уникальное имя Compose-проекта и свои порты API и SPA для будущей интеграции. Будущие настройки ассистента `OPENAI_API_KEY`, `OPENAI_MODEL` и денежный лимит заполняются только после проверки API-доступа и фиксации D4. Секреты хранятся на backend. Команды импорта и запуска API, worker и UI появятся после реализации точек входа.

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
- [План расчёта и оценки ИИ](docs/ML_PLAN.md) фиксирует профиль и гипотезу.
- [Приёмка](docs/ACCEPTANCE.md) разделяет проверки окружения и будущие проверки продукта.
- [Указатель источников](docs/reference/README.md) фиксирует действующее ТЗ 1.1 и отделяет его от материалов организатора и исторического исследования.
