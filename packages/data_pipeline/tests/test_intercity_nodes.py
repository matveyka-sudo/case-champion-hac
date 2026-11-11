"""Intercity node rules on synthetic SPARQL answers and a synthetic OurAirports file."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a2_data_pipeline import intercity_nodes, territories

WD = "http://www.wikidata.org/entity/"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": WD + qid}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def answer(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"results": {"bindings": rows}}).encode()


def node(qid: str, cls: str, label: str, coord: str | None = "Point(20.5 54.7)", **extra: Any):
    row: dict[str, Any] = {"n": uri(qid), "cls": uri(cls), "label": lit(label)}
    if coord:
        row["coord"] = lit(coord)
    row.update(extra)
    return row


STATION = "Q55488"
RAIL = [
    # a city station through a city district; the code-anchored answer lists every class
    node("Q100", STATION, "Город-Пассажирский", esr=lit("100016"), uic=lit("2058001")),
    node("Q100", "Q2319498", "Город-Пассажирский"),
    # a village station: the walk stops at the subject and never reaches the town
    node("Q101", STATION, "Деревенская", esr=lit("100200")),
    # a closed station and a freight station
    node("Q102", STATION, "Старая", esr=lit("100300"), gone=lit("1990-01-01T00:00:00Z")),
    node("Q103", STATION, "Грузовая", esr=lit("100400")),
    # a coded item of no railway class (a junction) is not a node
    node("Q104", "Q1311670", "Узел", esr=lit("100500")),
]
BUS = [
    node("Q200", "Q494829", "Автовокзал"),
    node("Q201", "Q2920759", "Конечная станция Метро"),  # urban terminus subclass
]
AIR = [
    # joint-use: a civil airport also typed as a military aerodrome, outside the city
    node("Q300", "Q644371", "Аэропорт Храброво", iata=lit("KGD"), served=uri("Q10")),
    node("Q301", "Q62447", "Военный", icao=lit("XXXX")),
    node("Q302", "Q62447", "Площадка без кода"),
]
EXCLUDED = [
    {"n": uri("Q103"), "bad": uri("Q55493")},
    {"n": uri("Q300"), "bad": uri("Q695850")},
    {"n": uri("Q301"), "bad": uri("Q695850")},
]
ADMIN = [
    {"x": uri("Q100"), "y": uri("Q11")},  # station in a city district
    {"x": uri("Q11"), "y": uri("Q10")},  # the district lies in the city
    {"x": uri("Q10"), "y": uri("Q1")},
    {"x": uri("Q101"), "y": uri("Q12")},  # village
    {"x": uri("Q12"), "y": uri("Q13")},  # municipal district run from the town
    {"x": uri("Q13"), "y": uri("Q1")},  # the subject: the walk stops here
    {"x": uri("Q1"), "y": uri("Q10")},  # a capital-style back edge must not be followed
    {"x": uri("Q200"), "y": uri("Q10")},
    {"x": uri("Q301"), "y": uri("Q12")},
]
AIRPORTS_CSV = (
    '"id","ident","type","name","latitude_deg","longitude_deg","elevation_ft","continent",'
    '"iso_country","iso_region","municipality","scheduled_service","icao_code","iata_code",'
    '"gps_code","local_code","home_link","wikipedia_link","keywords"\n'
    '1,"UMKK","medium_airport","Khrabrovo Airport",54.89,20.59,,"EU","RU","RU-KGD",'
    '"Kaliningrad","yes","UMKK","KGD","UMKK",,,,\n'
    '2,"UXXX","small_airport","Somewhere",60,30,,"EU","RU","RU-LEN","Somewhere","yes",'
    '"UXXX","SWX","UXXX",,,,\n'
)


def directory() -> list[territories.TerritoryEntry]:
    common: dict[str, Any] = {
        "timezone": "Europe/Kaliningrad",
        "timezone_basis": "subject_iana",
        "iso_code": None,
        "population": None,
        "population_date": None,
        "disputed": False,
    }
    return [
        territories.TerritoryEntry(
            territory_id="region-ru-kgd",
            level="region",
            name="Калининградская область",
            parent_region_id=None,
            capital_city_id="kaliningrad",
            wikidata_id="Q1",
            **common,
        ),
        territories.TerritoryEntry(
            territory_id="kaliningrad",
            level="city",
            name="Калининград",
            parent_region_id="region-ru-kgd",
            capital_city_id=None,
            wikidata_id="Q10",
            lon=20.5,
            lat=54.7,
            **common,
        ),
    ]


def raw(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "nodes.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in (
            ("rail", RAIL),
            ("bus", BUS),
            ("air", AIR),
            ("excluded", EXCLUDED),
            ("admin", ADMIN),
        ):
            archive.writestr(f"{name}.json", answer(rows))
    airports = tmp_path / "airports.csv"
    airports.write_text(AIRPORTS_CSV, encoding="utf-8")
    return path, airports


def test_assignment_and_exclusions(tmp_path: Path) -> None:
    nodes, report = intercity_nodes.build_nodes(*raw(tmp_path), directory())
    by_id = {n.node_id: n for n in nodes}
    assert set(by_id) == {"node-wd-q100", "node-wd-q200", "node-wd-q300"}
    station = by_id["node-wd-q100"]
    assert (station.territory_ids, station.assignment_basis) == (["kaliningrad"], "located_in")
    assert (station.esr_codes, station.express_codes) == (["100016"], ["2058001"])
    assert station.wikidata_classes == [STATION]  # the heritage class is not a railway class
    airport = by_id["node-wd-q300"]
    assert airport.assignment_basis == "served_place"
    assert (airport.iata, airport.ourairports_ident, airport.ourairports_scheduled_service) == (
        "KGD",
        "UMKK",
        True,
    )
    assert report["dropped_counts"]["outside_directory_cities"] == 1  # the village station
    assert report["dropped_counts"]["closed"] == 1
    assert report["dropped_counts"]["excluded_class"] == 2  # freight station, military aerodrome
    assert report["dropped"]["airport_without_code"] == ["Площадка без кода (Q302)"]
    assert report["urban_bus_termini_left_out"] == 1
    assert report["ourairports_scheduled_unmatched"] == ["Somewhere (SWX)"]


def test_checks_flag_missing_pilot_kinds(tmp_path: Path) -> None:
    nodes, report = intercity_nodes.build_nodes(*raw(tmp_path), directory())
    checks = {c["check_id"]: c for c in intercity_nodes._checks(nodes, report)}
    assert checks["node_ids_unique"]["status"] == "pass"
    assert checks["pilot_cities_have_nodes"]["status"] == "warn"  # moscow is not in the fixture
    assert "kaliningrad" not in checks["pilot_cities_have_nodes"]["details"]["missing_kinds"]
    assert checks["bus_station_coverage"]["status"] == "warn"


def test_publish_and_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("A2_DATA_DIR", str(data))
    raw_zip, airports = raw(tmp_path)
    (data / "raw").mkdir(parents=True)
    raw_zip.rename(data / "raw" / "nodes.zip")
    airports.rename(data / "raw" / "airports.csv")
    manifests = {
        dataset_id: SimpleNamespace(
            dataset_id=dataset_id,
            source_id=dataset_id.split(".")[0],
            source_url="https://example.org/",
            retrieved_at=territories.datetime(2026, 11, 9, tzinfo=territories.UTC),
            raw_path=path,
            checksum=SimpleNamespace(value=intercity_nodes._digest(data / "raw" / path)),
            license=SimpleNamespace(name="CC0-1.0"),
        )
        for dataset_id, path in (("wd.syn", "nodes.zip"), ("oa.syn", "airports.csv"))
    }
    monkeypatch.setattr(intercity_nodes, "load_dataset_manifest", manifests.__getitem__)
    territory_dir = data / "directories" / "syn-territories"
    territory_dir.mkdir(parents=True)
    (territory_dir / "manifest.json").write_text("{}")
    monkeypatch.setattr(territories, "load_directory", lambda _id: directory())

    target = intercity_nodes.publish("wd.syn", "oa.syn", "syn-territories", "syn-nodes-v1")
    assert {n.node_id for n in intercity_nodes.load_nodes("syn-nodes-v1")} == {
        "node-wd-q100",
        "node-wd-q200",
        "node-wd-q300",
    }
    with pytest.raises(FileExistsError):
        intercity_nodes.publish("wd.syn", "oa.syn", "syn-territories", "syn-nodes-v1")
    (target / "nodes.json").write_text("[]")
    with pytest.raises(ValueError, match="sha256"):
        intercity_nodes.load_nodes("syn-nodes-v1")


def test_osm_bus_stations_join_or_add(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    shapely_wkb = pytest.importorskip("shapely.wkb")
    from shapely.geometry import box

    national = tmp_path / "national"
    national.mkdir()
    outline = box(20.40, 54.65, 20.60, 54.80)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "osm_ref": "relation/1674442",
                    "city_wikidata": "Q10",
                    "name": "Калининград",
                    "admin_level": None,
                    "boundary": "city",
                    "wkb": shapely_wkb.dumps(outline),
                }
            ]
        ),
        national / "city_boundaries.parquet",
    )

    def station(ref: str, name: str | None, lon: float, lat: float, qid: str | None = None):
        return {"osm_ref": ref, "name": name, "wikidata": qid, "lon": lon, "lat": lat}

    pq.write_table(
        pa.Table.from_pylist(
            [
                station("node/1", "Автовокзал", 20.5001, 54.7001),  # 15 m from the Wikidata node
                station("node/2", "Автостанция Северная", 20.45, 54.78),
                station("way/3", "Автостанция Северная", 20.4505, 54.7801),  # the same station
                station("node/4", "Конечная остановка автобуса", 20.48, 54.70),  # urban terminus
                station("node/5", "Автостанция Пригородная", 21.0, 54.7),  # outside the outline
            ]
        ),
        national / "bus_stations.parquet",
    )
    BUS.append(node("Q202", "Q494829", "Автобусная конечная Синтетическая"))
    try:
        nodes, report = intercity_nodes.build_nodes(*raw(tmp_path), directory(), national)
    finally:
        BUS.pop()
    by_id = {n.node_id: n for n in nodes}
    assert "node-wd-q202" not in by_id  # v2: an urban terminus by name
    assert report["dropped_counts"]["urban_terminus_name"] == 1
    assert by_id["node-wd-q200"].osm_refs == ["node/1"]  # joined, not a second node
    northern = by_id["node-osm-node-2"]
    assert (northern.osm_refs, northern.territory_ids) == (["node/2", "way/3"], ["kaliningrad"])
    assert northern.assignment_basis == "osm_outline"
    stats = report["osm_bus_stations"]
    assert (stats["named_bus_station"], stats["added"], stats["outside_city_outlines"]) == (4, 1, 1)
    assert report["coverage"]["bus_station"]["reason"] == "OSM_NAMED_BUS_STATIONS_IN_CITY_OUTLINES"
