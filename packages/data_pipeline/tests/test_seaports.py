"""Seaport directory rules on synthetic Rosmorport pages and a synthetic national OSM pass."""

import zipfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from a2_data_pipeline import seaports, territories  # noqa: E402
from shapely import wkb as shapely_wkb  # noqa: E402
from shapely.geometry import box  # noqa: E402

LIST = """<html><body><div>
<h1>Перечень морских портов Российской Федерации, в которых осуществляет деятельность ФГУП «Росморпорт»</h1>
<p>БАЛТИЙСКИЙ БАССЕЙН</p><p>Северо-Западный бассейновый филиал</p>
<p>Калининград</p><p>Приморск</p>
<p>АРКТИЧЕСКИЙ БАССЕЙН</p><p>Арктический бассейновый филиал</p><p>Сабетта</p>
<!-- <p>Скрытый</p> -->
</div><div><a>Морские порты</a></div></body></html>"""
BRANCH = """<html><body>
<div class="main-company-news__item-info-only-title "><a>Морской порт Калининград</a></div>
<div class="list_parent li-one"><a>Регистрационный номер морского порта</a></div>
<div>Б-7</div>
<p>• Пропускная способность грузовых терминалов всего:<br>45 023,2 тыс. тонн в год;<br>
– наливные: 13 350 тыс. тонн в год;<br>– сухие:<br>21 893,2 тыс. тонн в год;<br>
– контейнеры:<br>• Максимальные габариты судов:</p>
<div class="main-company-news__item-info-only-title "><a>Морской порт Приморск</a></div>
<div class="list_parent li-one"><a>Регистрационный номер морского порта</a></div>
<div>Б-5</div><p>– наливные: 12,5 млн тонн;</p>
</body></html>"""


def raw(tmp_path: Path) -> Path:
    path = tmp_path / "rosmorport.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(seaports.LIST_PAGE, LIST)
        archive.writestr("spb_seaports.html", BRANCH)
    return path


def city(territory_id: str, name: str, qid: str) -> territories.TerritoryEntry:
    common: dict[str, Any] = {
        "timezone": "Europe/Kaliningrad",
        "timezone_basis": "subject_iana",
        "iso_code": None,
        "population": None,
        "population_date": None,
        "disputed": False,
        "capital_city_id": None,
    }
    return territories.TerritoryEntry(
        territory_id=territory_id,
        level="city",
        name=name,
        parent_region_id="region-ru-kgd",
        wikidata_id=qid,
        **common,
    )


def national(tmp_path: Path) -> Path:
    out = tmp_path / "national"
    out.mkdir()
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
        out / "city_boundaries.parquet",
    )
    ports = [
        ("way/1", box(20.41, 54.66, 20.42, 54.67)),  # small
        ("way/2", box(20.45, 54.68, 20.50, 54.70)),  # the largest: the location
        ("way/3", box(21.0, 54.9, 21.1, 55.0)),  # outside the city
    ]
    pq.write_table(
        pa.Table.from_pylist(
            [{"osm_ref": r, "name": None, "wkb": shapely_wkb.dumps(g)} for r, g in ports]
        ),
        out / "port_areas.parquet",
    )
    return out


def test_ports_join_roster_sections_cities_and_areas(tmp_path: Path) -> None:
    directory = [
        city("kaliningrad", "Калининград", "Q10"),
        city("city-q20", "Приморск", "Q20"),  # two cities share the name
        city("city-q21", "Приморск", "Q21"),
    ]
    ports, report = seaports.build_ports(raw(tmp_path), national(tmp_path), directory)
    by_id = {p.node_id: p for p in ports}
    assert report["listed"] == 3 and report["with_section"] == 2  # the hidden row is not listed
    kgd = by_id["seaport-kaliningrad"]
    assert (kgd.status, kgd.registration_number, kgd.territory_ids) == (
        "accepted",
        "Б-7",
        ["kaliningrad"],
    )
    assert kgd.osm_port_areas == ["way/2", "way/1"]
    assert 20.45 <= (kgd.lon or 0) <= 20.50
    assert kgd.capacities["total_capacity"].value == 45023.2
    assert kgd.capacities["dry_capacity"].value == 21893.2  # value on the next line
    assert "container_capacity" not in kgd.capacities  # an empty label is unknown
    primorsk = by_id["seaport-primorsk"]
    assert primorsk.assignment == "ambiguous_name"
    assert primorsk.capacities["liquid_capacity"].value == 12500.0  # «млн тонн»
    assert primorsk.quarantine_reasons == ["CITY_NOT_ASSIGNED"]
    sabetta = by_id["seaport-sabetta"]
    assert sabetta.quarantine_reasons == ["NO_PORT_SECTION", "CITY_NOT_ASSIGNED"]
    assert sabetta.assignment == "no_directory_city"


def test_capacity_text_is_read_in_thousands() -> None:
    assert seaports.capacity("89 500 тонн в год;").value == 89.5
    assert seaports.capacity("1 114 тыс. TEU в год;").unit == "thousand_teu_per_year"
    stated = seaports.capacity("нет;")
    assert (stated.value, stated.stated_none) == (None, True)
    assert seaports.slug("Петропавловск-Камчатский") == "petropavlovsk-kamchatskiy"
    assert seaports.slug("Шахтёрск") == "shakhtersk"
