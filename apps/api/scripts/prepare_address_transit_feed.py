"""Prepare the Waydex address transit pilot from the versioned Moscow GTFS zip."""

import argparse
import csv
import hashlib
import io
from collections.abc import Iterator
from pathlib import Path
from zipfile import ZipFile

SOURCE_SHA256 = "681afec73b21f8538a675194c95595093dbde03e95cfd72df0b4ef1f4114b2bc"
ROUTE_NAMES = {"м1", "м2", "м3", "м5", "м6", "м9", "м40", "м90", "м100", "с344"}


def records(archive: ZipFile, name: str) -> Iterator[dict[str, str]]:
    with io.TextIOWrapper(archive.open(name), encoding="utf-8-sig") as stream:
        yield from csv.DictReader(stream)


def write(path: Path, rows: Iterator[dict[str, str]]) -> None:
    first = next(rows)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(first))
        writer.writeheader()
        writer.writerow(first)
        writer.writerows(rows)


def stations(rows: Iterator[dict[str, str]]) -> Iterator[dict[str, str]]:
    for row in rows:
        # The source publishes ungrouped roadside stops. Valhalla 3.5.1 requires
        # a parent station for each platform. Keep the source coordinates.
        parent = f"station_{row['stop_id']}"
        yield {**row, "location_type": "0", "parent_station": parent}
        yield {**row, "stop_id": parent, "location_type": "1", "parent_station": ""}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gtfs", type=Path)
    parser.add_argument("feed", type=Path)
    args = parser.parse_args()
    with args.gtfs.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != SOURCE_SHA256:
            parser.error("GTFS snapshot does not match the address pilot")
    with ZipFile(args.gtfs) as archive:
        routes = [
            row
            for row in records(archive, "routes.txt")
            if row["route_short_name"].lower() in ROUTE_NAMES and row["route_type"] == "3"
        ]
        route_ids = {row["route_id"] for row in routes}
        trips = [row for row in records(archive, "trips.txt") if row["route_id"] in route_ids]
        trip_ids = {row["trip_id"] for row in trips}
        times = [row for row in records(archive, "stop_times.txt") if row["trip_id"] in trip_ids]
        stop_ids = {row["stop_id"] for row in times}
        args.feed.mkdir(parents=True, exist_ok=True)
        write(args.feed / "routes.txt", iter(routes))
        write(args.feed / "trips.txt", iter(trips))
        write(args.feed / "stop_times.txt", iter(times))
        write(
            args.feed / "stops.txt",
            stations(row for row in records(archive, "stops.txt") if row["stop_id"] in stop_ids),
        )
        for name in ("agency.txt", "calendar.txt"):
            (args.feed / name).write_bytes(archive.read(name))
    print(f"{len(routes)} route records, {len(trips)} trips, {len(stop_ids)} stops")


if __name__ == "__main__":
    main()
