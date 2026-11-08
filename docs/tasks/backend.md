# BACKEND

Ветка work/backend. Пути: apps/api/, packages/contracts/, packages/assistant/.

Источник: ../PRODUCT_SPEC.md; контракты ../../packages/contracts/specs/README.md.

DTO четырёх видов анализа, отдельные Metric и Score, nullable значения, jobs/cache. Серверный allowlist сценариев; AI только объясняет факты. Первым шагом DTO/fixtures, затем настоящий OpenAPI, worker и интеграции.

Зависимости: ML1: engine interface; ML2: snapshots.

Приёмка: Контрактные тесты missing != zero, methodology_pending, conditional fields; воспроизводимый экспорт схем.

Передача: commit, пути, команды/результаты проверок, контрактные изменения и ограничения. Формулы реализуются по FORMULAS.md и config/product.json; данные проверяются отдельно.

## Финализация 08.11.2026
Обязательны PRODUCT_SPEC.md, FORMULAS.md, IMPLEMENTATION.md и ASSISTANT.md. Начать с этапа 1 таблицы IMPLEMENTATION, сохранить существующие изменения коллег. Контракты версии 2: численные методики утверждены, readiness данных отдельно. Передать в PR фактические проверки и зависимости.
