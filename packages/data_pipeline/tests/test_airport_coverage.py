"""An engineering extraction radius does not prove a city-serving airport roster."""

from dataclasses import replace

import pytest
from a2_data_pipeline.build_city import _coverage
from a2_data_pipeline.cities import CITIES
from a2_data_pipeline.store import load_bundle


@pytest.mark.parametrize("unverified", [False, True])
@pytest.mark.parametrize("has_airport", [False, True])
def test_distance_and_scheduled_flag_do_not_close_airport_roster(
    unverified: bool, has_airport: bool
) -> None:
    config = replace(CITIES["kaliningrad"], airport_service_unverified=unverified)
    destinations = [
        d
        for d in load_bundle("kaliningrad-urban-2026-11-10.4").destinations
        if d.category == "airport" and d.status == "accepted" and has_airport
    ]
    records = _coverage(
        config, destinations, {"osm": "osm", "ghsl": "ghsl", "airports": "airports"}, {}, 3
    )
    airport = next(r for r in records if r.category == "airport")
    assert airport.status == ("partial" if has_airport else "unknown")
    assert "полный перечень" in airport.reason
