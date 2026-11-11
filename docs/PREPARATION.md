# Подготовка dev — 08.11.2026
Исходная вершина: 73ae577f0bf72c0d305b22a582cd1a27cda15c9b (включает изменения FRONTEND). Координатор действует по прямому поручению пользователя; постоянные work/* не изменяются.

## Результат
- PRODUCT_SPEC.md — итоговое ТЗ 2026-11-08.1.
- FORMULAS.md — формула, объяснение и причина; v3 закрывает калибровку эталона A по рёбрам.
- IMPLEMENTATION.md — конкретные решения пробелов, очередь и порядок интеграции.
- ASSISTANT.md — выбранная Alice AI LLM Flash, серверный API, tools и отказоустойчивость.
- TRAFFIC_AND_PROVIDERS.md — сохранённые результаты проверки провайдеров с обновлённым выбором модели; SOURCE_REGISTRY.md — все назначения A2.
- config/product.json и проверка отражают утверждённые параметры; контракты версии 2, задачи пяти ролей и инструкции синхронизированы.
- frontend/src, его assets, npm manifest/lock, Python manifests/uv.lock и история веток сохранены. Исторический p1-v1.toml не runtime default.
- Backend, engine и адаптеры не объявлены реализованными. Их работа распределена между владельцами; прошлый откаченный код не восстановлен автоматически.

## Проверки этого изменения
- python3 scripts/check_product_config.py — PASS.
- uvx --from uv==0.12.17 uv lock --check — PASS; системный uv 0.13.5 не соответствует pin и не использован для изменения lock.
- frontend: npm ci --ignore-scripts, npm run build, npm run lint, npm test — PASS, 11 тестов в 2 файлах. Среда Node 24.22.1/npm 11.9.0; точный CI pin Node 24.22.2 проверяется в GitHub CI.
- Docker отсутствует в текущей среде; Compose не запускался и не изменён.
- Платный вызов Flash, Yandex Расписания с проектным ключом, сборка графов OTP/Valhalla и продуктовый E2E не выполнялись. Предыдущие проверки источников в TRAFFIC_AND_PROVIDERS не являются новым тестом этой поставки.

## Как команде забрать
В чистом checkout своей постоянной work/<role>:
```bash
git fetch origin
git merge --ff-only '@{upstream}'
git merge origin/dev
```
Не переключать всех на dev и не переписывать историю. При локальной незавершённой работе сначала сохранить её в своей ветке. Начать с этапа 1 IMPLEMENTATION.md; BACKEND публикует DTO/OpenAPI, ML1 — чистые расчёты, ML2 — принятый data bundle, FRONTEND — API gateway, DEVOPS — runtime/CI. Согласованные PR направлять в dev.
