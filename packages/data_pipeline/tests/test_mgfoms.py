"""MGFOMS polyclinic rules on a synthetic directory page (no real contacts)."""

import json
from typing import Any

from a2_data_pipeline import mgfoms

ATTACHED = "ПОЛИКЛИНИКА С ПРИКРЕПЛЕНИЕМ"
GBUZ = "Государственное бюджетное учреждение здравоохранения города Москвы"


def item(**fields: Any) -> dict[str, Any]:
    base = {
        "ID": "1",
        "MO_CODE": "770001",
        "FILIAL_CODE": None,
        "TITLE": f"{GBUZ} &quot;Городская поликлиника № 1 Департамента здравоохранения города Москвы&quot;",
        "RUKOVODITEL": "Петров Пётр Петрович",
        "EMAIL": "x@example.org",
        "PHONE": "+7 (000) 000-00-00",
        "PRINADL_NAME": "ДЗМ",
        "TIP_NAMES": [ATTACHED],
        "NASEL_NAMES": ["ВЗРОСЛОЕ"],
        "ADDRESS": "100000, г. Москва, ул. Тестовая, д. 1",
        "MAP_LAT": "55.75",
        "MAP_LNG": "37.62",
    }
    return base | fields


ITEMS = [
    item(),
    # a filial at the same point as the head organisation: one object
    item(ID="2", FILIAL_CODE="ф1"),
    item(
        ID="3",
        MO_CODE="770002",
        TITLE=f"{GBUZ.upper()} &quot;ДЕТСКАЯ ГОРОДСКАЯ ПОЛИКЛИНИКА N 99 ДЕПАРТАМЕНТА ЗДРАВООХРАНЕНИЯ&quot;",
        FILIAL_CODE="Ф2",
        NASEL_NAMES=["ДЕТСКОЕ"],
        MAP_LAT="55.70",
    ),
    item(ID="4", MO_CODE="770003", NASEL_NAMES=["ВЗРОСЛОЕ", "ДЕТСКОЕ"], MAP_LAT="55.80"),
    item(ID="5", MO_CODE="770004", PRINADL_NAME="ВЕДОМСТВЕННЫЕ", MAP_LAT="55.81"),
    item(ID="6", MO_CODE="770005", TIP_NAMES=["СТАЦИОНАР"], MAP_LAT="55.82"),
    item(
        ID="7", MO_CODE="770006", TITLE="&quot;Женская консультация № 3&quot; ГБУЗ", MAP_LAT="55.83"
    ),
    item(ID="8", MO_CODE="770007", MAP_LAT="", MAP_LNG=""),
    # one MO code, two buildings: two objects, not one
    item(ID="9", MO_CODE="770008", MAP_LAT="55.90"),
    item(ID="10", MO_CODE="770008", MAP_LAT="55.91"),
]
PAGE = f"<script>var allItems = {json.dumps(ITEMS, ensure_ascii=False)};</script>"


def test_city_polyclinics_by_attachment_and_affiliation() -> None:
    entries = mgfoms.read_entries(PAGE)
    assert len(entries) == 9  # the entry without coordinates is not read
    objects, stats = mgfoms.polyclinics(entries)
    assert [(o.category, o.lat, len(o.entries)) for o in objects] == [
        ("adult_polyclinic", 55.75, 2),
        ("adult_polyclinic", 55.8, 1),
        ("adult_polyclinic", 55.9, 1),
        ("adult_polyclinic", 55.91, 1),
        ("child_polyclinic", 55.7, 1),
        ("child_polyclinic", 55.8, 1),
    ]
    assert all(o.ownership == "regional" for o in objects)
    assert stats["skipped_entries"] == {
        "not_city_owned": 1,
        "not_attached_polyclinic": 1,
        "womens_consultation": 1,
    }
    assert len({o.object_id for o in objects}) == 6
    assert objects[0].record_id == "770001:head:1;770001:Ф1:2"


def test_names_and_no_contacts() -> None:
    entries = {e.record_id: e for e in mgfoms.read_entries(PAGE)}
    assert entries["770001:head:1"].name == "Городская поликлиника № 1"
    assert entries["770001:Ф1:2"].name == "Городская поликлиника № 1, филиал Ф1"
    assert entries["770002:Ф2:3"].name == "Детская городская поликлиника № 99, филиал Ф2"
    text = repr(list(entries.values()))
    assert "example.org" not in text and "Петров" not in text and "+7" not in text
