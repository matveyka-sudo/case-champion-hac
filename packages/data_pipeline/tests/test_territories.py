"""Territory directory rules on synthetic SPARQL answers."""

import json
import zipfile
from pathlib import Path
from typing import Any

from a2_data_pipeline import territories

WD = "http://www.wikidata.org/entity/"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": WD + qid}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def answer(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"results": {"bindings": rows}}).encode()


SUBJECTS = [
    # oblast with an IANA zone, capital Q10
    {
        "s": uri("Q1"),
        "sLabel": lit("Тестовая область"),
        "iso": lit("RU-TST"),
        "capital": uri("Q10"),
        "tz": uri("Q900"),
        "iana": lit("Asia/Yekaterinburg"),
    },
    # autonomous okrug inside the oblast, only a UTC offset is known
    {
        "s": uri("Q2"),
        "sLabel": lit("Тестовый автономный округ"),
        "iso": lit("RU-TAO"),
        "capital": uri("Q20"),
        "tz": uri("Q901"),
        "offset": lit("5"),
    },
    # federal city: the subject and the city are one item
    {
        "s": uri("Q3"),
        "sLabel": lit("Москва"),
        "iso": lit("RU-MOW"),
        "tz": uri("Q902"),
        "iana": lit("Europe/Moscow"),
    },
    {
        "s": uri("Q4"),
        "sLabel": lit("Севастополь"),
        "iso": lit("UA-40"),
        "tz": uri("Q902"),
        "iana": lit("Europe/Moscow"),
    },
]
SUBJECTS += [
    {
        "s": uri("Q5"),
        "sLabel": lit("Калининградская область"),
        "iso": lit("RU-KGD"),
        "capital": uri("Q41"),
        "tz": uri("Q903"),
        "iana": lit("Europe/Kaliningrad"),
    },
    {
        "s": uri("Q6"),
        "sLabel": lit("Краснодарский край"),
        "iso": lit("RU-KDA"),
        "tz": uri("Q902"),
        "iana": lit("Europe/Moscow"),
    },
]
CITIES = [
    {
        "c": uri("Q10"),
        "cLabel": lit("Областной"),
        "coord": lit("Point(60.6 56.8)"),
        "pop": lit("1000"),
        "popDate": lit("2020-01-01T00:00:00Z"),
    },
    {
        "c": uri("Q10"),
        "cLabel": lit("Областной"),
        "pop": lit("1200"),
        "popDate": lit("2024-01-01T00:00:00Z"),
    },
    {"c": uri("Q20"), "cLabel": lit("Окружной"), "coord": lit("Point(73.4 61.2)")},
    {"c": uri("Q3"), "cLabel": lit("Москва"), "coord": lit("Point(37.6 55.75)")},
    {"c": uri("Q4"), "cLabel": lit("Севастополь"), "coord": lit("Point(33.5 44.6)")},
    {"c": uri("Q30"), "cLabel": lit("Ничейный"), "coord": lit("Point(40 50)")},
    {"c": uri("Q41"), "cLabel": lit("Калининград"), "coord": lit("Point(20.5 54.7)")},
    {"c": uri("Q51"), "cLabel": lit("Новороссийск"), "coord": lit("Point(37.8 44.7)")},
]
ADMIN = [
    {"x": uri("Q41"), "y": uri("Q5")},
    {"x": uri("Q51"), "y": uri("Q6")},
    {"x": uri("Q10"), "y": uri("Q1")},
    # the okrug city lies in the okrug, which lies in the oblast: the okrug is nearer
    {"x": uri("Q20"), "y": uri("Q200")},
    {"x": uri("Q200"), "y": uri("Q2")},
    {"x": uri("Q2"), "y": uri("Q1")},
]


def raw(tmp_path: Path) -> Path:
    path = tmp_path / "raw.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("subjects.json", answer(SUBJECTS))
        archive.writestr("cities.json", answer(CITIES))
        archive.writestr("admin.json", answer(ADMIN))
        archive.writestr("admin_any.json", answer(ADMIN))
    return path


def test_subjects_cities_and_zones(tmp_path: Path) -> None:
    entries, report = territories.build_entries(raw(tmp_path))
    by_id = {e.territory_id: e for e in entries}
    okrug_city = by_id["city-q20"]
    assert okrug_city.parent_region_id == "region-ru-tao"
    assert okrug_city.timezone == "Asia/Yekaterinburg"  # from the okrug's UTC offset
    assert by_id["region-ru-tao"].timezone_basis == "subject_offset"
    assert by_id["region-ru-tst"].capital_city_id == "city-q10"
    # the latest population statement wins
    assert (by_id["city-q10"].population, by_id["city-q10"].population_date) == (1200, "2024-01-01")
    # a federal city is its own subject and capital; pilot ids are kept
    assert by_id["moscow"].parent_region_id == "region-ru-mow"
    assert by_id["region-ru-mow"].capital_city_id == "moscow"
    # a subject without an RU- ISO code keeps a Wikidata id; disputed is marked
    assert by_id["region-q4"].disputed and by_id["city-q4"].disputed
    assert report["dropped_cities"]["no_subject"] == ["Ничейный"]


def test_territory_options_shape(tmp_path: Path) -> None:
    entries, _ = territories.build_entries(raw(tmp_path))
    options = territories.territory_options(entries)
    assert {tuple(sorted(o)) for o in options} == {
        ("capital_city_id", "level", "name", "parent_region_id", "territory_id", "timezone")
    }


def test_publish_and_load_round_trip(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("A2_DATA_DIR", str(tmp_path / "data"))
    stamp = territories.datetime(2026, 11, 9, tzinfo=territories.UTC)
    target = territories.publish(raw(tmp_path), "syn-territories-v1", stamp, "0" * 64)
    loaded = territories.load_directory("syn-territories-v1")
    assert {e.territory_id for e in loaded} >= {"moscow", "city-q20", "region-ru-tao"}
    (target / "territories.json").write_text("[]")
    try:
        territories.load_directory("syn-territories-v1")
    except ValueError as error:
        assert "sha256" in str(error)
    else:
        raise AssertionError("a tampered directory must not load")
