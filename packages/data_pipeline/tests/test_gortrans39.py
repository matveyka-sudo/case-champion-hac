"""gortrans39 frequency rules on synthetic route pages (no real timetables)."""

import pytest
from a2_data_pipeline import gortrans39
from a2_data_pipeline.bundle import StopFrequency
from pydantic import ValidationError


def page(title: str, listed: list[str], body: str) -> str:
    items = "".join(f'<li><span class="routesmap-stop-name">{n}</span></li>' for n in listed)
    return (
        f"<title>{title} - АО «Перевозчик» - сайт</title><ul>{items}</ul>"
        f"<script>\nvar scheduleData = {{\n{body}\n}};\n</script>"
    )


BY_DAY = page(
    "Автобус №1",
    ["Школа №5", "Парк «Южный»"],
    """    'Школа 5': {
        weekdays: ['06:05','06:20','06:20','07:10'],
        saturday: [],
        weekend: ['08:00','09:00']
    },
    'Парк Южный': {
        weekdays: ['06:15','06:30','07:20'],
        saturday: [],
        weekend: ['08:10','09:10']
    }""",
)
UNIFIED = page(
    "Трамвай №3",
    ["Депо"],
    """    'Депо': ['06:00','06:13','06:26','07:00']""",
)
GRID = "        weekdays: ['06:00','06:30','07:00','07:30'],\n        saturday: [],\n        weekend: []"
TEMPLATE_A = page("Автобус №2", ["А"], "    'А': {\n" + GRID + "\n    }")
TEMPLATE_B = page("Автобус №3", ["Б"], "    'Б': {\n" + GRID + "\n    }")


def routes() -> dict[str, gortrans39.RouteTimetable]:
    parsed = [
        gortrans39.parse_route("av1", BY_DAY),
        gortrans39.parse_route("tm3", UNIFIED),
        gortrans39.parse_route("av2", TEMPLATE_A),
        gortrans39.parse_route("av3", TEMPLATE_B),
    ]
    gortrans39.mark_templates(parsed)
    return {r.route_id: r for r in parsed}


def test_both_page_formats() -> None:
    parsed = routes()
    bus, tram = parsed["av1"], parsed["tm3"]
    assert (bus.name, bus.mode, bus.unified) == ("Автобус №1", "bus", False)
    # the page's spelling replaces the timetable key when it is the same stop
    assert [s.name for s in bus.stops] == ["Школа №5", "Парк «Южный»"]
    assert tram.unified and tram.mode == "tram"
    assert tram.stops[0].days["saturday"] == tram.stops[0].days["weekdays"]


def test_templates_are_quarantined() -> None:
    parsed = routes()
    assert parsed["av1"].reasons == [] and parsed["tm3"].reasons == []
    assert parsed["av2"].reasons == ["SHARED_TEMPLATE_TIMETABLE", "REGULAR_GRID_TIMETABLE"]
    assert parsed["av3"].reasons == ["SHARED_TEMPLATE_TIMETABLE", "REGULAR_GRID_TIMETABLE"]


def test_frequency_counts_unique_departures_and_keeps_unknown() -> None:
    stop = routes()["av1"].stops[0]
    weekdays = gortrans39.frequency(stop.days["weekdays"])
    assert weekdays.departures == 3 and weekdays.duplicate_times == 1
    assert (weekdays.first, weekdays.last) == ("06:05", "07:10")
    assert weekdays.max_hourly == 2 and weekdays.median_gap_min == 32.5
    saturday = gortrans39.frequency(stop.days["saturday"])
    assert saturday.departures is None and saturday.first is None  # empty is unknown


def test_normal_name() -> None:
    assert gortrans39.normal_name("Школа №59") == gortrans39.normal_name("Школа 59")
    assert gortrans39.normal_name("Улица Зелёная") == gortrans39.normal_name("Улица Зеленая")
    assert gortrans39.normal_name("Карташева (по требованию)") == "карташева"


def test_accepted_frequency_needs_departures() -> None:
    row = {
        "frequency_id": "syn-av1-0-saturday",
        "city_id": "syn",
        "route_id": "av1",
        "route_name": "Автобус №1",
        "mode": "bus",
        "stop_name": "Школа №5",
        "stop_list_position": 0,
        "day_type": "saturday",
        "unified_timetable": False,
        "departures": None,
        "first_departure": None,
        "last_departure": None,
        "max_hourly_departures": None,
        "median_gap_min": None,
        "duplicate_times": 0,
        "lon": None,
        "lat": None,
        "osm_stop_refs": [],
        "status": "accepted",
        "quarantine_reasons": [],
        "evidence_ids": ["e-syn"],
    }
    with pytest.raises(ValidationError, match="known departures"):
        StopFrequency.model_validate(row)
    quarantined = row | {"status": "quarantined", "quarantine_reasons": ["EMPTY_DAY_LIST"]}
    assert StopFrequency.model_validate(quarantined).departures is None
