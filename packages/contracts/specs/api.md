# HTTP API v2
Контракт v2 реализован в `a2_api.api.application` по PRODUCT_SPEC 2026-11-08.1. Production factory использует его по умолчанию. Исторический API 0.2.0 доступен только при явном legacy deployment; готовность данных/engine проверяется отдельно.
POST /api/v1/analyses → 202 {job_id}; GET /api/v1/jobs/{job_id} → queued|running|succeeded|failed|cancelled; GET /api/v1/analyses/{analysis_id} → AnalysisResult.
POST /api/v1/scenarios/{scenario_id}/runs → 202 {job_id}; GET /api/v1/scenario-runs/{run_id} → ScenarioResult.
Validation error: code, message, field_errors, request_id; provider error не конвертировать в transport_unavailable. Анализ регистрируется идемпотентно, тяжёлый расчёт в worker. Кеш учитывает дату/время, географию, режим, категории, профиль, версии snapshot и методики. Ключи провайдера на сервере.

GET /api/v1/capabilities → доступные города/даты/режимы/версии и причины недоступности. GET /api/v1/territories?level=&query= → справочник. POST /api/v1/analyses/{id}/explanation → объяснение сохранённого результата. GET /api/v1/analyses/{id}/scenarios → сохранённые предложения без пересчёта.
Idempotency-Key обязателен для POST analyses/scenario runs; повтор с другим payload →409. Jobs добавляют cancelled; cancellation endpoint POST /api/v1/jobs/{id}/cancel. Access scope на analysis_id проверяется во всех endpoints и tools. HttpOnly cookie задаёт scope анонимной сессии; клиент сохраняет cookie и работает через same-origin gateway. Схемы и точные поля — contracts/generated/openapi.json.


## Существующий backend до адаптации

В коммитах `a8a34fb` и `9860154` реализованы прежние DTO, `/api/v1/analyses`, `/jobs/{id}`, `/runs/{id}`, `/scenario-analyses` и экспорт сохранённых runs. Поведение, проверки и команды зафиксированы в `apps/api/README.md` и `apps/api/docs/stage2-handoff.md`. Это историческое состояние реализации, а не замена целевого контракта выше. При переходе на v2 сохраняются идемпотентность, атомарность публикации, версии/evidence и явные ошибки; изменяемые поля и маршруты передаются FRONTEND/ML1/ML2 отдельно.
