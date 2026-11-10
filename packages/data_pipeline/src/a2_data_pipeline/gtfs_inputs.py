"""Bounded accepted GTFS services and engine events, without invented transfers.

Physical GTFS stops are separate nodes from their snapped walking access nodes.
The derivative snapshot is identified by all immutable inputs and the complete
route/date selection. It retains the parent population and incomplete coverage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from a2_contracts.v2 import AcceptedData
from a2_engine import Timetable, TimetableEvent, TimetableRun, TimetableScope
from pydantic import TypeAdapter

from .bundle import BundleData, TimetableRef
from .contract_v2 import to_accepted_data_v2
from .gtfs import ROUTE_TYPES, _date, _seconds, service_dates
from .paths import data_dir
from .runtime_inputs import _hash, _path, _publish_directory
from .store import load_bundle

RULES = "bounded-gtfs-inputs-v1"
GTFS_REFERENCE = "https://gtfs.org/documentation/schedule/reference/"


@dataclass(frozen=True)
class NormalizedGtfs:
    accepted_data: AcceptedData
    timetable: Timetable
    report: dict[str, Any]


class StopTimeRow(TypedDict):
    node_id: str
    sequence: int
    arrival_seconds: int
    departure_seconds: int
    pickup: bool
    dropoff: bool


def _records(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    if name not in archive.namelist():
        return []
    with archive.open(name) as stream:
        return list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")))


def _clock(service_day: date, seconds: int, timezone: str) -> datetime:
    # GTFS elapsed service time starts at local noon minus 12 elapsed hours.
    # This also preserves >24h values and the standard's DST-day semantics.
    noon = datetime.combine(service_day, time(12), ZoneInfo(timezone)).astimezone(UTC)
    return noon - timedelta(hours=12) + timedelta(seconds=seconds)


def _permission(value: str) -> bool:
    if value in {"", "0"}:
        return True  # GTFS regular pickup/dropoff default, not an inferred transfer.
    if value == "1":
        return False
    raise ValueError("ON_DEMAND_PICKUP_DROPOFF_UNSUPPORTED")


def _selection(
    bundle: BundleData, ref: TimetableRef, route_ids: tuple[str, ...], start: date, end: date
) -> tuple[str, dict[str, Any]]:
    if not route_ids or len(set(route_ids)) != len(route_ids) or len(route_ids) > 4:
        raise ValueError("ONE_TO_FOUR_DISTINCT_ROUTE_IDS_REQUIRED")
    if end < start or (end - start).days > 30:
        raise ValueError("BOUNDED_SERVICE_DATE_WINDOW_REQUIRED")
    if start < ref.valid_from or end > ref.valid_to:
        raise ValueError("SERVICE_DATES_OUTSIDE_ACCEPTED_TIMETABLE")
    if any(start <= day <= end for day in ref.unknown_dates):
        raise ValueError("UNKNOWN_SERVICE_DATE_IN_SELECTION")
    selection = {
        "rules": RULES,
        "base_snapshot_id": bundle.manifest.bundle_id,
        "timetable_id": ref.timetable_id,
        "sha256": ref.sha256,
        "route_ids": sorted(route_ids),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "parent_accepted_data_sha256": hashlib.sha256(
            json.dumps(
                to_accepted_data_v2(bundle),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    return f"{bundle.manifest.bundle_id}:gtfs:{digest[:20]}", selection


def normalize_gtfs(
    bundle: BundleData,
    ref: TimetableRef,
    archive_path: Path,
    *,
    route_ids: tuple[str, ...],
    start: date,
    end: date,
    max_runs: int = 10_000,
    ambiguous_route_ids: frozenset[str] = frozenset(),
) -> NormalizedGtfs:
    """Normalize complete trip chains in a bounded accepted source selection.

    No event chain is truncated to a boundary or departure time. Trips with an
    unaccepted physical stop are excluded as whole chains and explicitly counted.
    Calendar exceptions override weekdays; unknown holidays are rejected.
    """
    if ref not in bundle.manifest.timetables or bundle.manifest.acceptance_state != "accepted":
        raise ValueError("ACCEPTED_TIMETABLE_REFERENCE_REQUIRED")
    if _hash(archive_path) != ref.sha256:
        raise ValueError("GTFS_SHA256_MISMATCH")
    if set(route_ids) & ambiguous_route_ids:
        raise ValueError("GTFS_PHYSICAL_EXECUTION_IDENTITY_AMBIGUOUS")
    if type(max_runs) is not int or max_runs < 1:
        raise ValueError("POSITIVE_RUN_LIMIT_REQUIRED")
    snapshot_id, selection = _selection(bundle, ref, route_ids, start, end)
    evidence_id = f"gtfs:{ref.timetable_id}"
    source = next(
        (
            e
            for e in bundle.evidence
            if e.evidence_id == evidence_id
            and e.content_hash == ref.sha256
            and e.verification_state == "verified"
        ),
        None,
    )
    if source is None:
        raise ValueError("GTFS_EVIDENCE_NOT_BOUND_TO_ARCHIVE")
    if len(bundle.manifest.geographic_scope) != 1:
        raise ValueError("SINGLE_CITY_STOP_ASSIGNMENT_REQUIRED")
    stops = {
        s.gtfs_stop_id: s
        for s in bundle.transit_stops
        if s.timetable_id == ref.timetable_id and s.status == "accepted"
    }
    with zipfile.ZipFile(archive_path) as archive:
        routes = {r["route_id"]: r for r in _records(archive, "routes.txt")}
        if set(route_ids) - routes.keys():
            raise ValueError("UNKNOWN_GTFS_ROUTE")
        agencies = {a["agency_id"]: a for a in _records(archive, "agency.txt")}
        calendars = {c["service_id"]: c for c in _records(archive, "calendar.txt")}
        exceptions: dict[str, dict[date, str]] = defaultdict(dict)
        for row in _records(archive, "calendar_dates.txt"):
            day = _date(row["date"])
            if day is None or row["exception_type"] not in {"1", "2"}:
                raise ValueError("INVALID_CALENDAR_EXCEPTION")
            if day in exceptions[row["service_id"]]:
                raise ValueError("DUPLICATE_CALENDAR_EXCEPTION")
            exceptions[row["service_id"]][day] = row["exception_type"]
        trips = {
            t["trip_id"]: t for t in _records(archive, "trips.txt") if t["route_id"] in route_ids
        }
        if any(
            t["service_id"] not in calendars and t["service_id"] not in exceptions
            for t in trips.values()
        ):
            raise ValueError("GTFS_TRIP_CALENDAR_UNKNOWN")
        dates = {
            sid: service_dates(calendars.get(sid), exceptions[sid], start, end)
            for sid in {t["service_id"] for t in trips.values()}
        }
        trips = {tid: t for tid, t in trips.items() if dates[t["service_id"]]}
        frequency_trips = {row["trip_id"] for row in _records(archive, "frequencies.txt")}
        if trips.keys() & frequency_trips:
            # stop_times are offsets for these templates, not dated executions.
            # exact_times=1 also needs explicit expansion and distinct run IDs.
            raise ValueError("FREQUENCY_BASED_GTFS_TRIPS_UNSUPPORTED")
        if sum(len(dates[t["service_id"]]) for t in trips.values()) > max_runs:
            raise ValueError("GTFS_RUN_LIMIT_EXCEEDED")
        chains: dict[str, list[dict[str, str]]] = defaultdict(list)
        with archive.open("stop_times.txt") as stream:
            for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")):
                if row["trip_id"] in trips:
                    chains[row["trip_id"]].append(row)
    payload = to_accepted_data_v2(bundle)
    payload["snapshot_id"] = snapshot_id
    payload["provenance"]["calendar_version"] = f"{RULES}:{ref.sha256}"
    payload["scheduled_services"] = []
    nodes: dict[str, dict[str, Any]] = {}
    runs = []
    excluded: Counter[str] = Counter()
    for trip_id, trip in sorted(trips.items()):
        rows = sorted(chains[trip_id], key=lambda row: int(row["stop_sequence"]))
        if len(rows) < 2 or any(row["stop_id"] not in stops for row in rows):
            excluded["WHOLE_TRIP_UNACCEPTED_STOP_OR_CHAIN"] += 1
            continue
        route = routes[trip["route_id"]]
        agency_id = route.get("agency_id", "")
        agency = agencies.get(agency_id) if agency_id else next(iter(agencies.values()), None)
        if agency is None or (not agency_id and len(agencies) != 1):
            raise ValueError("UNAMBIGUOUS_AGENCY_TIMEZONE_REQUIRED")
        timezone = agency["agency_timezone"]
        ZoneInfo(timezone)
        transport_type = ROUTE_TYPES.get(route["route_type"])
        if transport_type not in ref.modes:
            raise ValueError("UNACCEPTED_GTFS_TRANSPORT_MODE")
        stop_times: list[StopTimeRow] = []
        for row in rows:
            stop = stops[row["stop_id"]]
            node_id = f"gtfs-stop:{ref.timetable_id}:{stop.gtfs_stop_id}"
            nodes[node_id] = {
                "node_id": node_id,
                "kind": "gtfs_stop",
                "territory_id": bundle.manifest.geographic_scope[0],
                "location": {"lon": stop.lon, "lat": stop.lat},
                "source_refs": [evidence_id],
            }
            arrival, departure = _seconds(row["arrival_time"]), _seconds(row["departure_time"])
            if arrival is None or departure is None:
                raise ValueError("EXPLICIT_GTFS_TIMES_REQUIRED")
            stop_times.append(
                {
                    "node_id": node_id,
                    "sequence": int(row["stop_sequence"]),
                    "arrival_seconds": arrival,
                    "departure_seconds": departure,
                    "pickup": _permission(row.get("pickup_type", "")),
                    "dropoff": _permission(row.get("drop_off_type", "")),
                }
            )
        active_dates = sorted(dates[trip["service_id"]])
        service = {
            "service_id": f"gtfs-service:{ref.timetable_id}:{trip_id}",
            "trip_id": trip_id,
            "mode": "public_transport",
            "timezone": timezone,
            "service_dates": [d.isoformat() for d in active_dates],
            "trip_identity_confirmed": True,
            "stop_times": stop_times,
            "source_refs": [evidence_id],
        }
        payload["scheduled_services"].append(service)
        for day in active_dates:
            events = tuple(
                TimetableEvent(
                    sequence=e["sequence"],
                    node_id=e["node_id"],
                    arrival_at=_clock(day, e["arrival_seconds"], timezone),
                    departure_at=_clock(day, e["departure_seconds"], timezone),
                    pickup=e["pickup"],
                    dropoff=e["dropoff"],
                    evidence_refs=(evidence_id,),
                )
                for e in stop_times
            )
            runs.append(
                TimetableRun(
                    snapshot_id=snapshot_id,
                    source_id=source.source_id,
                    trip_id=trip_id,
                    service_date=day,
                    timezone=timezone,
                    transport_type=transport_type,
                    events=events,
                    active=True,
                    calendar_evidence_refs=(evidence_id,),
                )
            )
    if not runs:
        raise ValueError("NO_ACCEPTED_DATED_TRIPS_IN_SELECTION")
    payload["access_nodes"].extend(nodes[node_id] for node_id in sorted(nodes))
    payload["qa"]["checks"].extend(
        [
            "bounded_gtfs_archive_sha256",
            "bounded_gtfs_calendar_exceptions",
            "bounded_gtfs_whole_chains",
            "bounded_gtfs_physical_stop_identity",
        ]
    )
    accepted = AcceptedData.model_validate(payload)
    scope = TimetableScope(
        snapshot_id=snapshot_id,
        graph_version=accepted.provenance.graph_version,
        calendar_version=accepted.provenance.calendar_version,
        timetable_version=f"{RULES}:{ref.sha256}",
        footpath_version="not_collected",
        transfer_version="not_collected",
        timezone=accepted.timezone,
        start_at=min(
            _clock(start, 0, accepted.timezone), min(e.arrival_at for r in runs for e in r.events)
        ),
        end_at=max(
            _clock(end + timedelta(days=1), 0, accepted.timezone),
            max(e.departure_at for r in runs for e in r.events) + timedelta(microseconds=1),
        ),
        node_ids=frozenset(nodes),
        schedule_complete=False,
        calendar_complete=True,
        footpaths_complete=False,
        transfers_complete=False,
        continuations_complete=False,
        evidence_refs=(evidence_id,),
        limitations=("BOUNDED_GTFS_ROUTE_DATE_SUBSET", "GROUND_TRANSPORT_ONLY"),
    )
    return NormalizedGtfs(
        accepted,
        Timetable(scope, tuple(runs), (), ()),
        {
            **selection,
            "snapshot_id": snapshot_id,
            "services": len(accepted.scheduled_services),
            "dated_runs": len(runs),
            "physical_stops": len(nodes),
            "excluded_trips": dict(excluded),
            "global_network_complete": False,
            "gtfs_semantics": GTFS_REFERENCE,
            "parent_qa_inherited": True,
            "synthetic": bundle.manifest.synthetic,
        },
    )


def load_bounded_gtfs(
    bundle_id: str,
    *,
    route_ids: tuple[str, ...],
    start: date,
    end: date,
    root: Path | None = None,
    max_runs: int = 10_000,
) -> NormalizedGtfs:
    root = data_dir() if root is None else root
    bundle = load_bundle(bundle_id, root=root / "bundles")
    if len(bundle.manifest.timetables) != 1:
        raise ValueError("ONE_ACCEPTED_GTFS_TIMETABLE_REQUIRED")
    ref = bundle.manifest.timetables[0]
    qa = json.loads(_path(root, ref.qa_report.removeprefix("data/")).read_bytes())
    dropped = qa.get("repair", {}).get("dropped_columns", {}).get("stop_times.txt", [])
    if set(dropped) & {
        "pickup_type",
        "drop_off_type",
        "start_pickup_drop_off_window",
        "end_pickup_drop_off_window",
    }:
        raise ValueError("GTFS_BOARDING_RESTRICTIONS_LOST_IN_REPAIR")
    ambiguous = frozenset(
        row["route_id"]
        for row in qa.get("service_overlaps", {}).get("cases", [])
        if row["shared_first_departures"] > 0
    )
    return normalize_gtfs(
        bundle,
        ref,
        _path(root, ref.path),
        route_ids=route_ids,
        start=start,
        end=end,
        max_runs=max_runs,
        ambiguous_route_ids=ambiguous,
    )


def publish_normalized_inputs(inputs: NormalizedGtfs, target: Path) -> dict[str, Any]:
    """Publish derivative canonical data and typed timetable, atomically and exclusively."""
    timetable = TypeAdapter(Timetable).dump_python(inputs.timetable, mode="json")
    timetable["scope"]["node_ids"] = sorted(timetable["scope"]["node_ids"])
    documents = {
        "accepted-data.json": inputs.accepted_data.model_dump(mode="json"),
        "timetable.json": timetable,
        "normalization-report.json": inputs.report,
    }
    payloads = {
        name: (
            json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        for name, document in documents.items()
    }
    manifest = {
        "schema_version": "waydex-normalized-gtfs/1",
        "snapshot_id": inputs.accepted_data.snapshot_id,
        "files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
            for name, payload in payloads.items()
        },
    }
    payloads["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()

    def verify_existing(path: Path) -> None:
        if any((path / name).read_bytes() != payload for name, payload in payloads.items()):
            raise FileExistsError("NORMALIZED_INPUTS_IMMUTABLE")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gtfs-inputs-", dir=target.parent) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir()
        for name, payload in payloads.items():
            (staging / name).write_bytes(payload)
        _publish_directory(staging, target, verify_existing)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_id")
    parser.add_argument("--route", action="append", required=True)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = load_bounded_gtfs(
        args.bundle_id, route_ids=tuple(args.route), start=args.start, end=args.end, root=args.root
    )
    print(json.dumps(publish_normalized_inputs(inputs, args.output), indent=2))


if __name__ == "__main__":
    main()
