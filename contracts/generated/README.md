# Экспорт контрактов 2.0.0

`openapi.json` получен из production FastAPI v2. `v2.*.validation.schema.json` и `v2.*.serialization.schema.json` описывают текущие DTO, включая discriminated `AnalysisRequest` и `CargoProfile`. `manifest.json` содержит версию и SHA-256 без временных меток. `legacy-openapi.json` и файлы без префикса `v2.` сохранены для исторической совместимости; для нового frontend они не используются.

Из корня репозитория:

```sh
export PYTHONPATH=packages/contracts/src:apps/api/src:packages/assistant/src
.venv/bin/python -m a2_api.export --output contracts/generated
.venv/bin/python -m a2_api.export --output contracts/generated --check
```

Повторный экспорт байт-в-байт идентичен; `--check` не меняет файлы. Python cross-field/context validators обязательны: одна JSON Schema не подтверждает достоверность данных, полноту сети или готовность расчётчика. Основание — `docs/PRODUCT_SPEC.md`, `docs/FORMULAS.md`, `config/product.json`; интеграция и ограничения — `apps/api/docs/handoff-ml1-ml2.md` и `docs/tasks/backend.md`.

Аудит PR #8 добавляет DTO `EvidenceChunk`, `EvidenceHit`, `EvidenceSearchRequest/Result` и серверное поле `Explanation.retrieved_evidence`; schema_version остаётся `"2"`. Входные аргументы `search_evidence` расширены source/year/period filters. Generated schemas экспортируются повторно вместе с OpenAPI; они не содержат production-документов.
