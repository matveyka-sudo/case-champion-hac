# План HTTP API
Это контракт для реализации BACKEND, endpoints пока отсутствуют.
POST /api/v1/analyses → 202 {job_id}; GET /api/v1/jobs/{job_id} → queued|running|succeeded|failed; GET /api/v1/analyses/{analysis_id} → AnalysisResult.
POST /api/v1/scenarios/{scenario_id}/runs → 202 {job_id}; GET /api/v1/scenario-runs/{run_id} → ScenarioResult.
Validation error: code, message, field_errors, request_id; provider error не конвертировать в transport_unavailable. Анализ регистрируется идемпотентно, тяжёлый расчёт в worker. Кеш учитывает дату/время, географию, режим, категории, профиль, версии snapshot и методики. Ключи провайдера на сервере.

GET /api/v1/capabilities → доступные города/даты/режимы/версии и причины недоступности. GET /api/v1/territories?level=&query= → справочник. POST /api/v1/analyses/{id}/explanation → объяснение сохранённого результата. GET /api/v1/analyses/{id}/scenarios → сохранённые предложения без пересчёта.
Idempotency-Key обязателен для POST analyses/scenario runs; повтор с другим payload →409. Jobs добавляют cancelled; cancellation endpoint POST /api/v1/jobs/{id}/cancel. Access scope на analysis_id проверяется во всех endpoints и tools. API пока не реализован.
