# Экспорт контрактов

BACKEND публикует здесь OpenAPI из реального FastAPI-приложения и JSON Schema из канонических Pydantic-моделей, версия контракта 0.2.0. FRONTEND использует `openapi.json` для генерации типов. `*.validation.schema.json` описывают вход, `*.serialization.schema.json` — сериализацию; `manifest.json` содержит SHA-256 артефактов без временных меток.

Из корня репозитория:

```sh
export PYTHONPATH=packages/contracts/src:apps/api/src
.venv/bin/python -m a2_api.export --output contracts/generated
.venv/bin/python -m a2_api.export --output contracts/generated --check
```

Повторный экспорт из тех же исходников байт-в-байт идентичен. `--check` не меняет файлы и завершает процесс с кодом 1 при расхождении. Cross-field и контекстные проверки выполняются Python-валидаторами; одна структурная JSON Schema не подтверждает достоверность входных данных. Схемы не означают готовность расчётчика/worker или согласование реального списка городов. См. `apps/api/docs/handoff-ml1-ml2.md`.

## Актуальность после слияния dev 08.11.2026

Активный frontend находится в `frontend/`. Опубликованные схемы 0.2.0 отражают существующий backend до перехода на PRODUCT_SPEC 2026-11-08.1. Целевой контракт v2 задан `docs/PRODUCT_SPEC.md`, `docs/FORMULAS.md`, `config/product.json` и `packages/contracts/specs/`; экспортированные модели ещё требуют адаптации и не объявляются соответствующими новым четырём видам анализа. Команды выше сохраняются для воспроизводимости текущего состояния.
