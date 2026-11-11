"""The synthetic demo set is reproducible, contract-valid and matches the ML1 engine."""

import hashlib
import json
from csv import DictReader
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
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


def rows(name: str) -> list[dict[str, str]]:
    with (PUBLISHED / name).open(encoding="utf-8", newline="") as file:
        return list(DictReader(file))


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    out = root / demo.DATASET_ID
    copied_extract = root / EXTRACT.name
    copied_extract.write_bytes(EXTRACT.read_bytes())
    demo.build(out, extract_path=copied_extract)
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
        elif row["kind"] == "runtime_extract":
            assert (
                row["sha256"]
                == demo.EXTRACT_SHA256
                == hashlib.sha256(EXTRACT.read_bytes()).hexdigest()
            )


def test_extract_refuses_another_archive(tmp_path: Path) -> None:
    fake = tmp_path / "runtime.tar.gz"
    fake.write_bytes(b"not the handoff")
    with pytest.raises(ValueError, match="pinned handoff archive"):
        demo.extract(fake, tmp_path / "out.json")


def test_build_rejects_changed_observation_with_original_evidence(tmp_path: Path) -> None:
    payload = json.loads(EXTRACT.read_bytes())
    row = next(row for row in payload["road"]["rows"] if row["http_status"] == 200)
    row["time_s"] = 1.0
    changed = tmp_path / EXTRACT.name
    changed.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    out = tmp_path / demo.DATASET_ID
    with pytest.raises(ValueError, match="runtime extract sha256"):
        demo.build(out, extract_path=changed)
    assert not out.exists()


def _table(city: str) -> tuple[dict, object]:
    sid = f"demo-{city}-{demo.DATASET_VERSION}"
    receipt = load(f"receipts/{sid}-transport.json")
    snapshot = load(f"snapshots/{sid}.json")
    assert receipt["snapshot_hash"] == canonical_hash(snapshot)
    return snapshot, pydantic.TypeAdapter(engine.Timetable).validate_python(receipt["model"])


def _operation(city: str, operation: str, table: object) -> tuple[dict, object]:
    control = load(f"control/cmp-demo-{city}-urban-pt-{operation}.json")
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
    return control, replace(table, runs=runs)


@pytest.mark.parametrize("city", list(demo.CITIES))
@pytest.mark.parametrize("operation", ["coordinate_transfer", "additional_departure"])
def test_engine_reproduces_the_control_values(city: str, operation: str) -> None:
    snapshot, table = _table(city)
    control, changed = _operation(city, operation, table)
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
    start = datetime(2026, 11, 11, 8, 30, tzinfo=ZoneInfo(demo.CITIES[city]["tz"]))
    before = engine.solve_timetable(table, origin, target, start, max_wait_seconds=3600)
    after = engine.solve_timetable(changed, origin, target, start, max_wait_seconds=3600)
    assert before.best_known.total_seconds == control["before"]["total_s"]
    assert (
        after.best_known.total_seconds == control["after"]["total_s"] < control["before"]["total_s"]
    )


@pytest.mark.parametrize("city", list(demo.CITIES))
def test_receipt_matches_the_api_canonical_timetable(city: str) -> None:
    transport = pytest.importorskip("a2_api.adapters.demo_transport")
    source = pytest.importorskip("a2_api.adapters.timetable_source")
    sid = f"demo-{city}-{demo.DATASET_VERSION}"
    snapshot = AcceptedData.model_validate(load(f"snapshots/{sid}.json"))
    receipt = transport.DemoTransportReceipt.model_validate(load(f"receipts/{sid}-transport.json"))
    assert receipt.snapshot_hash == canonical_hash(snapshot.model_dump(mode="json"))
    canonical = source.verify_canonical_timetable(snapshot, receipt.model, demo.CLOCK)
    assert canonical.materialization_complete


