"""Kaliningrad municipal timetables (gortrans39.ru): a frequency reference only.

Rule set ``gortrans39-frequency-rules-v1``. A route page embeds ``scheduleData``:
departure times per named stop, either per day type (``weekdays``,
``saturday``, ``weekend``) or one list for every day («единое расписание»).
There is no trip id and no direction, so departures of different stops are
never joined into trips (IMPLEMENTATION: no n-th departure matching). Per
route, stop and day type the reference keeps the number of unique departures,
the first and last one, the busiest hour and the median gap.

A route is quarantined when its timetable is a template, not a schedule:

- ``SHARED_TEMPLATE_TIMETABLE`` — the first stop's list is identical to the
  first stop's list of another route;
- ``REGULAR_GRID_TIMETABLE`` — every list of the first stop is a constant
  30-minute grid on round minutes (06:00, 06:30, …).

An empty day list is unknown (``EMPTY_DAY_LIST``), never zero departures.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_ID = "gortrans39"
RULES_VERSION = "gortrans39-frequency-rules-v1"
PAGE_URL = "https://gortrans39.ru/routes/{route_id}"
DAY_TYPES = ("weekdays", "saturday", "weekend")
MODES = {"av": "bus", "tb": "trolleybus", "tm": "tram"}
TIME = re.compile(r"'(\d{1,2}):(\d{2})'")
ENTRY = re.compile(r"'((?:[^'\\]|\\.)*)'\s*:\s*(\{.*?\}|\[[^\]]*\])", re.S)
DAY_LIST = re.compile(r"(weekdays|saturday|weekend)\s*:\s*\[([^\]]*)\]")


@dataclass
class StopTimes:
    name: str
    sequence: int  # position in the page's stop list, not a trip sequence
    days: dict[str, list[int]]  # day type -> minutes after midnight, as published


@dataclass
class RouteTimetable:
    route_id: str
    name: str
    mode: str
    unified: bool  # one list for every day
    stops: list[StopTimes]
    listed_stops: list[str]
    reasons: list[str] = field(default_factory=list)

    def first_lists(self) -> tuple[tuple[int, ...], ...]:
        if not self.stops:
            return ()
        return tuple(tuple(self.stops[0].days.get(day, [])) for day in DAY_TYPES)


def normal_name(name: str) -> str:
    """Stop name without quotes, «№», ё and notes: «Школа №59» == «Школа 59»."""

    text = re.sub(r"\(по требованию\)", " ", name.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", re.sub(r"[«»\"№]", " ", text)).strip()


def _minutes(text: str) -> list[int]:
    return [int(h) * 60 + int(m) for h, m in TIME.findall(text)]


def parse_route(route_id: str, page: str) -> RouteTimetable:
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    name = html.unescape(title.group(1).split(" - ")[0].strip()) if title else route_id
    listed = [
        html.unescape(n)
        for n in re.findall(r'<span class="routesmap-stop-name">(.*?)</span>', page)
    ]
    start = page.find("var scheduleData = {")
    stops: list[StopTimes] = []
    unified = False
    if start >= 0:
        block = page[start : page.find("\n};", start)]
        for index, found in enumerate(ENTRY.finditer(block)):
            stop = found.group(1).replace("\\'", "'")
            body = found.group(2)
            if body.startswith("["):
                unified = True
                times = _minutes(body)
                days = {day: list(times) for day in DAY_TYPES}
            else:
                days = {day: _minutes(values) for day, values in DAY_LIST.findall(body)}
            stops.append(StopTimes(name=html.unescape(stop), sequence=index, days=days))
    # the page's list spells names better («Школа №59»); use it when it is the same stop
    if len(listed) == len(stops):
        for stop, shown in zip(stops, listed):
            if normal_name(shown) == normal_name(stop.name):
                stop.name = shown
    mode = MODES.get(re.sub(r"\d+$", "", route_id), "unknown")
    return RouteTimetable(route_id, name, mode, unified, stops, listed)


def _regular_grid(times: list[int]) -> bool:
    if len(times) < 3 or any(t % 30 for t in times):
        return False
    return {b - a for a, b in zip(times, times[1:])} == {30}


def mark_templates(routes: list[RouteTimetable]) -> None:
    """Quarantine template timetables (see the module docstring)."""

    shared: dict[tuple[tuple[int, ...], ...], list[str]] = {}
    for route in routes:
        if route.stops:
            shared.setdefault(route.first_lists(), []).append(route.route_id)
    for route in routes:
        if not route.stops:
            route.reasons.append("NO_TIMETABLE")
            continue
        if len(shared[route.first_lists()]) > 1:
            route.reasons.append("SHARED_TEMPLATE_TIMETABLE")
        lists = [lst for lst in route.first_lists() if lst]
        if lists and all(_regular_grid(list(lst)) for lst in lists):
            route.reasons.append("REGULAR_GRID_TIMETABLE")


@dataclass(frozen=True)
class Frequency:
    departures: int | None
    first: str | None
    last: str | None
    max_hourly: int | None
    median_gap_min: float | None
    duplicate_times: int
    unordered: bool


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def frequency(times: list[int]) -> Frequency:
    """Unique departures of one stop and day type; an empty list is unknown."""

    unique = sorted(set(times))
    duplicates = len(times) - len(unique)
    unordered = times != sorted(times)
    if not unique:
        return Frequency(None, None, None, None, None, duplicates, unordered)
    hourly: dict[int, int] = {}
    for minute in unique:
        hourly[minute // 60] = hourly.get(minute // 60, 0) + 1
    gaps = [b - a for a, b in zip(unique, unique[1:])]
    return Frequency(
        departures=len(unique),
        first=_clock(unique[0]),
        last=_clock(unique[-1]),
        max_hourly=max(hourly.values()),
        median_gap_min=float(statistics.median(gaps)) if gaps else None,
        duplicate_times=duplicates,
        unordered=unordered,
    )


def record_hash(route: RouteTimetable, stop: StopTimes) -> str:
    payload = json.dumps(
        {"route": route.route_id, "stop": stop.name, "seq": stop.sequence, "days": stop.days},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load(raw_zip: Path) -> list[RouteTimetable]:
    """Route pages from the raw archive (``<route_id>.html``; the index page is skipped)."""

    routes = []
    with zipfile.ZipFile(raw_zip) as archive:
        for name in sorted(archive.namelist()):
            route_id = Path(name).stem
            if not name.endswith(".html") or route_id == "index":
                continue
            page = archive.read(name).decode("utf-8", errors="strict")
            routes.append(parse_route(route_id, page))
    mark_templates(routes)
    return routes


def stats(routes: list[RouteTimetable]) -> dict[str, Any]:
    return {
        "rules": RULES_VERSION,
        "routes": len(routes),
        "accepted_routes": sorted(r.route_id for r in routes if not r.reasons),
        "quarantined_routes": {r.route_id: r.reasons for r in routes if r.reasons},
        "unified_routes": sorted(r.route_id for r in routes if r.unified),
    }
