# Подготовка dev от 07.11.2026

Исходная вершина: a3d244623fec783670060b43dfca72b51a924f98. Координатор обновил документацию и контракты по поручению пользователя; рабочие ветки не изменялись.

## Результат
- PRODUCT_SPEC.md — текущий scope с последним решением отложить все формулы.
- config/product.json — четыре анализа, три города, категории/режимы, pending-методики без коэффициентов.
- Задачи пяти ролей и документационные контракты приведены к новому scope; старые материалы сохранены в docs/archive/2026-11-06.
- frontend/ — активный Vite starter, его код не переписывался. apps/web/ сохранён как исторический каркас; CI собирает и проверяет frontend/.
- Python manifests, lock и инфраструктура сохранены; фиктивных API, индексов и провайдеров не добавлено.

## Проверено локально
- python3 scripts/check_product_config.py — PASS.
- git diff --check — PASS.
- npm ci (временный кеш), npm run build, npm run lint в frontend/ — PASS.
- uv lock --check под uv 0.12.17 — PASS. Системный uv 0.13.6 несовместим с существующим pin; проверка выполнена через uvx с изолированными cache/tool dirs.
- docker compose --env-file .env.example config --quiet — PASS. Запуск PostgreSQL и миграции не проверялись.

Backend/engine/адаптеры пока не реализованы; product end-to-end тесты и расчёт на реальных данных не выполнялись. CI в GitHub проверяется отдельно после push.

## Старт команды
Получить origin/dev и влить его в свою постоянную work/<role> без force push. Прочитать PRODUCT_SPEC.md, OWNERSHIP.md и docs/tasks/<role>.md. BACKEND начинает с DTO/fixtures; ML2 с пригодности источников и snapshot; ML1 с временных цепочек; FRONTEND с формы и состояний; DEVOPS с запуска реализованных entrypoints. Согласование формул выполняется отдельным изменением с версией методики.
