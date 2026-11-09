"""Assembly of a GTFS archive from data.mos.ru JSON datasets on synthetic tables."""

import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from a2_data_pipeline import mos_gtfs


def write(path: Path, rows: list[dict[str, Any]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.json", json.dumps(rows, ensure_ascii=False))
    return path


def point(lon: float, lat: float) -> list[dict[str, Any]]:
    return [{"type": "Point", "coordinates": [lon, lat]}]


CAL = {d: 1 for d in ("monday", "tuesday", "wednesday", "thursday", "friday")} | {
    "saturday": 0,
    "sunday": 0,
}


def tables(tmp_path: Path) -> dict[str, Path]:
    stops = [
        {"stop_id": 1, "stop_name": "А", "geoData": point(37.6, 55.7)},
        {"stop_id": 2, "stop_name": "Б", "geoData": None, "geodata_center": point(37.61, 55.71)[0]},
        {"stop_id": 3, "stop_name": "В", "geoData": None},  # no point: dropped by the repair
    ]
    trips = [
        {
            "route_id": "r1",
            "service_id": "s-old",
            "trip_id": "t1",
            "trip_headsign": "",
            "direction_id": 0,
        },
        {
            "route_id": "r1",
            "service_id": "s-new",
            "trip_id": "t2",
            "trip_headsign": "",
            "direction_id": 0,
        },
    ]
    times = [
        {"trip_id": t, "arrival_time": a, "departure_time": a, "stop_id": s, "stop_sequence": q}
        for t, a, s, q in (
            ("t1", "08:00:00", 1, 1),
            ("t1", "08:10:00", 2, 2),
            ("t2", "08:00:00", 1, 1),  # the same first departure: a second version
            ("t2", "08:12:00", 2, 2),
            ("t9", "09:00:00", 1, 1),  # a trip of a newer trips dataset
            ("t9", "09:10:00", 2, 2),
        )
    ]
    return {
        "agency.txt": write(
            tmp_path / "a.zip",
            [
                {
                    "agency_code": "1",
                    "agency_name": "ГУП",
                    "agency_url": "",
                    "agency_timezone": "Europe/Moscow",
                }
            ],
        ),
        "stops.txt": write(tmp_path / "s.zip", stops),
        "routes.txt": write(
            tmp_path / "r.zip",
            [
                {
                    "route_id": "r1",
                    "agency_code": "1",
                    "route_short_name": "1",
                    "route_long_name": "",
                    "route_type": 3,
                }
            ],
        ),
        "trips.txt": write(tmp_path / "t.zip", trips),
        "calendar.txt": write(
            tmp_path / "c.zip",
            [
                {"service_id": "s-old", **CAL, "start_date": "20260509", "end_date": "20301231"},
                {"service_id": "s-new", **CAL, "start_date": "20260620", "end_date": "20301231"},
            ],
        ),
        "stop_times.txt": write(tmp_path / "st.zip", times),
    }


def test_stream_reader_survives_small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mos_gtfs, "CHUNK", 7)
    rows = [{"a": i, "b": "текст " * i} for i in range(20)]
    stream = io.BytesIO(json.dumps(rows, ensure_ascii=False).encode())
    assert list(mos_gtfs.iter_array(stream)) == rows
    with pytest.raises(ValueError, match="array"):
        list(mos_gtfs.iter_array(io.BytesIO(b'{"a": 1}')))


def test_assembly_repair_and_overlaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mos_gtfs.gtfs, "repo_root", lambda: tmp_path)
    raws = tables(tmp_path)
    assembled = tmp_path / "out" / "feed.zip"
    assembly = mos_gtfs.assemble(raws, assembled)
    assert assembly["rows"]["stop_times.txt"] == 6
    assert assembly["stop_times_of_trips_not_in_trips_dataset"] == {"rows": 2, "trips": 1}
    with zipfile.ZipFile(assembled) as archive:
        stops = list(csv.DictReader(io.TextIOWrapper(archive.open("stops.txt"), encoding="utf-8")))
    assert stops[1]["stop_lat"] == "55.71" and stops[2]["stop_lat"] == ""
    repaired = tmp_path / "out" / "repaired.zip"
    window = (date(2026, 11, 9), date(2026, 11, 10))
    report = mos_gtfs.gtfs.repair(assembled, repaired, window)
    assert report["losses"]["stop_time_unknown_trip"] == 2
    overlaps = mos_gtfs.service_overlaps(repaired, window)
    assert overlaps["route_directions_with_two_services"] == 1
    assert overlaps["duplicated"] == 1  # both versions start at the same stop and minute
    # deterministic: the same raws give the same archive
    again = tmp_path / "out" / "again.zip"
    mos_gtfs.assemble(raws, again)
    assert again.read_bytes() == assembled.read_bytes()
