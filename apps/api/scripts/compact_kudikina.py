"""Import the two dated route captures used by the compact transport profiles."""

import hashlib
import html
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData, AccessNode, Evidence, ScheduledService
from a2_engine import Timetable, TimetableEvent, TimetableRun, TimetableScope

FIRST_DAY = date(2026, 11, 12)
NEXT_DAY = FIRST_DAY + timedelta(days=1)
ROUTES = {
    "kaliningrad": (("klgd/bus/3", 33),),
    "novorossiysk": (("nvrsk/bus/103", 19), ("nvrsk/trolley/11", 19)),
}
STOP = re.compile(r'<div class="bus-stop [^"]+">\s*<a href="([^"]+)">\s*\d+\)\s*(.*?)\s*</a>')
TIMES = re.compile(r'<div class="stop-times">\s*(.*?)\s*</div>', re.S)
CLOCK = re.compile(r">(\d\d):([0-5]\d)</span>")
EXPAND = re.compile(r"<span\b[^>]*onScheduleExpand[^>]*>Показать все</span>")
MAP_STOPS = re.compile(r'^\s*(\[\{"name".*?\}\]),?\s*$', re.M)


def stop_name(value: str) -> str:
    value = html.unescape(value).casefold().replace("ё", "е").strip()
    return re.sub(r"[^\w]", "", re.sub(r"^(ул\.\s*|улица\s+)", "", value))


def service_times(block: str) -> list[int]:
    result: list[int] = []
    offset = 0
    for hour, minute in CLOCK.findall(block):
        seconds = int(hour) * 3600 + int(minute) * 60
        if seconds >= 86400:
            raise ValueError("Expected a local clock time")
        if result and result[-1] - (seconds + offset) > 43200:
            offset += 86400
        result.append(seconds + offset)
    return result


def read_capture(root: Path, route: str, suffix: str) -> tuple[str, Evidence]:
    data = (root / f"{route.replace('/', '-')}-{suffix.replace('?d=', '')}.html").read_bytes()
    return data.decode("utf-8"), Evidence(
        evidence_id=f"kudikina:{route.replace('/', ':')}:{suffix.replace('?d=', '-')}",
        source_id="kudikina",
        source_version=FIRST_DAY.isoformat(),
        url=f"https://kudikina.ru/{route}/{suffix}",
        retrieved_at=datetime.combine(FIRST_DAY, time(6, 18), ZoneInfo("UTC")),
        valid_period={"start": FIRST_DAY, "end": NEXT_DAY},
        license="Kudikina rules: noncommercial use with attribution (https://kudikina.ru/rules)",
        content_hash=hashlib.sha256(data).hexdigest(),
        verified=True,
    )


