# Bounded saved-result question checkpoint

This first checkpoint accepts a Russian question and the current saved-result selection. The
model chooses a small internal answer plan and existing metric/score IDs; the
server assembles a paragraph about the score, methodology and H, a saved journey,
coverage, a possible separate scenario, or two comparable saved values. Questions
are not restricted to the UI suggestion buttons, but the prose still comes from
eight server-owned answer modes. This does not complete arbitrary conversational
generation. Unsupported causes, prices,
forecasts and new numerical calculations receive an explicit limitation.

Examples include “Почему такая оценка?”, “Откуда берётся H?”, “Какой участок
дольше?”, “Почему вместо числа нет данных?” and “Сравни эти две сохранённые точки”.
The comparison describes stored values with the same meaning, units and mode;
it never creates an index or a numerical scenario effect. Journey explanations
use only a metric's linked saved journey and never combine different chains.

No public DTO was added. `answer_mode` is internal to the model response; legacy
ID/status selections remain valid. Every number is copied by the server from
the immutable result into typed claims. Server prose is reconstructed again
before rendering, including partial, observed-route and synthetic-model warnings.

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
