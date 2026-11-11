"""Calendar, physical identity and complete-chain normalization on synthetic GTFS."""

import csv
import hashlib
import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline.bundle import BundleData, TimetableRef
from a2_data_pipeline.contract_v2 import to_accepted_data_v2
from a2_data_pipeline.gtfs_inputs import _clock, normalize_gtfs, publish_normalized_inputs
from synthetic_bundle import EVIDENCE, QA, collections, evidence, manifest


def fixture(
    tmp_path: Path, *, bad_stop: bool = False, frequency: str | None = None
) -> tuple[BundleData, TimetableRef, Path]:
    tables = {
        "agency.txt": [["agency_id", "agency_timezone"], ["a", "Europe/Moscow"]],
        "routes.txt": [["route_id", "agency_id", "route_type"], ["r", "a", "3"]],
        "trips.txt": [["route_id", "service_id", "trip_id"], ["r", "c", "t"]],
        "calendar.txt": [
            [
                "service_id",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "start_date",
                "end_date",
            ],
            ["c", "1", "1", "1", "1", "1", "1", "1", "20261101", "20261130"],
        ],
        "calendar_dates.txt": [["service_id", "date", "exception_type"], ["c", "20261111", "2"]],
        "stop_times.txt": [
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
            ["t", "23:55:00", "23:56:00", "s1", "1"],
            ["t", "25:05:00", "25:05:00", "s2", "2"],
        ],
    }
    if frequency is not None:
        tables["frequencies.txt"] = [
            ["trip_id", "start_time", "end_time", "headway_secs", "exact_times"],
            ["t", "06:00:00", "07:00:00", "600", frequency],
        ]
    if bad_stop:
        tables["trips.txt"].append(["r", "c", "bad"])
        tables["stop_times.txt"].extend(
            [
                ["bad", "10:00:00", "10:00:00", "s1", "1"],
                ["bad", "11:00:00", "11:00:00", "unknown-stop", "2"],
            ]
        )
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in tables.items():
            stream = io.StringIO(newline="")
            csv.writer(stream).writerows(rows)
            archive.writestr(name, stream.getvalue())
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    ref = TimetableRef(
        timetable_id="synthetic-table",
        format="gtfs",
        path="processed/synthetic.zip",
        sha256=checksum,
        dataset_ids=["synthetic-v1"],
        valid_from=date(2026, 11, 1),
        valid_to=date(2026, 11, 30),
        unknown_dates=[date(2026, 11, 4)],
        modes=["bus"],
        qa_report="synthetic.json",
        limitations=["synthetic"],
    )
    parts: dict[str, Any] = collections()
    parts["evidence"].append({**evidence("gtfs:synthetic-table"), "content_hash": checksum})
    parts["transit_stops"] = [
        {
            "stop_id": f"synthetic-table:{sid}",
            "timetable_id": "synthetic-table",
            "gtfs_stop_id": sid,
            "name": sid,
            "lon": lon,
            "lat": 54.005,
            "access_node_id": "walk:way:1:0.0",
            "access_distance_m": 5,
            "status": "accepted",
            "quarantine_reasons": [],
            "evidence_ids": [EVIDENCE, "gtfs:synthetic-table"],
        }
        for sid, lon in [("s1", 20.004), ("s2", 20.006)]
    ]
    model = manifest().model_copy(update={"timetables": [ref]})
    return BundleData.model_validate({"manifest": model, "qa_report": QA, **parts}), ref, path


def normalize(tmp_path: Path, **options: Any) -> Any:
    bundle, ref, path = fixture(tmp_path, bad_stop=options.pop("bad_stop", False))
    return normalize_gtfs(
        bundle,
        ref,
        path,
        route_ids=("r",),
        start=date(2026, 11, 10),
        end=date(2026, 11, 12),
        **options,
    )


def test_calendar_utc_overnight_chain_and_physical_stops(tmp_path: Path) -> None:
    result = normalize(tmp_path)
    service = result.accepted_data.scheduled_services[0]
    assert service.service_dates == [date(2026, 11, 10), date(2026, 11, 12)]
    assert service.stop_times[1].arrival_seconds == 90300  # 25:05 remains service-day time
    run = next(r for r in result.timetable.runs if r.service_date == date(2026, 11, 10))
    assert run.events[-1].arrival_at == datetime(2026, 11, 10, 22, 5, tzinfo=UTC)
    assert len({e.node_id for e in run.events}) == 2  # both source stops shared one walk projection
    assert all(e.node_id != "walk:way:1:0.0" for e in run.events)
    assert not result.timetable.footpaths and not result.timetable.transfers
    assert result.timetable.scope.schedule_complete is False
    assert result.timetable.scope.footpaths_complete is False
    assert result.timetable.scope.calendar_complete is True
    node_ids = {n.node_id for n in result.accepted_data.access_nodes}
    assert set(result.timetable.scope.node_ids) <= node_ids


