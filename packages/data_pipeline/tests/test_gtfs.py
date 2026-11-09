"""GTFS repair rules (gtfs-repair-v1) on a synthetic feed."""

import csv
import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest
from a2_data_pipeline import gtfs

WINDOW = (date(2026, 11, 1), date(2026, 11, 30))
FEED = {
    "agency.txt": "global_id,agency_id,agency_name,agency_url,agency_timezone,\n"
    "1,1,Перевозчик,,Europe/Moscow,\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WK,1,1,1,1,1,0,0,20260101,20301231\n"
    "OLD,1,1,1,1,1,1,1,20250101,20251231\n"
    "BAD,1,1,1,1,1,1,1,20260201,20260101\n",
    "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type,route_desc\n"
    "R1,1,1,Первый,3,x\n"
    "R2,1,2,Второй,0,y\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,geoData\n"
    "S1,А,55.70,37.60,{}\n"
    "S2,Б,55.71,37.61,{}\n"
    "S3,В,55.72,37.62,{}\n"
    "S4,Без координат,,,{}\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id\n"
    "R1,WK,T1,,0\n"  # good, stop times out of order in the file
    "R1,WK,T2,,0\n"  # loses its only valid second stop: fewer than two
    "R2,OLD,T3,,1\n"  # good but its service ended in 2025
    "R1,WK,T4,,0\n"  # time goes backwards
    "R1,BAD,T5,,0\n",  # unknown (dropped) service
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign\n"
    "T1,08:10:00,08:10:00,S2,2,\n"
    "T1,08:00:00,08:01:00,S1,1,\n"
    "T1,25:05:00,25:05:00,S3,3,\n"
    "T2,09:00:00,09:00:00,S1,1,\n"
    "T2,09:10:00,09:10:00,S4,2,\n"
    "T3,10:00:00,10:00:00,S1,1,\n"
    "T3,10:20:00,10:20:00,S3,2,\n"
    "T4,11:00:00,11:05:00,S1,1,\n"
    "T4,11:02:00,11:02:00,S2,2,\n"
    "GHOST,12:00:00,12:00:00,S1,1,\n",
}


def write_feed(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in FEED.items():
            archive.writestr(name, text)
    return path


def read(path: Path, name: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        return list(csv.DictReader(io.TextIOWrapper(archive.open(name), encoding="utf-8")))


def test_repair_drops_and_reports_every_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gtfs, "repo_root", lambda: tmp_path)
    raw = write_feed(tmp_path / "raw.zip")
    out = tmp_path / "out" / "feed.zip"
    report = gtfs.repair(raw, out, WINDOW)
    assert report["losses"] == {
        "calendar_invalid_service": 1,
        "stop_time_unknown_stop": 1,
        "stop_time_unknown_trip": 1,
        "stop_without_coordinates": 1,
        "trip_fewer_than_two_stop_times": 1,
        "trip_time_goes_backwards": 1,
        "trip_unknown_service": 1,
    }
    assert [t["trip_id"] for t in read(out, "trips.txt")] == ["T1", "T3"]
    times = [
        (r["trip_id"], r["stop_sequence"], r["arrival_time"]) for r in read(out, "stop_times.txt")
    ]
    # sorted by sequence; times after midnight stay as published (25:05:00)
    assert times == [
        ("T1", "1", "08:00:00"),
        ("T1", "2", "08:10:00"),
        ("T1", "3", "25:05:00"),
        ("T3", "1", "10:00:00"),
        ("T3", "2", "10:20:00"),
    ]
    assert "geoData" not in read(out, "stops.txt")[0]
    assert report["dropped_columns"]["agency.txt"] == ["global_id", "<empty>"]
    # only the weekday service runs in the window; the 2025 one does not
    assert report["window"]["trips_running"] == 1
    assert report["window"]["services_running"] == 1
    assert report["unrepaired"]["agencies_without_url"] == 1


def test_repair_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gtfs, "repo_root", lambda: tmp_path)
    raw = write_feed(tmp_path / "raw.zip")
    first = gtfs.repair(raw, tmp_path / "a.zip", WINDOW)["output"]["sha256"]
    second = gtfs.repair(raw, tmp_path / "b.zip", WINDOW)["output"]["sha256"]
    assert first == second == hashlib.sha256((tmp_path / "a.zip").read_bytes()).hexdigest()


def test_service_days_without_exceptions() -> None:
    service = {d: "1" if d in {"saturday", "sunday"} else "0" for d in gtfs.WEEKDAYS}
    service |= {"start_date": "20261101", "end_date": "20261130"}
    assert gtfs.service_days(service, *WINDOW) == 9  # Nov 2026: five Sundays, four Saturdays
