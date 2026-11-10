# DEVOPS

Ветка work/devops. Пути: infra/, .github/, config/, общие docs/.

Источник: ../PRODUCT_SPEC.md; контракты ../../packages/contracts/specs/README.md.

Окружение, существующие lock-файлы, CI активного apps/web/, проверки конфигурации. Формулы утверждены; проверять config/product.json и версию методики. Подключать entrypoints после реализации; без фиктивных команд запуска API.

Зависимости: Все роли: проверенные entrypoints, зависимости и ресурсы.

Приёмка: CI, lock, изолированный Compose, отсутствие секретов. Не переписывать историю work/*.

Передача: commit, пути, команды/результаты проверок, контрактные изменения и ограничения. Формулы реализуются по FORMULAS.md и config/product.json; данные проверяются отдельно.

## Финализация 08.11.2026
Обязательны PRODUCT_SPEC.md, FORMULAS.md, IMPLEMENTATION.md и ASSISTANT.md. Начать с этапа 1 таблицы IMPLEMENTATION, сохранить существующие изменения коллег. Контракты версии 2: численные методики утверждены, readiness данных отдельно. Передать в PR фактические проверки и зависимости.

## Web relocation — 09.11.2026, fix-fullstack

Активное приложение и npm cache lock теперь apps/web. environment.yml использует
новый working-directory, проверяет generated API, strict TS, build/lint/unit tests
и root scripts/check_web_layout.mjs. Node24.22.2/npm11.19.1 и dependency versions
сохранены. Docker/web deployment отсутствовал; PostgreSQL Compose не менялся.
Дистрибутив для будущего static deployment: apps/web/dist. Same-origin production
proxy должен передавать /api/v1 и /health, сохраняя Host/Origin/cookie; подробности
в docs/FRONTEND_INTEGRATION.md. Локальный Vite proxy проверен с echo API, cloud CI
и production backend E2E не выдаются за выполненные локальные проверки.
