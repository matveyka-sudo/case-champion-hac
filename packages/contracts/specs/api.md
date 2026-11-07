# План HTTP API
Это контракт для реализации BACKEND, endpoints пока отсутствуют.
POST /api/v1/analyses → 202 {job_id}; GET /api/v1/jobs/{job_id} → queued|running|succeeded|failed; GET /api/v1/analyses/{analysis_id} → AnalysisResult.
POST /api/v1/scenarios/{scenario_id}/runs → 202 {job_id}; GET /api/v1/scenario-runs/{run_id} → ScenarioResult.
Validation error: code, message, field_errors, request_id; provider error не конвертировать в transport_unavailable. Анализ регистрируется идемпотентно, тяжёлый расчёт в worker. Кеш учитывает дату/время, географию, режим, категории, профиль, версии snapshot и методики. Ключи провайдера на сервере.
