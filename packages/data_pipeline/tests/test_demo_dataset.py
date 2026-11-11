"""The synthetic demo set is reproducible, contract-valid and matches the ML1 engine."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("a2_contracts")
engine = pytest.importorskip("a2_engine")
pydantic = pytest.importorskip("pydantic")

from a2_contracts.common import canonical_hash  # noqa: E402
from a2_contracts.v2.dataset import AcceptedData  # noqa: E402
from a2_data_pipeline import demo_dataset as demo  # noqa: E402
from a2_data_pipeline.paths import repo_root  # noqa: E402

PUBLISHED = repo_root() / "data" / "demo" / demo.DATASET_ID
EXTRACT = repo_root() / "data" / "demo" / "inputs" / "waydex-handoff-2026-11-11-extract.json"
pytestmark = pytest.mark.skipif(not EXTRACT.is_file(), reason="runtime extract not present")


def load(name: str) -> object:
    return json.loads((PUBLISHED / name).read_text())


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo") / demo.DATASET_ID
    demo.build(out)
    return out


def test_rebuild_is_byte_identical(rebuilt: Path) -> None:
    published = sorted(p.relative_to(PUBLISHED) for p in PUBLISHED.rglob("*") if p.is_file())
    again = sorted(p.relative_to(rebuilt) for p in rebuilt.rglob("*") if p.is_file())
    assert published == again
    assert (rebuilt / "SHA256SUMS").read_bytes() == (PUBLISHED / "SHA256SUMS").read_bytes()
    for line in (PUBLISHED / "SHA256SUMS").read_text().splitlines():
        digest, path = line.split("  ", 1)
        assert hashlib.sha256((PUBLISHED / path).read_bytes()).hexdigest() == digest


def test_matrix_lists_24_presets_and_21_comparisons() -> None:
    manifest = load("manifest.json")
    presets = [p for p in manifest["presets"] if p.get("in_acceptance_matrix", True)]
    assert len(presets) == 24 and len(manifest["comparisons"]) == 21
    tabs = {}
    for p in presets:
        tabs.setdefault(p["tab"], []).append(p)
    assert {k: len(v) for k, v in tabs.items()} == {
        "in_city": 9,
        "passenger_links": 6,
        "cargo_in_city": 3,
        "cargo_between": 6,
    }
    pairs = {(p["origin_city_id"], p["destination_city_id"]) for p in tabs["passenger_links"]}
    assert pairs == set(demo.PAIRS)
    for p in tabs["in_city"]:
        assert (p["schedule_operations"] == []) == (p["passenger_mode"] != "public_transport")
    for c in manifest["comparisons"]:
        assert c["ids"]["after"] == c["ids"]["before"] + c["created_ids"]
        assert c["delta"]["total_s"] > 0
    assert all(q["passed"] for q in load("qa.json"))


def test_snapshots_are_accepted_data_with_demo_label() -> None:
    manifest = load("manifest.json")
    for row in manifest["snapshots"]:
        payload = json.loads((PUBLISHED / row["path"]).read_text())
        snapshot = AcceptedData.model_validate(payload)
        assert canonical_hash(snapshot.model_dump(mode="json")) == row["content_hash"]
        assert demo.DEMO_SOURCE in snapshot.qa.checks and snapshot.snapshot_id.startswith("demo-")
        model_rows = [e for e in snapshot.evidence if e.source_id == demo.DEMO_SOURCE]
        assert model_rows and all(e.source_version == demo.DATASET_ID for e in model_rows)


def test_real_inputs_are_pinned_to_the_handoff() -> None:
    manifest = load("manifest.json")
    assert manifest["handoff"]["runtime_archive_sha256"] == demo.HANDOFF["runtime_archive_sha256"]
    ext = json.loads(EXTRACT.read_text())
    pinned = {s["snapshot_id"]: s["content_hash"] for s in ext["runtime_snapshots"]}
    for row in manifest["real_inputs"]:
        if row["kind"] == "accepted_bundle":
            assert pinned[row["snapshot_id"]] == row["content_hash"]


def test_extract_refuses_another_archive(tmp_path: Path) -> None:
    fake = tmp_path / "runtime.tar.gz"
    fake.write_bytes(b"not the handoff")
    with pytest.raises(ValueError, match="pinned handoff archive"):
        demo.extract(fake, tmp_path / "out.json")


def _table(city: str) -> tuple[dict, object]:
    sid = f"demo-{city}-2026-11-11.1"
    receipt = load(f"receipts/{sid}-transport.json")
    snapshot = load(f"snapshots/{sid}.json")
    assert receipt["snapshot_hash"] == canonical_hash(snapshot)
    return snapshot, pydantic.TypeAdapter(engine.Timetable).validate_python(receipt["model"])


@pytest.mark.parametrize("city", list(demo.CITIES))
@pytest.mark.parametrize("operation", ["coordinate_transfer", "additional_departure"])
def test_engine_reproduces_the_control_values(city: str, operation: str) -> None:
    snapshot, table = _table(city)
    control = load(f"control/cmp-demo-{city}-urban-pt-{operation}.json")
    origin = next(
        p["access_node_id"]
        for p in snapshot["sample_points"]
        if p["point_id"] == control["ids"]["before"][0]
    )
    target = next(
        d["access_node_id"]
        for d in snapshot["destinations"]
        if d["destination_id"] == control["ids"]["before"][1]
    )
    params = control["fixed_parameters"]
    trip = params["service_ids"][0].replace("-svc-", "-trip-")
    shift = timedelta(seconds=params["shift_seconds"])
    run = next(r for r in table.runs if r.trip_id == trip)
    moved = replace(
        run,
        events=tuple(
            replace(e, arrival_at=e.arrival_at + shift, departure_at=e.departure_at + shift)
            for e in run.events
        ),
    )
    if operation == "additional_departure":
        runs = (*table.runs, replace(moved, trip_id=control["created_ids"][0]))
    else:
        runs = tuple(moved if r.trip_id == trip else r for r in table.runs)
    start = datetime(2026, 11, 11, 8, 30, tzinfo=ZoneInfo(demo.CITIES[city]["tz"]))
    before = engine.solve_timetable(table, origin, target, start, max_wait_seconds=3600)
    after = engine.solve_timetable(
        replace(table, runs=runs), origin, target, start, max_wait_seconds=3600
    )
    assert before.best_known.total_seconds == control["before"]["total_s"]
    assert (
        after.best_known.total_seconds == control["after"]["total_s"] < control["before"]["total_s"]
    )


def test_moscow_receipt_passes_the_api_demo_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = pytest.importorskip("a2_api.adapters.demo_transport")
    source = pytest.importorskip("a2_api.adapters.timetable_source")
    monkeypatch.setenv("A2_DEMO_TRANSPORT_ENABLED", "true")
    for city in demo.CITIES:
        sid = f"demo-{city}-2026-11-11.1"
        snapshot = AcceptedData.model_validate(load(f"snapshots/{sid}.json"))
        receipt = transport.DemoTransportReceipt.model_validate(
            load(f"receipts/{sid}-transport.json")
        )
        canonical = source.verify_canonical_timetable(snapshot, receipt.model, demo.CLOCK)
        assert canonical.materialization_complete
        if city == "moscow":
            assert (
                transport.verify_demo_model(snapshot, receipt.model, demo.CLOCK, receipt) == receipt
            )
        else:  # the API demo guard admits demo-moscow-* only (BACKEND opt-in needed)
            with pytest.raises(ValueError, match="DEMO_TRANSPORT_SNAPSHOT_REQUIRED"):
                transport.verify_demo_model(snapshot, receipt.model, demo.CLOCK, receipt)
