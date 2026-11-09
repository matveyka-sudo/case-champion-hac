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
| `A2_ASSISTANT_TOOLS_ENABLED` | `false` by default. Enable only after a separately authorized Flash function-calling capability check. |

`YANDEX_SERVICE_ACCOUNT_ID`, `YANDEX_API_KEY_ID` and `YANDEX_API_KEY_EXPIRES_AT` are not required for the configured API-key call. Keep `.env.example` as a template; do not put real credentials in it. Environment variables override local `.env` values. The API/worker database and calculation integration are configured separately as described in the backend README.

The factory never creates `SDKTransport` until `LLM_ENABLED=true`, a nonempty key, a valid folder, and the budget hook are present. A disabled hook still returns `FlashAssistant`, which provides a template from the saved result. The runtime creates the assistant once with its active repository and closes the transport when FastAPI stops. Misconfigured model, URL, folder or budget hook fails startup with a generic error that omits secret values.

## Spending guard

`DeploymentBudget.reserve` is called before **each** model attempt, including retries. It receives an opaque reservation ID, model and maximum input/output tokens; `record_usage` receives provider usage after the attempt. The assistant caps one explanation at four model calls, eight tool calls, 2000 output tokens and 90 seconds total. Retries occur only for 429/5xx and each needs a new reservation. These bounds do not replace a monetary ceiling.

There is currently **no production ledger, approved spending ceiling or verified tariff table**. The supplied hook must atomically reserve the worst-case monetary amount in a shared durable store across all API processes. Minimal production implementation: a PostgreSQL deployment-budget row with currency, operator-approved positive ceiling and pricing revision; a unique reservation row keyed by reservation ID; transactional row lock or equivalent atomic update that refuses reservations exceeding the ceiling; a usage record/reconciliation step that never treats missing usage as free. The owner must supply confirmed Flash input/output token prices and billing currency, define how failed/unknown attempts are charged, and test simultaneous reservations from multiple processes. An in-memory counter, `durable=True` flag alone, arbitrary price or unlimited ceiling does not satisfy this contract. No such hook is bundled or enabled here.

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
3. **Live mode authorized:** **not yet**. Requires a confirmed budget and tariff, an atomic cross-process ledger hook, its concurrency tests, deployment secrets, and a separately authorized Flash tool-calling capability check if tools are enabled. Real analysis data and browser E2E are separate ML1/ML2/frontend dependencies.

No API endpoint or DTO field changed. Generated OpenAPI and JSON Schema remain unchanged. The existing eight tools stay on their read-only allowlist and access-scope checks; enabling model tool calling is a separate deployment decision.
