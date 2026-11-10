# Alice AI Flash → Waydex backend

The existing endpoint `POST /api/v1/analyses/{analysis_id}/explanation` reads an immutable, session-scoped `AnalysisResult`. `AnalysisService.explain` validates the optional question and selection, builds the saved-result access scope and passes it to `FlashAssistant`. `SavedTools` implements the existing eight read-only tools. The model cannot start a calculation or a scenario. Alice returns only saved metric IDs, semantic explanation codes and optional registered template IDs. The backend assembles values, units, evidence and server-owned sentences from the saved result, then validates the public ExplanationContent. Model/provider failure yields a typed template explanation. Invalid answers log only a category and Pydantic error location/type/count.

## Configuration

Set `ASSISTANT_HOOK=a2_api.assistant.alice:build_assistant` for the API process. The factory reads these environment variables and local `.env` in the process working directory:

| Variable | Meaning |
| --- | --- |
| `LLM_ENABLED` | `false` by default; explicit `true` is required for live calls. |
| `YANDEX_API_KEY` | Secret API key. Loaded as `SecretStr`; never placed in source, response or logs. Missing/empty key disables AI. |
| `YANDEX_FOLDER_ID` | Yandex folder used as the SDK project and in `gpt://<folder>/aliceai-llm-flash`. Required with an enabled key. |
| `YANDEX_MODEL` | Only `aliceai-llm-flash`; any other value is a safe startup configuration error. |
| `YANDEX_BASE_URL` | Only `https://ai.api.cloud.yandex.net/v1`; another URL is rejected. |
| `ASSISTANT_BUDGET_HOOK` | Optional trusted `module:function(service)` returning a `DeploymentBudget` backed by a shared durable ledger. Without it, no SDK client is created. |
| `ALICE_CURRENCY` | Ledger accounting currency; the bundled public tariff currently supports `RUB`. This does not confirm the billing account currency. |
| `ALICE_TARIFF_ID` | Versioned price revision; currently `aliceai-llm-flash-sync-rub-2026-11-09`. Other revisions fail closed until vetted and added. |
| `ALICE_BUDGET_LIMIT` | Operator-chosen positive monetary ceiling across the deployment; the local template uses 10. |
| `ALICE_TOKEN_LIMIT` | Operator-chosen positive total-token ceiling, independent of money; the local template uses 50000. |
| `ALICE_BILLING_CURRENCY_CONFIRMED` | `false` by default. Separate live gate; do not set it based on the working RUB assumption. |
| `ASSISTANT_TOOLS_ENABLED` | `false` by default. With tools disabled, the bundled factory allows one model call and no retry. |

`YANDEX_SERVICE_ACCOUNT_ID`, `YANDEX_API_KEY_ID` and `YANDEX_API_KEY_EXPIRES_AT` are not required for the configured API-key call. Keep `.env.example` as a template; do not put real credentials in it. Environment variables override local `.env` values. The API/worker database and calculation integration are configured separately as described in the backend README.

The factory never creates `SDKTransport` until `LLM_ENABLED=true`, `ALICE_BILLING_CURRENCY_CONFIRMED=true`, a nonempty key, a valid folder, and the budget hook are present. A disabled hook still returns `FlashAssistant`, which provides a template from the saved result. The runtime creates the assistant once with its active repository and closes the transport when FastAPI stops. Misconfigured model, URL, folder or budget hook fails startup with a generic error that omits secret values. Do not set either live gate during offline verification.

