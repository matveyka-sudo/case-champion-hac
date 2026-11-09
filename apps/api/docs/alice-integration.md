# Alice AI Flash → Waydex backend

The existing endpoint `POST /api/v1/analyses/{analysis_id}/explanation` reads an immutable, session-scoped `AnalysisResult`. `AnalysisService.explain` validates the optional question and selection, builds the saved-result access scope and passes it to `FlashAssistant`. `SavedTools` implements the existing eight read-only tools. The model cannot start a calculation or a scenario. The response permits only server-approved narrative sentences and exact saved metric claims; model/provider failure yields a typed template explanation.

## Configuration

Set `A2_ASSISTANT_HOOK=a2_api.alice:build_assistant` for the API process. The factory reads these environment variables (and an optional local `.env` in the process working directory):

| Variable | Meaning |
| --- | --- |
| `LLM_ENABLED` | `false` by default; explicit `true` is required for live calls. |
| `YANDEX_API_KEY` | Secret API key. Loaded as `SecretStr`; never placed in source, response or logs. Missing/empty key disables AI. |
| `YANDEX_FOLDER_ID` | Yandex folder used as the SDK project and in `gpt://<folder>/aliceai-llm-flash`. Required with an enabled key. |
| `YANDEX_MODEL` | Only `aliceai-llm-flash`; any other value is a safe startup configuration error. |
| `YANDEX_BASE_URL` | Only `https://ai.api.cloud.yandex.net/v1`; another URL is rejected. |
| `A2_ASSISTANT_BUDGET_HOOK` | Optional trusted `module:function(service)` returning a `DeploymentBudget` backed by a shared durable ledger. Without it, no SDK client is created. |
| `A2_ALICE_CURRENCY` | Ledger accounting currency; the bundled public tariff currently supports `RUB`. This does not confirm the billing account currency. |
| `A2_ALICE_TARIFF_ID` | Versioned price revision; currently `aliceai-llm-flash-sync-rub-2026-11-09`. Other revisions fail closed until vetted and added. |
| `A2_ALICE_BUDGET_LIMIT` | Operator-chosen positive monetary ceiling across the deployment; no default. |
| `A2_ALICE_TOKEN_LIMIT` | Operator-chosen positive total-token ceiling, independent of money; no default. |
| `A2_ALICE_BILLING_CURRENCY_CONFIRMED` | `false` by default. Separate live gate; do not set it based on the working RUB assumption. |
| `A2_ASSISTANT_TOOLS_ENABLED` | `false` by default. Enable only after a separately authorized Flash function-calling capability check. |

`YANDEX_SERVICE_ACCOUNT_ID`, `YANDEX_API_KEY_ID` and `YANDEX_API_KEY_EXPIRES_AT` are not required for the configured API-key call. Keep `.env.example` as a template; do not put real credentials in it. Environment variables override local `.env` values. The API/worker database and calculation integration are configured separately as described in the backend README.

The factory never creates `SDKTransport` until `LLM_ENABLED=true`, `A2_ALICE_BILLING_CURRENCY_CONFIRMED=true`, a nonempty key, a valid folder, and the budget hook are present. A disabled hook still returns `FlashAssistant`, which provides a template from the saved result. The runtime creates the assistant once with its active repository and closes the transport when FastAPI stops. Misconfigured model, URL, folder or budget hook fails startup with a generic error that omits secret values. Do not set either live gate during offline verification.

## Spending guard

`DeploymentBudget.reserve` is called before **each** model attempt, including retries. It receives an opaque reservation ID, model and maximum input/output tokens; `record_usage` receives provider usage after the attempt. The assistant caps one explanation at four model calls, eight tool calls, 2000 output tokens and 90 seconds total. Retries occur only for 429/5xx and each needs a new reservation. These bounds do not replace a monetary ceiling.

Migration `0004_alice_budget` adds a singleton `assistant_budget` row and unique `assistant_reservations` rows. Neither migration nor application startup creates a budget. The bundled `a2_api.budget_hook:build_budget` shares the API PostgreSQL pool. Each reservation locks the singleton row in a transaction, checks both money and total-token ceilings, and holds the maximum possible cost before a model attempt. Reusing a reservation ID never authorizes a second call. Reservations persist across restarts and are common to all API processes using the deployment schema. There is no daily request-count limit.

