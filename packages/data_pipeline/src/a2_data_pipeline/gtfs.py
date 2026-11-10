"""GTFS audit and repair (rule set ``gtfs-repair-v2``).

The repair never invents data: it keeps the standard GTFS columns, drops rows
that break a reference or an order rule, and reports every loss by reason.

- columns outside GTFS and columns with empty names are dropped;
- calendar services with invalid dates or flags are dropped;
- stops without valid coordinates are dropped;
- trips with an unknown route or service are dropped;
- stop times of an unknown trip or stop are dropped; a trip is then sorted by
  ``stop_sequence`` and dropped when sequences repeat, an arrival is after its
  departure, times go backwards, or fewer than two stop times remain;
- routes, stops and services that no trip uses are kept (they are harmless) and
  counted.

Calendar exceptions (``calendar_dates.txt``) are kept and applied: an exception
of type 2 removes the service on that date, type 1 adds it (also for a service
that exists only in ``calendar_dates``). Rows with an invalid date or type, or a
second row for the same service and date, are dropped and counted. A feed without
``calendar_dates`` has no holidays, which the report states. (``gtfs-repair-v1``
dropped the file; for a feed without it both versions write the same archive.) The output archive is deterministic
(sorted rows, fixed timestamps), so a rebuild from the same raw has the same
sha256.

Usage::

    python -m a2_data_pipeline.gtfs gtfs_moscow_mdb3226.202606301640 --from 2026-11-01 --to 2026-11-30
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .manifest import load_dataset_manifest
from .paths import processed_dir, raw_dir, repo_root

REPAIR_VERSION = "gtfs-repair-v2"
COLUMNS = {
    "agency.txt": ("agency_id", "agency_name", "agency_url", "agency_timezone"),
    "calendar.txt": (
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
    ),
    "routes.txt": ("route_id", "agency_id", "route_short_name", "route_long_name", "route_type"),
    "stops.txt": ("stop_id", "stop_name", "stop_lat", "stop_lon"),
    "trips.txt": ("route_id", "service_id", "trip_id", "trip_headsign", "direction_id"),
    "stop_times.txt": ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"),
    "calendar_dates.txt": ("service_id", "date", "exception_type"),
}
OPTIONAL_FILES = {"calendar_dates.txt"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
ROUTE_TYPES = {"0": "tram", "1": "subway", "2": "rail", "3": "bus", "4": "ferry", "5": "cable_tram"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


class Losses:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def add(self, reason: str, n: int = 1) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + n


def _rows(archive: zipfile.ZipFile, name: str) -> tuple[list[str], Iterator[list[str]]]:
    stream = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="")
    reader = csv.reader(stream)
    return next(reader), reader


def _records(
    archive: zipfile.ZipFile, name: str, dropped_columns: dict[str, list[str]]
) -> Iterator[dict[str, str]]:
    header, rows = _rows(archive, name)
    keep = COLUMNS[name]
    dropped_columns[name] = [h if h else "<empty>" for h in header if h not in keep]
    index = {h: i for i, h in enumerate(header) if h in keep}
    for row in rows:
        yield {k: row[i].strip() if i < len(row) else "" for k, i in index.items()}


def _date(text: str) -> date | None:
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])) if len(text) == 8 else None
    except ValueError:
        return None


def _seconds(text: str) -> int | None:
    parts = text.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    hours, minutes, seconds = (int(p) for p in parts)
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _coordinate(lat: str, lon: str) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180 and float(lat) != 0
    except ValueError:
        return False


def service_dates(
    service: dict[str, str] | None,
    exceptions: dict[date, str],
    start: date,
    end: date,
) -> set[date]:
    """Dates of ``start..end`` on which a service runs: the calendar, then its exceptions."""

    dates: set[date] = set()
    if service is not None:
        first, last = _date(service["start_date"]), _date(service["end_date"])
        if first is not None and last is not None:
            day = max(first, start)
            while day <= min(last, end):
                if service[WEEKDAYS[day.weekday()]] == "1":
                    dates.add(day)
                day += timedelta(days=1)
    for day, kind in exceptions.items():
        if start <= day <= end:
            if kind == "1":
                dates.add(day)
            else:
                dates.discard(day)
    return dates


def service_days(service: dict[str, str], start: date, end: date) -> int:
    """Days of ``start..end`` on which a calendar service runs, without exceptions."""

    return len(service_dates(service, {}, start, end))


def repair(raw_zip: Path, out_zip: Path, window: tuple[date, date]) -> dict[str, Any]:  # noqa: C901 - one pass per file, kept together on purpose
    losses = Losses()
    dropped_columns: dict[str, list[str]] = {}
    with zipfile.ZipFile(raw_zip) as archive:
        names = set(archive.namelist())
        missing_files = sorted(n for n in COLUMNS if n not in names and n not in OPTIONAL_FILES)
        if missing_files:
            raise ValueError(f"required GTFS files missing: {missing_files}")
        agencies = {a["agency_id"]: a for a in _records(archive, "agency.txt", dropped_columns)}
        services: dict[str, dict[str, str]] = {}
        for service in _records(archive, "calendar.txt", dropped_columns):
            flags_ok = all(service[d] in {"0", "1"} for d in WEEKDAYS)
            first, last = _date(service["start_date"]), _date(service["end_date"])
            if not flags_ok or first is None or last is None or last < first:
                losses.add("calendar_invalid_service")
            elif service["service_id"] in services:
                losses.add("calendar_duplicate_service")
            else:
                services[service["service_id"]] = service
        exceptions: dict[str, dict[date, str]] = {}
        has_exceptions = "calendar_dates.txt" in names
        if has_exceptions:
            for row in _records(archive, "calendar_dates.txt", dropped_columns):
                day = _date(row["date"])
                if not row["service_id"] or day is None or row["exception_type"] not in {"1", "2"}:
                    losses.add("calendar_date_invalid")
                elif day in exceptions.get(row["service_id"], {}):
                    losses.add("calendar_date_duplicate")
                else:
                    exceptions.setdefault(row["service_id"], {})[day] = row["exception_type"]
        known_services = set(services) | set(exceptions)
        routes: dict[str, dict[str, str]] = {}
        for route in _records(archive, "routes.txt", dropped_columns):
            if route["route_type"] not in ROUTE_TYPES:
                losses.add("route_unknown_type")
            elif route["agency_id"] and route["agency_id"] not in agencies:
                losses.add("route_unknown_agency")
            elif route["route_id"] in routes:
                losses.add("route_duplicate")
            else:
                routes[route["route_id"]] = route
        stops: dict[str, dict[str, str]] = {}
        for stop in _records(archive, "stops.txt", dropped_columns):
            if not _coordinate(stop["stop_lat"], stop["stop_lon"]):
                losses.add("stop_without_coordinates")
            elif stop["stop_id"] in stops:
                losses.add("stop_duplicate")
            else:
                stops[stop["stop_id"]] = stop
        trips: dict[str, dict[str, str]] = {}
        for trip in _records(archive, "trips.txt", dropped_columns):
            if trip["route_id"] not in routes:
                losses.add("trip_unknown_route")
            elif trip["service_id"] not in known_services:
                losses.add("trip_unknown_service")
            elif trip["trip_id"] in trips:
                losses.add("trip_duplicate")
            else:
                trips[trip["trip_id"]] = trip
        events: dict[str, list[tuple[int, int, int, str]]] = {}
        stop_time_rows = 0
        for row in _records(archive, "stop_times.txt", dropped_columns):
            stop_time_rows += 1
            if row["trip_id"] not in trips:
                losses.add("stop_time_unknown_trip")
                continue
            if row["stop_id"] not in stops:
                losses.add("stop_time_unknown_stop")
                continue
            arrival = _seconds(row["arrival_time"])
            departure = _seconds(row["departure_time"])
            if not row["stop_sequence"].isdigit() or arrival is None or departure is None:
                losses.add("stop_time_unparsable")
                continue
            events.setdefault(row["trip_id"], []).append(
                (int(row["stop_sequence"]), arrival, departure, row["stop_id"])
            )
    kept: dict[str, list[tuple[int, int, int, str]]] = {}
    for trip_id in sorted(trips):
        trip_events = sorted(events.get(trip_id, []))
        sequences = [e[0] for e in trip_events]
        if len(trip_events) < 2:
            losses.add("trip_fewer_than_two_stop_times")
        elif len(set(sequences)) != len(sequences):
            losses.add("trip_repeated_stop_sequence")
        elif any(a > d for _, a, d, _ in trip_events):
            losses.add("trip_arrival_after_departure")
        elif any(nxt[1] < cur[2] for cur, nxt in zip(trip_events, trip_events[1:])):
            losses.add("trip_time_goes_backwards")
        else:
            kept[trip_id] = trip_events
    kept_trips = {t: trips[t] for t in kept}
    used_routes = {t["route_id"] for t in kept_trips.values()}
    used_services = {t["service_id"] for t in kept_trips.values()}
    used_stops = {e[3] for trip_events in kept.values() for e in trip_events}
    _write(
        out_zip,
        agencies,
        services,
        routes,
        stops,
        kept_trips,
        kept,
        exceptions if has_exceptions else None,
    )
    start, end = window
    running_dates = {
        s: service_dates(services.get(s), exceptions.get(s, {}), start, end) for s in used_services
    }
    active_services = {s for s, dates in running_dates.items() if dates}
    active_trips = [t for t in kept_trips.values() if t["service_id"] in active_services]
    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    return {
        "repair_version": REPAIR_VERSION,
        "output": {"path": str(out_zip.relative_to(repo_root())), "sha256": digest},
        "input_rows": {
            "agency": len(agencies),
            "calendar": len(services) + losses.counts.get("calendar_invalid_service", 0),
            "calendar_dates": sum(len(v) for v in exceptions.values())
            + losses.counts.get("calendar_date_invalid", 0)
            + losses.counts.get("calendar_date_duplicate", 0),
            "routes": len(routes),
            "stops": len(stops) + losses.counts.get("stop_without_coordinates", 0),
            "trips": len(trips)
            + sum(
                losses.counts.get(k, 0)
                for k in ("trip_unknown_route", "trip_unknown_service", "trip_duplicate")
            ),
            "stop_times": stop_time_rows,
        },
        "output_rows": {
            "agency": len(agencies),
            "calendar": len(services),
            "calendar_dates": sum(len(v) for v in exceptions.values()),
            "routes": len(routes),
            "stops": len(stops),
            "trips": len(kept),
            "stop_times": sum(len(v) for v in kept.values()),
        },
        "losses": dict(sorted(losses.counts.items())),
        "dropped_columns": dropped_columns,
        "unused": {
            "routes": len(routes) - len(used_routes),
            "stops": len(stops) - len(used_stops),
            "services": len(known_services) - len(used_services),
        },
        "modes": _count(ROUTE_TYPES[routes[r]["route_type"]] for r in used_routes),
        "window": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "services_running": len(active_services),
            "exceptions_in_window": {
                "added": sum(
                    1
                    for s in used_services
                    for d, k in exceptions.get(s, {}).items()
                    if start <= d <= end and k == "1"
                ),
                "removed": sum(
                    1
                    for s in used_services
                    for d, k in exceptions.get(s, {}).items()
                    if start <= d <= end and k == "2"
                ),
            },
            "trips_running": len(active_trips),
            "routes_running": len({t["route_id"] for t in active_trips}),
            "modes_running": _count(
                ROUTE_TYPES[routes[r]["route_type"]] for r in {t["route_id"] for t in active_trips}
            ),
        },
        "service_end_years": _count(
            services[s]["end_date"][:4] for s in used_services if s in services
        ),
        "unrepaired": {
            "agencies_without_url": sum(1 for a in agencies.values() if not a["agency_url"]),
            "calendar_dates": "kept and applied"
            if has_exceptions
            else "absent: holidays and other exceptions are unknown",
            "shapes": "absent",
        },
    }


def _count(values: Iterator[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _csv(header: tuple[str, ...], rows: Iterator[tuple[str, ...]] | list[tuple[str, ...]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _write(
    out_zip: Path,
    agencies: dict[str, dict[str, str]],
    services: dict[str, dict[str, str]],
    routes: dict[str, dict[str, str]],
    stops: dict[str, dict[str, str]],
    trips: dict[str, dict[str, str]],
    events: dict[str, list[tuple[int, int, int, str]]],
    exceptions: dict[str, dict[date, str]] | None = None,
) -> None:
    def table(name: str, rows: dict[str, dict[str, str]]) -> bytes:
        columns = COLUMNS[name]
        return _csv(columns, [tuple(rows[k][c] for c in columns) for k in sorted(rows)])

    stop_times = (
        (trip_id, _clock(arrival), _clock(departure), stop_id, str(sequence))
        for trip_id in sorted(events)
        for sequence, arrival, departure, stop_id in events[trip_id]
    )
    files = {
        "agency.txt": table("agency.txt", agencies),
        "calendar.txt": table("calendar.txt", services),
        "routes.txt": table("routes.txt", routes),
        "stops.txt": table("stops.txt", stops),
        "trips.txt": table("trips.txt", trips),
        "stop_times.txt": _csv(COLUMNS["stop_times.txt"], list(stop_times)),
    }
    if exceptions is not None:  # written only when the feed has the file: same bytes otherwise
        files["calendar_dates.txt"] = _csv(
            COLUMNS["calendar_dates.txt"],
            [
                (service_id, day.strftime("%Y%m%d"), kind)
                for service_id in sorted(exceptions)
                for day, kind in sorted(exceptions[service_id].items())
            ],
        )
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    partial = out_zip.with_name(out_zip.name + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    partial.rename(out_zip)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_id")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    manifest = load_dataset_manifest(args.dataset_id)
    out = processed_dir() / "gtfs" / f"{args.dataset_id}.{REPAIR_VERSION}.zip"
    report = repair(raw_dir() / manifest.raw_path, out, (args.start, args.end))
    with zipfile.ZipFile(raw_dir() / manifest.raw_path) as archive:
        file_dates = {i.filename: "%04d-%02d-%02d" % i.date_time[:3] for i in archive.infolist()}
    report = {
        "dataset_id": manifest.dataset_id,
        "source_url": str(manifest.source_url),
        "raw_sha256": manifest.checksum.value,
        # dates of the files inside the archive: when the data was exported
        "source_file_dates": dict(sorted(file_dates.items())),
    } | report
    path = repo_root() / "data" / "manifests" / "qa" / f"{args.dataset_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
