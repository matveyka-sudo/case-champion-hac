# Loading inputs for a user request

Startup `registry()` still validates the full hash-pinned product manifest,
snapshots, profiles, graph bindings and scenario documents. A request previously
repeated this full load in API preflight and again in the worker, even when it
selected only one city and one freight profile.

`load_runtime(request=..., snapshot_id=...)` now reads the pinned manifest and
loads the selected snapshot and every matching profile, using the same predicate
as final dispatch. It does not select the first match: overlaps remain errors.
Selected file bytes, hashes, evidence, graph bindings and semantic checks are
validated anew. No mutable parsed-object cache or timestamp shortcut is used.
Global manifest identity uniqueness is still checked. Unrelated source files are
not opened in this request path; full-release readiness remains the startup gate.

Profiles with scenario documents retain the full loader. Their documents can
validate requests at times other than the baseline, so limiting their overlap
checks to the baseline's matching profiles would be incorrect. This optimization
does not yet accelerate those profiles.

On 2026-11-11, the real 33-profile, eight-snapshot runtime was measured in separate
containers using the same image/environment and read-only dataset mounts, with
network access disabled. The candidate mounted only the changed adapter source.
No routing or database operation was included in these loader measurements:

| Selected cargo city | Full loader, seconds | Scoped loader, seconds |
| --- | ---: | ---: |
| Novorossiysk | 11.998 | 0.152 |
| Kaliningrad | 11.276 | 0.051 |
| Moscow | 13.397 | 0.191 |

Each scoped load read one snapshot and one profile; selected profile identities
matched the full loads. These are single-run engineering measurements on the
local Docker host, not latency percentiles or whole-job performance claims.
Regression tests exercise the actual preflight/worker call sites, changed selected
bytes after prior admission, scoped/full result equality, overlapping profiles,
duplicate manifest identities and inconsistent snapshot selection.