@pytest.mark.parametrize("city", list(demo.CITIES))
def test_walk_roster_is_routable_in_public_transport(city: str) -> None:
    snapshot, table = _table(city)
    origins = {row["point_id"]: row for row in snapshot["sample_points"]}
    destinations = {row["destination_id"]: row for row in snapshot["destinations"]}
    nodes = {row["node_id"]: row for row in snapshot["access_nodes"]}
    endpoints = [*origins.values(), *destinations.values()]
    assert len({row["access_node_id"] for row in endpoints}) == len(endpoints)
    assert all(nodes[row["access_node_id"]]["location"] == row["location"] for row in endpoints)
    assert all(row["access_distance_m"] == 0 for row in origins.values())
    edges = {row["edge_id"]: row for row in snapshot["reference_edges"] if row["mode"] == "walk"}
    paths = {row.path_id: row for row in table.footpaths}
    by_nodes = {(row.from_node_id, row.to_node_id): row for row in table.footpaths}
    for roster, entities, role in (
        ("origins", origins, "point"),
        ("destinations", destinations, "destination"),
    ):
        for row in rows(f"tables/{city}/{roster}.csv"):
            identity = row[f"{role}_id"]
            physical, access = row["physical_node_id"], row["access_node_id"]
            assert physical == f"physical:{role}:{identity}"
            assert access == entities[identity]["access_node_id"]
            assert nodes[physical]["location"] == nodes[access]["location"]
            boundary = (physical, access) if role == "point" else (access, physical)
            assert by_nodes[boundary].seconds == 0 and by_nodes[boundary].usable
    start = datetime(2026, 11, 11, 8, 30, tzinfo=ZoneInfo(demo.CITIES[city]["tz"]))
    for row in rows(f"tables/{city}/walk_edges.csv"):
        origin = origins[row["origin_point_id"]]["access_node_id"]
        target = destinations[row["destination_id"]]["access_node_id"]
        edge, path = edges[row["edge_id"]], paths[row["edge_id"]]
        assert (
            (path.from_node_id, path.to_node_id, path.seconds)
            == (
                origin,
                target,
                float(row["time_s"]),
            )
            == (edge["from_node_id"], edge["to_node_id"], edge["running_time_seconds"])
        )
        assert path.usable
        assert start + timedelta(seconds=path.seconds) <= table.scope.end_at


@pytest.mark.parametrize(
    ("point_id", "destination_id", "walk_only"),
    [
        (
            "kaliningrad-utm34n-1km-v1-e466-n6065-p",
            "kaliningrad-adult_polyclinic-dce0201c87e5",
            True,
        ),
        (
            "kaliningrad-utm34n-1km-v1-e468-n6061-p",
            "kaliningrad-adult_polyclinic-dce0201c87e5",
            False,
        ),
    ],
)
def test_public_transport_uses_the_faster_or_only_walk_before_and_after(
    point_id: str, destination_id: str, walk_only: bool
) -> None:
    city = "kaliningrad"
    snapshot, table = _table(city)
    origin = f"physical:point:{point_id}"
    target = f"physical:destination:{destination_id}"
    start = datetime(2026, 11, 11, 8, 30, tzinfo=ZoneInfo(demo.CITIES[city]["tz"]))
    walk_s = float(
        next(
            r["time_s"]
            for r in rows(f"tables/{city}/walk_edges.csv")
            if (r["origin_point_id"], r["destination_id"]) == (point_id, destination_id)
        )
    )
    direct_ids = {row["edge_id"] for row in snapshot["reference_edges"] if row["mode"] == "walk"}
    _, coordinated = _operation(city, "coordinate_transfer", table)
    _, extra = _operation(city, "additional_departure", table)
    for variant in (table, coordinated, extra):
        best = engine.solve_timetable(variant, origin, target, start, max_wait_seconds=3600)
        assert best.best_known.total_seconds == walk_s
        assert not best.best_known.boardings
        without_direct = replace(
            variant, footpaths=tuple(p for p in variant.footpaths if p.path_id not in direct_ids)
        )
        other = engine.solve_timetable(without_direct, origin, target, start, max_wait_seconds=3600)
        if walk_only:
            assert other.best_known is None
        else:
            assert other.best_known.total_seconds > walk_s


@pytest.mark.parametrize("city", list(demo.CITIES))
def test_transfer_csv_matches_snapshot_and_receipt(city: str) -> None:
    snapshot, table = _table(city)
    expected = {
        (row["from_node_id"], row["to_node_id"]): row["seconds"] for row in snapshot["transfers"]
    }
    minima = {row["minimum_id"]: row["seconds"] for row in snapshot["minimum_transfers"]}
    rules = {r.rule_id: r for r in table.transfers}
    transfers = rows(f"tables/{city}/transfers.csv")
    assert len(transfers) == len(expected) == len(minima) == len(rules)
    for row in transfers:
        pair = row["from_node_id"], row["to_node_id"]
        rule = rules[row["rule_id"]]
        assert (rule.from_node_id, rule.to_node_id) == pair
        assert float(row["walk_s"]) == expected[pair]
        assert (
            float(row["minimum_seconds"])
            == expected[pair] + demo.MIN_CHANGE_S
            == minima[row["rule_id"]]
            == rule.minimum_seconds
        )