def test_repeatability_and_parent_snapshot_unchanged(tmp_path: Path) -> None:
    bundle, ref, path = fixture(tmp_path)
    before = bundle.model_dump_json()
    one = normalize_gtfs(
        bundle, ref, path, route_ids=("r",), start=date(2026, 11, 10), end=date(2026, 11, 10)
    )
    two = normalize_gtfs(
        bundle, ref, path, route_ids=("r",), start=date(2026, 11, 10), end=date(2026, 11, 10)
    )
    assert one == two
    assert one.timetable.runs[0].run_id == two.timetable.runs[0].run_id
    assert one.accepted_data.snapshot_id != bundle.manifest.bundle_id
    assert bundle.model_dump_json() == before
    assert (
        one.accepted_data.population_cells
        == type(one.accepted_data).model_validate(to_accepted_data_v2(bundle)).population_cells
    )


def test_unaccepted_stop_excludes_whole_chain(tmp_path: Path) -> None:
    result = normalize(tmp_path, bad_stop=True)
    assert result.report["excluded_trips"] == {"WHOLE_TRIP_UNACCEPTED_STOP_OR_CHAIN": 1}
    assert len(result.accepted_data.scheduled_services) == 1
    assert len(result.timetable.runs) == 2


@pytest.mark.parametrize("case", ["hash", "holiday", "ambiguous", "limit", "outside"])
def test_unverified_or_unbounded_inputs_are_rejected(tmp_path: Path, case: str) -> None:
    bundle, ref, path = fixture(tmp_path)
    kwargs: dict[str, Any] = {
        "route_ids": ("r",),
        "start": date(2026, 11, 10),
        "end": date(2026, 11, 12),
    }
    if case == "hash":
        path.write_bytes(b"different")
    if case == "holiday":
        kwargs.update(start=date(2026, 11, 4), end=date(2026, 11, 4))
    if case == "ambiguous":
        kwargs["ambiguous_route_ids"] = frozenset({"r"})
    if case == "limit":
        kwargs["max_runs"] = 1
    if case == "outside":
        kwargs["end"] = date(2026, 12, 1)
    with pytest.raises(ValueError):
        normalize_gtfs(bundle, ref, path, **kwargs)


def test_gtfs_noon_minus_twelve_elapsed_hours_on_dst_change() -> None:
    assert _clock(date(2026, 10, 25), 0, "Europe/Berlin") == datetime(2026, 10, 24, 23, tzinfo=UTC)


@pytest.mark.parametrize("exact_times", ["0", "1", ""])
def test_frequency_templates_are_never_promoted_to_dated_executions(
    tmp_path: Path, exact_times: str
) -> None:
    bundle, ref, path = fixture(tmp_path, frequency=exact_times)
    with pytest.raises(ValueError, match="FREQUENCY_BASED_GTFS_TRIPS_UNSUPPORTED"):
        normalize_gtfs(
            bundle, ref, path, route_ids=("r",), start=date(2026, 11, 10), end=date(2026, 11, 10)
        )


def test_published_inputs_preserve_typed_events_and_refuse_overwrite(tmp_path: Path) -> None:
    from a2_contracts.v2 import AcceptedData
    from a2_engine import Timetable
    from pydantic import TypeAdapter

    result = normalize(tmp_path)
    target = tmp_path / "published"
    one = publish_normalized_inputs(result, target)
    assert publish_normalized_inputs(result, target) == one
    assert (
        AcceptedData.model_validate_json((target / "accepted-data.json").read_bytes())
        == result.accepted_data
    )
    assert (
        TypeAdapter(Timetable).validate_json((target / "timetable.json").read_bytes())
        == result.timetable
    )
    (target / "timetable.json").write_bytes(b"different")
    with pytest.raises(FileExistsError, match="IMMUTABLE"):
        publish_normalized_inputs(result, target)
    assert (target / "timetable.json").read_bytes() == b"different"
