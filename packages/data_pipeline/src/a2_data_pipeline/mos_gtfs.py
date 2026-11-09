"""Moscow ground transport GTFS from data.mos.ru JSON (rule set ``mos-gtfs-assembly-v1``).

data.mos.ru publishes the city's GTFS as separate JSON datasets of «Маршруты и остановки
наземного городского пассажирского транспорта»: 60663 agency, 60662 stops, 60664
routes, 60665 trips, 60666 calendar, 60661 stop times. This module writes them as a
plain GTFS archive and passes it to ``gtfs.repair`` (``gtfs-repair-v1``), which drops
broken references and reports every loss.

Rules:

- fields are copied, never inferred: a stop takes the point of its ``geoData`` (or
  ``geodata_center``); a missing value stays empty and ``gtfs.repair`` drops the row;
- the stop times file (≈3 GB) is read as a stream with the standard decoder;
- the datasets are updated separately: stop times of a trip absent from the trips
  dataset are counted by the assembly and dropped by the repair, not matched;
- the feed has no ``calendar_dates``: official non-working days inside the window
  are reported as days whose service is unknown (the weekday pattern is not the
  holiday schedule);
- two services of one route and direction running on the same day are reported with
  the share of shared first departures; they are not merged or dropped.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import IO, Any

from . import gtfs
from .gtfs import COLUMNS, FIXED_TIME, REPAIR_VERSION, WEEKDAYS
from .manifest import load_dataset_manifest
from .paths import data_dir, processed_dir, raw_dir
from .store import sha256_file

RULES_VERSION = "mos-gtfs-assembly-v1"
TABLES = {
    "agency.txt": "60663",
    "stops.txt": "60662",
    "routes.txt": "60664",
    "trips.txt": "60665",
    "calendar.txt": "60666",
    "stop_times.txt": "60661",
}
# official non-working days (production calendar 2026); the feed has no exceptions
HOLIDAYS = {date(2026, 11, 4): "День народного единства"}
CHUNK = 1 << 20


def iter_array(stream: IO[bytes]) -> Iterator[dict[str, Any]]:
    """Objects of a top-level JSON array, decoded in chunks (no full load in memory)."""

    decoder = json.JSONDecoder()
    reader = io.TextIOWrapper(stream, encoding="utf-8")
    buffer = ""
    started = False
    while True:
        chunk = reader.read(CHUNK)
        buffer += chunk
        position = 0
        while True:
            while position < len(buffer) and buffer[position] in " \t\r\n,":
                position += 1
            if not started and position < len(buffer):
                if buffer[position] != "[":
                    raise ValueError("a JSON array is expected")
                started = True
                position += 1
                continue
            if position < len(buffer) and buffer[position] == "]":
                return
            try:
                item, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                break  # the object continues in the next chunk
            yield item
            position = end
        buffer = buffer[position:]
        if not chunk:
            if buffer.strip():
                raise ValueError("the JSON array is not closed")
            return


def _items(raw_zip: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(raw_zip) as archive:
        with archive.open(archive.namelist()[0]) as stream:
            yield from iter_array(stream)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _point(stop: dict[str, Any]) -> tuple[str, str]:
    for key in ("geoData", "geodata_center"):
        value = stop.get(key)
        shape = value[0] if isinstance(value, list) and value else value
        if isinstance(shape, dict) and shape.get("type") == "Point":
            lon, lat = shape["coordinates"][:2]
            return str(lat), str(lon)
    return "", ""


ROW: dict[str, Callable[[dict[str, Any]], tuple[str, ...]]] = {
    "agency.txt": lambda r: (
        _text(r.get("agency_code")),
        _text(r.get("agency_name")),
        _text(r.get("agency_url")),
        _text(r.get("agency_timezone")),
    ),
    "stops.txt": lambda r: (_text(r.get("stop_id")), _text(r.get("stop_name")), *_point(r)),
    "routes.txt": lambda r: (
        _text(r.get("route_id")),
        _text(r.get("agency_code")),
        _text(r.get("route_short_name")),
        _text(r.get("route_long_name")),
        _text(r.get("route_type")),
    ),
    "trips.txt": lambda r: (
        _text(r.get("route_id")),
        _text(r.get("service_id")),
        _text(r.get("trip_id")),
        _text(r.get("trip_headsign")),
        _text(r.get("direction_id")),
    ),
    "calendar.txt": lambda r: (
        _text(r.get("service_id")),
        *(_text(r.get(day)) for day in WEEKDAYS),
        _text(r.get("start_date")),
        _text(r.get("end_date")),
    ),
    "stop_times.txt": lambda r: (
        _text(r.get("trip_id")),
        _text(r.get("arrival_time")),
        _text(r.get("departure_time")),
        _text(r.get("stop_id")),
        _text(r.get("stop_sequence")),
    ),
}


def assemble(raws: dict[str, Path], out_zip: Path) -> dict[str, Any]:
    """A GTFS archive of the six JSON datasets, deterministic for the same raws."""

    trip_ids = {_text(r.get("trip_id")) for r in _items(raws["trips.txt"])}
    counts: dict[str, int] = {}
    unknown_trip_rows = 0
    unknown_trip_ids: set[str] = set()
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    partial = out_zip.with_name(out_zip.name + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(TABLES):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w") as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
                writer = csv.writer(text, lineterminator="\n")
                writer.writerow(COLUMNS[name])
                n = 0
                for record in _items(raws[name]):
                    row = ROW[name](record)
                    if name == "stop_times.txt" and row[0] not in trip_ids:
                        unknown_trip_rows += 1
                        unknown_trip_ids.add(row[0])
                    writer.writerow(row)
                    n += 1
                text.flush()
                text.detach()
            counts[name] = n
    partial.rename(out_zip)
    return {
        "rules": RULES_VERSION,
        "rows": counts,
        "stop_times_of_trips_not_in_trips_dataset": {
            "rows": unknown_trip_rows,
            "trips": len(unknown_trip_ids),
        },
    }


def service_overlaps(repaired_zip: Path, window: tuple[date, date]) -> dict[str, Any]:
    """Route-directions with two services on one day and their shared first departures."""

    with zipfile.ZipFile(repaired_zip) as archive:

        def rows(name: str) -> list[dict[str, str]]:
            reader = csv.DictReader(io.TextIOWrapper(archive.open(name), encoding="utf-8"))
            return list(reader)

        services = {s["service_id"]: s for s in rows("calendar.txt")}
        trips = rows("trips.txt")
        first: dict[str, str] = {}
        reader = csv.DictReader(io.TextIOWrapper(archive.open("stop_times.txt"), encoding="utf-8"))
        for event in reader:
            if event["trip_id"] not in first:  # the repair writes each trip sorted by sequence
                first[event["trip_id"]] = f"{event['stop_id']}@{event['departure_time']}"
    by_group: dict[tuple[str, str], dict[str, set[str]]] = {}
    for trip in trips:
        key = (trip["route_id"], trip["direction_id"])
        by_group.setdefault(key, {}).setdefault(trip["service_id"], set()).add(
            first.get(trip["trip_id"], "")
        )
    overlapping: dict[tuple[str, str], tuple[str, str]] = {}
    day = window[0]
    while day <= window[1]:
        stamp = day.strftime("%Y%m%d")
        flag = WEEKDAYS[day.weekday()]
        for key, by_service in by_group.items():
            running = sorted(
                s
                for s in by_service
                if services[s][flag] == "1"
                and services[s]["start_date"] <= stamp <= services[s]["end_date"]
            )
            if len(running) > 1:
                overlapping.setdefault(key, (running[0], running[1]))
        day += timedelta(days=1)
    shares: list[dict[str, Any]] = []
    for (route_id, direction), (a, b) in sorted(overlapping.items()):
        left, right = by_group[(route_id, direction)][a], by_group[(route_id, direction)][b]
        shared = len(left & right) / max(1, min(len(left), len(right)))
        shares.append(
            {
                "route_id": route_id,
                "direction_id": direction,
                "services": [a, b],
                "shared_first_departures": round(shared, 3),
            }
        )
    return {
        "route_directions_with_two_services": len(shares),
        "duplicated": sum(1 for s in shares if s["shared_first_departures"] >= 0.5),
        "cases": shares,
    }


def build(dataset_ids: dict[str, str], window: tuple[date, date], output_id: str) -> dict[str, Any]:
    raws: dict[str, Path] = {}
    sources = []
    for name, dataset_id in dataset_ids.items():
        manifest = load_dataset_manifest(dataset_id)
        path = raw_dir() / manifest.raw_path
        if sha256_file(path).value != manifest.checksum.value:
            raise ValueError(f"{dataset_id}: raw sha256 mismatch")
        raws[name] = path
        sources.append(
            {
                "table": name,
                "dataset_id": dataset_id,
                "raw_sha256": manifest.checksum.value,
                "publication_date": manifest.publication_date.isoformat()
                if manifest.publication_date
                else None,
                "license_status": manifest.license.status,
            }
        )
    base = processed_dir() / "gtfs"
    assembled = base / f"{output_id}.assembled.zip"
    repaired = base / f"{output_id}.{REPAIR_VERSION}.zip"
    assembly = assemble(raws, assembled)
    report = gtfs.repair(assembled, repaired, window)
    holidays = {
        d.isoformat(): name for d, name in sorted(HOLIDAYS.items()) if window[0] <= d <= window[1]
    }
    publication = {s["publication_date"] for s in sources}
    return {
        "output_id": output_id,
        "rules": [RULES_VERSION, REPAIR_VERSION],
        "sources": sources,
        "versions_differ": len(publication) > 1,
        "assembly": assembly,
        "assembled_sha256": sha256_file(assembled).value,
        "repaired_sha256": sha256_file(repaired).value,
        "repaired_path": str(repaired.relative_to(data_dir())),
        "holidays_without_exceptions": holidays,
        "service_overlaps": service_overlaps(repaired, window),
    } | {"repair": report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_id")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    for name in TABLES:
        parser.add_argument(f"--{name.removesuffix('.txt').replace('_', '-')}", required=True)
    args = parser.parse_args()
    ids = {name: getattr(args, name.removesuffix(".txt")) for name in TABLES}
    report = build(ids, (args.start, args.end), args.output_id)
    target = data_dir() / "manifests" / "qa" / f"{args.output_id}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


__all__ = ["assemble", "build", "iter_array", "service_overlaps"]

if __name__ == "__main__":
    main()