def load_kudikina(
    root: Path, city: str, parent: AcceptedData
) -> tuple[AcceptedData, Timetable, dict[str, object]]:
    evidence: list[Evidence] = []
    nodes: dict[str, AccessNode] = {}
    services: list[ScheduledService] = []
    runs: list[TimetableRun] = []
    zone = ZoneInfo(parent.timezone)
    horizon_start = datetime.combine(FIRST_DAY, time.min, zone)
    horizon_end = datetime.combine(NEXT_DAY + timedelta(days=1), time.min, zone)
    for route, reverse_start in ROUTES[city]:
        transport = "trolleybus" if "/trolley/" in route else "bus"
        map_html, map_source = read_capture(root, route, "map")
        match = MAP_STOPS.search(map_html)
        if match is None:
            raise ValueError(f"{route}: missing stop coordinates")
        mapped = json.loads(match[1])
        evidence.append(map_source)
        for day, weekday, title_day in ((FIRST_DAY, 4, "четверг"), (NEXT_DAY, 5, "пятницу")):
            midnight = datetime.combine(day, time.min, zone)
            for direction, first in (("A", 0), ("B", reverse_start)):
                page, source = read_capture(root, route, f"{direction}?d={weekday}")
                evidence.append(source)
                title = re.search(r"<title>(.*?)</title>", page)
                if (
                    title is None
                    or f"расписание на {title_day}" not in title[1]
                    or f'<footer data-date="{FIRST_DAY:%d.%m.%Y},' not in page
                ):
                    raise ValueError(f"{source.url}: unexpected capture date or weekday")
                stops = STOP.findall(page)
                blocks = [EXPAND.sub("", block) for block in TIMES.findall(page)]
                rows = [service_times(block) for block in blocks]
                if (
                    len(stops) != len(rows)
                    or len(stops) < 2
                    or not rows[0]
                    or any(
                        len(row) != len(rows[0])
                        or len(CLOCK.findall(block)) != len(re.findall(r"<span\b", block))
                        or any(a >= b for a, b in zip(row, row[1:]))
                        for row, block in zip(rows, blocks, strict=True)
                    )
                ):
                    raise ValueError(f"{source.url}: incomplete or non-ordered exact timetable")
                node_ids = []
                for index, (path, name) in enumerate(stops):
                    location = mapped[(first + index) % len(mapped)]
                    if stop_name(name) != stop_name(location["name"]):
                        raise ValueError(f"{source.url}: stop order differs from map at {name}")
                    node_id = "kudikina-stop:" + canonical_hash(
                        [path, location["lat"], location["long"]]
                    )
                    nodes[node_id] = AccessNode(
                        node_id=node_id,
                        territory_id=city,
                        kind="transit_stop",
                        location={"lon": location["long"], "lat": location["lat"]},
                        source_refs=[map_source.evidence_id],
                    )
                    node_ids.append(node_id)
                for column, times in enumerate(zip(*rows, strict=True)):
                    if any(a > b for a, b in zip(times, times[1:])):
                        raise ValueError(f"{source.url}: reversed trip times in column {column}")
                    trip = f"kudikina:{route.replace('/', ':')}:{direction}:{day}:{column}"
                    refs = (source.evidence_id, map_source.evidence_id)
                    events = tuple(
                        TimetableEvent(
                            i,
                            node_id,
                            midnight + timedelta(seconds=t),
                            midnight + timedelta(seconds=t),
                            i < len(times) - 1,
                            i > 0,
                            refs,
                        )
                        for i, (node_id, t) in enumerate(zip(node_ids, times, strict=True))
                    )
                    services.append(
                        ScheduledService(
                            service_id=trip,
                            trip_id=trip,
                            mode="public_transport",
                            timezone=parent.timezone,
                            service_dates=[day],
                            trip_identity_confirmed=True,
                            stop_times=[
                                {
                                    "node_id": e.node_id,
                                    "sequence": e.sequence,
                                    "arrival_seconds": t,
                                    "departure_seconds": t,
                                    "pickup": e.pickup,
                                    "dropoff": e.dropoff,
                                }
                                for e, t in zip(events, times, strict=True)
                            ],
                            source_refs=list(refs),
                        )
                    )
                    runs.append(
                        TimetableRun(
                            parent.snapshot_id,
                            source.source_id,
                            trip,
                            day,
                            parent.timezone,
                            transport,
                            events,
                            True,
                            refs,
                        )
                    )
    version = "kudikina-dated-columns-v1:" + canonical_hash([e.content_hash for e in evidence])
    snapshot = AcceptedData.model_validate(
        {
            **parent.model_dump(),
            "provenance": {**parent.provenance.model_dump(), "calendar_version": version},
            "access_nodes": [*parent.access_nodes, *nodes.values()],
            "scheduled_services": services,
            "evidence": [*parent.evidence, *evidence],
            "source_versions": {**parent.source_versions, "kudikina": FIRST_DAY.isoformat()},
        }
    )
    scope = TimetableScope(
        snapshot.snapshot_id,
        snapshot.provenance.graph_version,
        version,
        version,
        "not_collected",
        "not_collected",
        snapshot.timezone,
        horizon_start,
        max(horizon_end, max(r.events[-1].arrival_at for r in runs) + timedelta(microseconds=1)),
        frozenset(nodes),
        False,
        True,
        False,
        False,
        False,
        tuple(e.evidence_id for e in evidence),
        ("BOUNDED_KUDIKINA_ROUTE_DATE_SUBSET",),
    )
    return (
        snapshot,
        Timetable(scope, tuple(runs), (), ()),
        {
            "routes": [route for route, _ in ROUTES[city]],
            "dated_runs": len(runs),
            "physical_stops": len(nodes),
            "capture_hashes": {str(e.url): e.content_hash for e in evidence},
            "request_date": FIRST_DAY.isoformat(),
            "buffer_date": NEXT_DAY.isoformat(),
            "trip_binding": "ordered columns with equal stop counts and monotonic times",
        },
    )
