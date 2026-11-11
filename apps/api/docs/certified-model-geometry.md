# Certified canonical walking geometry

The certified executor can attach geometry to the existing bounded airport model
without changing its numerical operands. The source remains the reviewed native
97-edge path with canonical approach links. It does not establish access through
every physical airport entrance. The result stays partial, the city index stays
unknown, and ranking stays disabled.

`ProofBinding.geometry` is an optional hash-pinned `FileRef`. Existing bindings
omit it and retain their original fingerprints and duration-only behavior. Its
JSON document has this private shape:

```json
{
  "schema_version": "waydex-certified-walk-geometry-binding/1",
  "receipt": {
    "path": "geometry/receipt.json",
    "sha256": "89dcd9238cf1289e243030611303bb2a964b819a978e51aba9bf83a2045a4eab"
  },
  "original_snapshot": {
    "path": "accepted-data.json",
    "sha256": "f616122d81a297917d49fa64bec39a9876c20ef385469a3f4a7cbdf9b271b82d"
  }
}
```

The snapshot's file SHA is distinct from its API canonical hash `e3ac0868…4930`.
The loader checks both against the original binding and the geometry receipt.
The engine's compatibility document separately uses its existing ASCII canonical
convention `7c57ada6…bebd9`. These identities are not interchangeable.

The loader verifies all 32 archived files, the exact original native inventory,
the currently imported geometry verifier, and equality of original binding core
fields. The copied original binding has no geometry field; its existing digest
does not depend on the new supplement. A cumulative 16 MiB geometry/snapshot
budget bounds metadata loading; the snapshot additionally uses the registry's
accepted-document limit rather than the 1 MiB small-metadata limit.

The calculation child recomputes the native/turn/physical minimum and reuses its
single parsed graph for `load_certified_walk_geometry`. It verifies the exact
sequence, direction, clipping and physical approach chain. Saved receipt output
flags do not authorize a result. API preflight hashes metadata without invoking
the native solver, geometry replay or an external provider.

`minimum_journey_seconds` remains exactly `13773.627798430474`, unlinked to a
Journey, and remains the category score operand. The score is unchanged at
`0.4971801994143471`. A separate partial `total_seconds` metric links the displayed
Journey. Its timeline is rounded once from the rational minimum to the nearest
integer microsecond: `13773.627798` seconds. A single walking leg uses the same
elapsed timestamps and duration. `MODEL_TIMELINE_MICROSECOND_PRECISION` discloses
this representation; no DTO tolerance or scoring formula changes. Geometry keeps
the native six-decimal tile encoding and explicit canonical-access limitation.

`apps/api/scripts/attach_certified_walk_geometry.py` creates a new runtime
directory from an original immutable runtime and a reviewed geometry run. It
preserves original manifest/settings in `provenance/`, updates composite profile
fingerprints and emits the existing five runtime environment settings. It does
not route, enable providers, migrate a database or restart a stack. Full proof
replay still runs in the calculation child. The original runtime is untouched.

The preserved-source regression supports restored archives on another machine:

```sh
A2_TEST_CERTIFIED_MODEL_RUNTIME_PATH=/restored/original-runtime \
A2_TEST_CERTIFIED_GEOMETRY_RUN_PATH=/restored/geometry \
pytest apps/api/tests/test_certified_model_geometry.py -q
```

These paths must contain the original `proof-binding.json` runtime and the exact
reviewed geometry `receipt.json` inventory. Tests verify the pinned receipt SHA
and all source bindings. When neither override is configured, a missing local
archive skips only the real replay with the required paths in its reason.
An explicitly configured missing archive fails. Synthetic metadata tests never
establish proof readiness; no test downloads artifacts or calls a provider.
