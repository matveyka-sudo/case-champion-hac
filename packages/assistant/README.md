# Bounded natural explanations of saved results

The assistant accepts a Russian question and the current saved-result selection.
The model can now write a question-specific qualitative paragraph, bounded to
650 characters, and select up to three existing metric/score IDs. Every selected
fact must appear as an opaque `[[metric:ID]]` or `[[score:ID]]` placeholder. The
server substitutes exact saved values and builds typed claims and citations;
the model never supplies those values. The earlier eight server-owned answer
modes remain compatible when the model returns only an intent/ID selection.

Examples include “Почему такая оценка?”, “Откуда берётся H?”, “Какой участок
дольше?”, “Почему вместо числа нет данных?” and “Сравни эти две сохранённые точки”.
The optional `[[comparison]]`, `[[journey]]`, `[[methodology]]` and `[[coverage]]`
atoms expand into server-owned factual clauses. Comparisons use stored values
with the same meaning, units and mode; they never create an index or numerical
scenario effect. Journey clauses use only a metric's linked saved journey and
never combine different chains. Model evidence IDs must belong to the selected
saved facts; the server owns the actual citations.

No public DTO was added. `paragraph`, `evidence_ids` and `answer_mode` are internal
model fields; legacy ID/status selections remain valid. Unknown, partial,
observed-route, demo and ranking restrictions are always appended by the server.
Raw numbers (including written forms), links, foreign placeholders, unsupported
causes, forecasts, comparisons, coverage/ranking promotions and some common
contradictions are rejected with the existing safe template fallback. Conservative
lexical screening is not a semantic entailment proof: arbitrary paraphrases can
evade it, and valid wording can be rejected. This is bounded natural generation,
not acceptance of unrestricted factual prose. Unsupported prices, causes,
forecasts and new numerical calculations need additional evidence or a separate
authorized calculation. No second verifier or model call was added.

Generated prose carries a private in-process provenance witness binding the
whole public output to the selected saved facts, linked journeys, request,
versions, snapshot, limitations and all evidence references. Server rendering
checks the witness before public DTO revalidation, then validates every typed
claim again. Python copies preserve the witness and mutations are rejected.
The existing service returns Explanation JSON directly to the frontend; no
server explanation cache/reload exists. Public JSON deliberately omits the
witness: a client-returned generated paragraph cannot establish server rendering
trust from JSON alone. Such a future reload path would need its own trusted
persistence boundary. The witness is local provenance, not a signature against
arbitrary trusted Python code.

The existing `get_analysis` read-only tool optionally accepts `selection` for a
second saved entity. The API rechecks ownership, the assistant checks the saved
result hash, and only references actually exposed by the bounded reply can enter
claims. Tool-supplied numeric values are never authoritative. Without tools, an
unavailable second fact is explained rather than guessed. Selected-city totals
remain visible even after a long list of point records.

The configured money, call, retry, token and timeout limits are unchanged. No
scenario runs automatically. Private-source results continue using the local
template path with a final current-permission check; this change does not enable
private model egress or any transport provider. Each explanation uses the current
question and selection, not an unsent conversation history.

Pre-analysis general help is a separate UI/API concern: the current explanation
endpoint requires an authorized saved analysis ID. The capabilities endpoint can
supply local onboarding copy; a no-analysis chat endpoint would need a separate
BACKEND contract. This package does not manufacture an analysis to provide help.
