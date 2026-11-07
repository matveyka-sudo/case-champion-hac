# REST, Run, Job и Error

BACKEND владеет HTTP, общими валидаторами, сохранением результата и worker. FRONTEND и tools читают те же схемы и статусы. Все маршруты используют `/api/v1`, входы и результаты ссылаются на версии домена.

| Метод и путь | Назначение |
| --- | --- |
| GET `/profiles`, `/places`, `/destinations`, `/coverage` | Профили и параметры, места и цели, подтверждённая область и пробелы. |
| GET `/evidence`, `/evidence/{id}` | Зарегистрированные доказательства, запись, источник, даты действия, hash и статус. |
| POST `/journeys` | JourneyRequest → JourneyResult, общий engine. |
| POST `/analyses` | `bundle_id`, `profile_id`, `profile_version`, `task_set_hash`, `origin_ids`, `include_case_results` → 202 с `job_id` и статусом. |
| POST `/scenario-analyses` | ScenarioRequest → 202 с `job_id` и статусом. |
| GET `/jobs/{id}` | Текущее состояние job, готовый `run_id` либо ошибка. |
| GET `/runs/{id}` | Версионированный результат, включая ProfileResult или Comparison. F08 добавляет экспорт JSON/CSV с версиями, единицами и параметрами. |
| GET `/runs/{id}/export?format=json\|csv` | Экспорт сохранённого результата вместе с исходным request/context, версиями, hash, evidence и единицами; вычисление не запускается. |

У обоих тяжёлых POST используется `Idempotency-Key`. Повтор того же payload с тем же ключом возвращает тот же job, в том числе после завершения. Отличающийся payload под прежним ключом → HTTP 409. BACKEND выбирает стабильную нормализацию для сравнения payload. Регистрация job и идемпотентности согласованы атомарно. Кэш включает bundle, профиль, hash задач, параметры, сценарий и версию кода. Новая версия услуг меняет bundle и инвалидирует старую применимость кэша даже при прежнем расписании.

**Run.** Обязательны `run_id`, `status` (ok/partial/insufficient_data), `bundle_id`, профильные версии, task versions/hash, `code_version`, `params_hash`, `result_hash`, `provenance`, `evidence_ids`, `assumption_ids` и предупреждения. В provenance разрешаются использованные версии, данные и область, принятые допущения и причины неполноты. Scenario run также несёт `scenario_id`, связи с базами и hash операций. `partial` может содержать диапазоны, `insufficient_data` содержит причину и пропуски. Публикуемые timestamps содержат смещение, расчётные события в UTC, единицы определены в доменном результате.

**Job.** Обязательны `job_id`, `status` из queued/running/succeeded/failed/cancelled, `trace_id`, версия входов и времена, `run_id` при успехе и `error` при failure. Один worker. Polling каждые 2 с до terminal. После 90 с UI предлагает вернуться позже, job продолжает работу. Hard timeout 10 минут переводит job в failed с причиной. Рекомендованный предел демо 3500 задач × 3 версии сети, превышение → 422. Задания вне длительного HTTP. Журнал хранит версии, trace/call/run и метрики, срок по умолчанию 7 дней.

**Error.** Общий envelope содержит `error.code`, `error.message`, `error.field_errors`, `error.retryable`, `error.trace_id`. Пустой список ошибок полей допустим. Сбой маршрутизатора возвращает типизированную ошибку и не меняет бизнес-исход задачи.

| HTTP / code | Смысл |
| --- | --- |
| 422 `invalid_request` | Некорректные поля, семантика, область, операция или лимит демо. |
| 404 `unknown_id` | Неизвестный идентификатор. |
| 409 `incompatible_versions` | Несовместимые входы, базы или версии. При конфликте идемпотентности HTTP 409 с причиной о повторе ключа; код для этого подслучая фиксирует BACKEND. |
| 429 `budget_exceeded` | Исчерпан разрешённый бюджет обращения. |
| 503 `engine_unavailable` | Расчёт недоступен. |