@pytest.mark.parametrize(("origin", "destination"), demo.PAIRS)
def test_intercity_cargo_control_uses_service_day_utc(origin: str, destination: str) -> None:
    manifest = load("manifest.json")
    chain = next(
        c
        for c in load("tables/intercity/freight_chains.json")
        if c["pair"] == f"{origin}--{destination}"
    )
    control = load(f"control/cmp-demo-icc-{chain['pair']}-{chain['operation_type']}.json")
    ready_at = datetime.fromisoformat(manifest["slice"]["utc"][origin]) + timedelta(
        seconds=chain["legs"][0]["run_s"] + chain["legs"][0]["handling_at_end_s"]
    )
    service_day = date.fromisoformat(manifest["slice"]["service_date"])
    zone = ZoneInfo(manifest["slice"]["timezones"][origin])
    departures = []
    for local in chain["legs"][1]["departures_local"]:
        day_offset, clock = local.split()
        day = service_day + timedelta(days=int(day_offset.removesuffix("d")))
        departures.append(datetime.combine(day, time.fromisoformat(clock), zone).astimezone(UTC))
    before = min(departure for departure in departures if departure >= ready_at)
    after = before
    if chain["operation_type"] == "add_compatible_departure":
        after = ready_at + timedelta(minutes=30)
        after += timedelta(seconds=-int(after.timestamp()) % 1800)
        assert chain["change"]["departure_utc"] == after.isoformat()
    for label, expected in (("before", before), ("after", after)):
        assert chain[label]["departure_utc"] == expected.isoformat()
        assert chain[label]["wait_s"] == (expected - ready_at).total_seconds()
        assert control[label] == chain[label]


@pytest.mark.parametrize(("origin", "destination"), demo.PAIRS)
def test_intercity_passenger_control_uses_node_to_node_boundaries(
    origin: str, destination: str
) -> None:
    preset = next(
        p for p in load("presets.json") if p["preset_id"] == f"demo-ic-{origin}--{destination}"
    )
    snapshot = load(f"snapshots/{preset['snapshot_id']}.json")
    control = load(f"control/{preset['comparison_ids'][0]}.json")
    services = {row["trip_id"]: row for row in snapshot["scheduled_services"]}
    model = control["model_inputs"]
    changed = next(row for row in services.values() if row["service_id"] == model["service_ids"][0])
    ready = datetime.fromisoformat(preset["departure_utc"])
    changed_trip = model.get("new_trip_id", changed["trip_id"])

    def timestamp(service: dict, event: dict, field: str, shift_s: int) -> datetime:
        midnight = datetime.combine(
            date.fromisoformat(service["service_dates"][0]), time(), ZoneInfo(service["timezone"])
        )
        return (midnight + timedelta(seconds=event[field] + shift_s)).astimezone(UTC)

    for label in ("before", "after"):
        journey = control[label]
        legs = []
        for trip_id in journey["trips"]:
            service = services.get(trip_id, changed)
            first, last = service["stop_times"]
            shift_s = model["shift_seconds"] if label == "after" and trip_id == changed_trip else 0
            legs.append(
                (
                    first["node_id"],
                    last["node_id"],
                    timestamp(service, first, "departure_seconds", shift_s),
                    timestamp(service, last, "arrival_seconds", shift_s),
                )
            )
        first, last = legs[0], legs[-1]
        assert journey["initial_wait_s"] == (first[2] - ready).total_seconds()
        assert journey["path_s"] == (last[3] - first[2]).total_seconds()
        assert journey["total_s"] == (last[3] - ready).total_seconds()
        assert (journey["from_node_id"], journey["to_node_id"]) == (first[0], last[1])
        assert journey["departure_utc"] == first[2].isoformat()
        assert journey["arrival_utc"] == last[3].isoformat()
        if len(legs) == 2:
            assert first[1] == last[0] == model["transfer_node_id"]
            assert journey["transfer_wait_s"] == (last[2] - first[3]).total_seconds()
            assert (
                journey["transfer_slack_s"]
                == journey["transfer_wait_s"] - model["minimum_connection_s"]
            )
