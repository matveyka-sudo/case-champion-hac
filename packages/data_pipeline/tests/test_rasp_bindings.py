"""Rasp binding rules on a synthetic stations_list answer (no Rasp data in the repository)."""

from typing import Any

from a2_data_pipeline import rasp_bindings, territories
from a2_data_pipeline.intercity_nodes import IntercityNode, capital_boundaries


def station(code: str, kind: str, stype: str, lon: float, lat: float, esr: str | None = None):
    codes = {"yandex_code": code} | ({"esr_code": esr} if esr else {})
    return {
        "title": "x",
        "longitude": lon,
        "latitude": lat,
        "transport_type": kind,
        "station_type": stype,
        "codes": codes,
        "direction": "",
    }


def settlement(code: str, stations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": "x", "codes": {"yandex_code": code}, "stations": stations}


ANSWER = {
    "countries": [
        {
            "title": "x",
            "codes": {"yandex_code": "l225"},
            "regions": [
                {
                    "title": "x",
                    "codes": {"yandex_code": "r1"},
                    "settlements": [
                        settlement(
                            "c22",
                            [
                                station("s1", "train", "train_station", 20.50, 54.69, "100016"),
                                station("s2", "train", "station", 20.50, 54.72, "103616"),
                                station("s3", "bus", "bus_station", 20.502, 54.6934),
                            ],
                        ),
                        settlement(
                            "c10", [station("s4", "train", "station", 20.30, 54.73, "103014")]
                        ),
                        settlement("c30", [station("s5", "plane", "airport", 20.586, 54.883)]),
                        settlement("c40", [station("s6", "plane", "airport", 94.40, 51.67)]),
                    ],
                }
            ],
        },
        {"title": "x", "codes": {"yandex_code": "l149"}, "regions": []},
    ]
}


def node(node_id: str, kind: str, city: str, lon: float, lat: float, **extra: Any) -> IntercityNode:
    fields: dict[str, Any] = {
        "node_id": node_id,
        "kind": kind,
        "name": node_id,
        "wikidata_id": None,
        "wikidata_classes": [],
        "lon": lon,
        "lat": lat,
        "territory_ids": [city],
        "assignment_basis": "located_in",
        "esr_codes": [],
        "express_codes": [],
        "iata": None,
        "icao": None,
        "ourairports_ident": None,
        "ourairports_scheduled_service": None,
        "disputed": False,
    }
    return IntercityNode(**(fields | extra))


NODES = [
    node("n-south", "rail_station", "kaliningrad", 20.50, 54.69, esr_codes=["100016"]),
    node("n-north", "rail_station", "kaliningrad", 20.50, 54.72, esr_codes=["103616"]),
    node("n-out", "rail_station", "kaliningrad", 20.30, 54.73, esr_codes=["103014"]),
    node("n-bus", "bus_station", "kaliningrad", 20.5021, 54.6935),  # ~10 m from s3
    node("n-none", "rail_station", "kaliningrad", 20.4, 54.7, esr_codes=["999999"]),
    # Khrabrovo serves the city from outside: it does not choose the city's settlement
    node("n-kgd", "airport", "kaliningrad", 20.59, 54.89, assignment_basis="served_place"),
    # a capital with only an airport takes the airport's station code
    node("n-kyzyl", "airport", "kyzyl", 94.40, 51.669, assignment_basis="served_place"),
]


def entry(territory_id: str, level: str, **extra: Any) -> territories.TerritoryEntry:
    fields: dict[str, Any] = {
        "territory_id": territory_id,
        "level": level,
        "name": territory_id,
        "timezone": "Europe/Kaliningrad",
        "timezone_basis": "subject_iana",
        "parent_region_id": None,
        "capital_city_id": None,
        "wikidata_id": "Q-" + territory_id,
        "iso_code": None,
        "population": None,
        "population_date": None,
        "disputed": False,
    }
    return territories.TerritoryEntry(**(fields | extra))


DIRECTORY = [
    entry("region-ru-kgd", "region", capital_city_id="kaliningrad"),
    entry("region-ru-ty", "region", capital_city_id="kyzyl", timezone="Asia/Krasnoyarsk"),
    entry("region-disputed", "region"),
    entry("kaliningrad", "city", parent_region_id="region-ru-kgd"),
    entry("kyzyl", "city", parent_region_id="region-ru-ty", timezone="Asia/Krasnoyarsk"),
]


def test_codes_bind_nodes_cities_and_regions() -> None:
    codes, bindings, report = rasp_bindings.build(ANSWER, NODES, DIRECTORY, "rasp:test")
    by_node = {c.node_id: c for c in codes}
    assert by_node["n-south"].provider_code == "s1" and by_node["n-south"].basis == "esr_code"
    assert by_node["n-bus"].basis == "bus_station_within_300m"
    assert by_node["n-kgd"].provider_code == "s5" and not by_node["n-kgd"].counts_for_city
    assert "n-none" not in by_node
    by_territory = {b.territory_id: b for b in bindings}
    city = by_territory["kaliningrad"]
    # 3 of 4 counted nodes lie in c22: a strict majority; c10 is reported
    assert (city.provider_code, city.basis) == ("c22", "node_codes_majority")
    assert report["cities_several_settlements"] == ["kaliningrad: c10 (1), c22 (3) → c22"]
    assert (by_territory["kyzyl"].provider_code, by_territory["kyzyl"].basis) == (
        "s6",
        "served_airport",
    )
    region = by_territory["region-ru-kgd"]
    assert (region.provider_code, region.basis) == ("c22", "capital")
    assert "region-disputed" not in by_territory
    checks = {c["check_id"]: c["status"] for c in rasp_bindings._checks(bindings, report)}
    assert checks["territory_bound_once"] == checks["provider_code_names_one_city"] == "pass"


def test_settlement_without_majority_or_shared_is_not_bound() -> None:
    tie = [
        node("a", "rail_station", "kaliningrad", 20.50, 54.69, esr_codes=["100016"]),
        node("b", "rail_station", "kaliningrad", 20.30, 54.73, esr_codes=["103014"]),
        # a neighbour whose only station lies in the city's settlement
        node("c", "rail_station", "murino", 20.50, 54.72, esr_codes=["103616"]),
    ]
    directory = [*DIRECTORY, entry("murino", "city")]
    _, bindings, report = rasp_bindings.build(ANSWER, tie, directory, "rasp:test")
    assert {b.territory_id for b in bindings if b.level == "city"} == {"murino"}
    assert report["cities_without_majority"] == ["kaliningrad: c10 (1), c22 (1)"]


def test_ml1_inputs() -> None:
    modes = {row["transport_type"]: row["mode"] for row in rasp_bindings.transport_mode_bindings()}
    assert modes["plane"] == "air" and modes["suburban"] == "rail" and modes["bus"] == "road"
    rows = capital_boundaries(DIRECTORY, NODES, ["directory:test"])
    by_region = {r["region_id"]: r for r in rows}
    assert by_region["region-ru-kgd"]["capital_city_id"] == "kaliningrad"
    assert "n-kgd" in by_region["region-ru-kgd"]["node_ids"]
    assert "region-disputed" not in by_region  # no capital: unknown, not empty


def test_public_summary_has_no_rasp_codes() -> None:
    _, bindings, report = rasp_bindings.build(ANSWER, NODES, DIRECTORY, "rasp:test")
    summary = {"summary": report, "checks": rasp_bindings._checks(bindings, report)}
    text = str(rasp_bindings._public(summary))
    assert "c22" not in text and "c10" not in text
    assert rasp_bindings._public(summary)["summary"]["cities_several_settlements"] == [
        "kaliningrad"
    ]