[Chat Completions](https://aistudio.yandex.ru/ru/docs/ai-studio/api/Chat-Completions/createChatCompletion) documents `response_format=json_schema` for supporting models, but does not establish Alice Flash compatibility. [Structured output](https://aistudio.yandex.ru/ru/docs/ai-studio/concepts/generation/structured-output) also cannot be combined with tools. The transport therefore leaves this parameter unset until a separate Flash capability check confirms it.

## Spending guard

`DeploymentBudget.reserve` is called before **each** model attempt, including retries. It receives an opaque reservation ID, model and maximum input/output tokens; `record_usage` receives provider usage after the attempt. With tools disabled, the bundled Alice factory allows one model call and zero retries. If tools are explicitly enabled, the assistant caps one explanation at four model calls, eight tool calls, 2000 output tokens and 90 seconds total. Retries then occur only for 429/5xx and each needs a new reservation. These bounds do not replace a monetary ceiling.

Migration `0004_alice_budget` adds a singleton `assistant_budget` row and unique `assistant_reservations` rows. Neither migration nor application startup creates a budget. The bundled `a2_api.assistant.budget:build_budget` shares the API PostgreSQL pool. Each reservation locks the singleton row in a transaction, checks both money and total-token ceilings, and holds the maximum possible cost before a model attempt. Reusing a reservation ID never authorizes a second call. Reservations persist across restarts and are common to all API processes using the deployment schema. There is no daily request-count limit.

The versioned public RUB tariff for synchronous Alice AI LLM Flash is [published by Yandex AI Studio](https://aistudio.yandex.ru/ru/docs/ai-studio/pricing): input 0.10 ₽, output 0.20 ₽ and cached input 0.025 ₽ per 1000 tokens. The ledger charges **all** input at the ordinary 0.10 ₽ rate; it never assumes a cache discount. Configuration selects a vetted currency/revision entry. Future verified currencies and revisions can be added without a ledger schema change. RUB is the working accounting currency; the organizers' actual billing-account currency has not been checked. Prices may change, so review the revision before live authorization.

Known usage settles at the ordinary rate and releases only unused reserve. Failed attempts, timeout, cancellation, missing usage and process crashes retain the full reserve: no assumed zero cost or automatic expiry. Reported usage above the reservation is charged and blocks new reservations for operator investigation. Database faults, absent budget row, mismatched settings and unknown tariff deny a new reservation. A failed settlement yields a safe explanation fallback and preserves the hold. Raising ceilings does not erase usage or holds; `set-limit` never lowers a ceiling or clears a blocked budget.

### Install, inspect and raise the deployment budget

Use a dedicated PostgreSQL deployment schema, apply migrations and set these **operator-selected** values in the process environment or private `.env`. The example uses the approved local test limits; production limits require a separate decision:

```sh
ALICE_CURRENCY=RUB
ALICE_TARIFF_ID=aliceai-llm-flash-sync-rub-2026-11-09
ALICE_BUDGET_LIMIT=10
ALICE_TOKEN_LIMIT=50000
ASSISTANT_HOOK=a2_api.assistant.alice:build_assistant
ASSISTANT_BUDGET_HOOK=a2_api.assistant.budget:build_budget
LLM_ENABLED=false
```

From the repository root with backend `PYTHONPATH` and `DATABASE_URL` configured as in the README; `DATABASE_SCHEMA` is optional and defaults to `a2_backend`:

```sh
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/python -m a2_api.assistant.budget_cli status
.venv/bin/python -m a2_api.assistant.budget_cli set-limit
.venv/bin/python -m a2_api.assistant.budget_cli status
```

Before `set-limit`, status is `{"status":"unconfigured"}`. Afterwards it reports currency, tariff, money limit/charged/held/available, total token limit/charged/held/available, reservation count and a blocked flag. No request IDs or credentials appear. To increase a ceiling, raise one or both variables, run `set-limit`, then restart API processes so their settings match the row. Use a disposable test schema for experimentation. No budget is installed automatically.

## Safe local check

Use a separate writable test database with migrations and a **synthetic** saved analysis. Start the API with `LLM_ENABLED=false` and `ASSISTANT_HOOK=a2_api.assistant.alice:build_assistant`. For example, after configuring the backend database and source layout from the README:

```sh
LLM_ENABLED=false ASSISTANT_HOOK=a2_api.assistant.alice:build_assistant \
  .venv/bin/uvicorn a2_api.api.bootstrap:build_app --factory --host 127.0.0.1 --port 8033
```

Retain the same session cookie that created the analysis. Substitute its saved ID below; these requests run in fallback mode and make **no paid model call**:

```sh
curl -sS -b waydex-cookies.txt -c waydex-cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"schema_version":"2","question":"Что известно из сохранённого анализа?"}' \
  http://127.0.0.1:8033/api/v1/analyses/SAVED_ANALYSIS_ID/explanation
```

Expected status is `template` with `reason_code=DISABLED` and `analysis_result_hash` equal to the saved run. Another session receives HTTP 404. Invalid selection receives HTTP 404 before any assistant call. Do not paste keys into a command, issue body or log. With an enabled, funded deployment, this same endpoint may make paid requests; use it only after explicit operator approval and ledger verification.

## Verification and readiness

1. **Configuration ready:** model, URL, folder and key load from private settings; `.env.example` contains no credentials and leaves LIVE off.
2. **Offline integration verified:** synthetic tests use a fake SDK transport and budget, run the existing HTTP endpoint against PostgreSQL, and check saved claims, session isolation, errors, cancellation, limits and transport closure.
3. **One isolated LIVE E2E succeeded on 2026-11-09:** a single Alice Flash call through FastAPI returned a generated explanation of synthetic saved data; the existing PostgreSQL ledger recorded usage. The server stopped and both LIVE gates were left off. The actual organizer billing currency remains unconfirmed. This test does not authorize deployment, real data, tool calling, or additional paid calls.

No public API endpoint or DTO field changed. Generated OpenAPI and JSON Schema remain unchanged. The existing eight tools stay on their read-only allowlist and access-scope checks; enabling model tool calling is a separate deployment decision.

## Расположение после структурного разделения fix-fullstack

Каноническая backend-фабрика — `a2_api.assistant.alice`, read-only adapters —
`a2_api.assistant.tools`, scoped explanation orchestration —
`a2_api.assistant.explanations`. PostgreSQL ledger/SQL —
`a2_api.db.repositories.budget`; hook/settings и CLI —
`a2_api.assistant.budget` и `a2_api.assistant.budget_cli`.
Библиотека `packages/assistant` осталась в прежнем расположении; её обработка ответа Alice AI использует минимальный DTO и серверную сборку ExplanationContent.

Текущие module:function hooks и команды выше используют только канонические
модули. `a2_api.assistant.alice.build_assistant` сохраняет точку внедрения SDKTransport;
paid-call gates, восемь read-only tools, budgets/timeouts/fallback и scope
проверки сохранены. При выключенных tools разрешён один model call без retries. Тарифы и операционные лимиты этим переносом не пересматривались.