The versioned public RUB tariff for synchronous Alice AI LLM Flash is [published by Yandex AI Studio](https://aistudio.yandex.ru/ru/docs/ai-studio/pricing): input 0.10 ₽, output 0.20 ₽ and cached input 0.025 ₽ per 1000 tokens. The ledger charges **all** input at the ordinary 0.10 ₽ rate; it never assumes a cache discount. Configuration selects a vetted currency/revision entry. Future verified currencies and revisions can be added without a ledger schema change. RUB is the working accounting currency; the organizers' actual billing-account currency has not been checked. Prices may change, so review the revision before live authorization.

Known usage settles at the ordinary rate and releases only unused reserve. Failed attempts, timeout, cancellation, missing usage and process crashes retain the full reserve: no assumed zero cost or automatic expiry. Reported usage above the reservation is charged and blocks new reservations for operator investigation. Database faults, absent budget row, mismatched settings and unknown tariff deny a new reservation. A failed settlement yields a safe explanation fallback and preserves the hold. Raising ceilings does not erase usage or holds; `set-limit` never lowers a ceiling or clears a blocked budget.

### Install, inspect and raise the deployment budget

Use a dedicated PostgreSQL deployment schema, apply migrations and set these **operator-selected** values in the process environment or private `.env`. Placeholders are not a proposed budget:

```sh
A2_ALICE_CURRENCY=RUB
A2_ALICE_TARIFF_ID=aliceai-llm-flash-sync-rub-2026-11-09
A2_ALICE_BUDGET_LIMIT=YOUR_APPROVED_RUB_AMOUNT
A2_ALICE_TOKEN_LIMIT=YOUR_APPROVED_TOTAL_TOKEN_COUNT
A2_ASSISTANT_HOOK=a2_api.alice:build_assistant
A2_ASSISTANT_BUDGET_HOOK=a2_api.budget_hook:build_budget
LLM_ENABLED=false
```

From the repository root with backend `PYTHONPATH`, `A2_DATABASE_URL` and `A2_DATABASE_SCHEMA` configured as in the README:

```sh
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/python -m a2_api.budget_cli status
.venv/bin/python -m a2_api.budget_cli set-limit
.venv/bin/python -m a2_api.budget_cli status
```

Before `set-limit`, status is `{"status":"unconfigured"}`. Afterwards it reports currency, tariff, money limit/charged/held/available, total token limit/charged/held/available, reservation count and a blocked flag. No request IDs or credentials appear. To increase a ceiling, raise one or both variables, run `set-limit`, then restart API processes so their settings match the row. Use a disposable test schema for experimentation. No budget is installed automatically.

## Safe local check

Use a separate writable test database with migrations and a **synthetic** saved analysis. Start the API with `LLM_ENABLED=false` and `A2_ASSISTANT_HOOK=a2_api.alice:build_assistant`. For example, after configuring the backend database and source layout from the README:

```sh
LLM_ENABLED=false A2_ASSISTANT_HOOK=a2_api.alice:build_assistant \
  .venv/bin/uvicorn a2_api.app:create_configured_app --factory --host 127.0.0.1 --port 8033
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

1. **Configuration ready:** the model, URL, folder and key can be loaded. The user reports a successful earlier SDK Flash smoke (16 input tokens, 2 output tokens); this work makes no live request.
2. **Offline integration verified:** synthetic tests use a fake SDK transport and budget, run the existing HTTP endpoint against PostgreSQL, check saved claims, session isolation, errors, cancellation, limits and transport closure.
3. **Live mode authorized:** **not yet**. The ledger and public RUB rates can be tested offline, but no operator amount/token ceiling has been selected, the actual billing currency is unknown, and `LLM_ENABLED` plus the separate currency-confirmation gate stay off. A later rollout requires an explicit authorization decision, vetted pricing/currency, deployment secrets and a separately authorized Flash tool-calling check if tools are enabled. Real analysis data and browser E2E are separate ML1/ML2/frontend dependencies.

No API endpoint or DTO field changed. Generated OpenAPI and JSON Schema remain unchanged. The existing eight tools stay on their read-only allowlist and access-scope checks; enabling model tool calling is a separate deployment decision.
