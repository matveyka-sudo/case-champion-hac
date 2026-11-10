"""klgd.ru roster parsing on synthetic pages (no real institution data)."""

import zipfile
from pathlib import Path

from a2_data_pipeline import klgd

LIST_PAGE = """
<table>
<tr><td>Наименование</td><td>Район</td></tr>
<tr bgcolor="#FFFFFF"><td><a href="/x/detail.php?ID=101">МАОУ СОШ № 7</a></td>
<td>Ленинградский<br>236029, г. Калининград, ул. Синтетическая, 1</td><td>(4012) 00-00-00</td>
<td>Иванов Иван Иванович</td></tr>
<tr><td><a href="/x/detail.php?ID=102">МАОУ лицей 35</a></td><td>Центральный<br>ул. Тестовая, 2</td>
<td></td><td></td></tr>
</table>
"""
MAP_PAGE = (
    'var items = {"e101":{"name":"МАОУ СОШ № 7","url":"detail.php?ID=101","LAT":54.7,'
    '"LNG":20.5,"cat":"s29586","adress_short":"Синтетическая 1","director":"Иванов"},'
    '"e555":{"name":"МАОУ СОШ № 7","url":"detail.php?ID=555","LAT":54.71,"LNG":20.51,'
    '"cat":"s29586","adress_short":"Синтетическая 9"},'
    '"e777":{"name":"МАДОУ д/с № 99","url":"detail.php?ID=777","LAT":54.72,"LNG":20.52,'
    '"cat":"s29585"},'
    '"e888":{"name":"Лагерь","url":"detail.php?ID=888","LAT":54.73,"LNG":20.53,"cat":"s29608"}};'
)


def test_parse_list_keeps_facts_only() -> None:
    rows = klgd.parse_list(LIST_PAGE, "school")
    assert [(r.institution_id, r.name, r.district) for r in rows] == [
        ("101", "МАОУ СОШ № 7", "Ленинградский"),
        ("102", "МАОУ лицей 35", "Центральный"),
    ]
    assert "Иванов" not in repr(rows)
    assert rows[0].address == "236029, г. Калининград, ул. Синтетическая, 1"


def test_parse_map_skips_other_layers_and_personal_data() -> None:
    points = klgd.parse_map(MAP_PAGE)
    assert [(p.map_id, p.kind) for p in points] == [
        ("101", "school"),
        ("555", "school"),
        ("777", "kindergarten"),
    ]
    assert "Иванов" not in repr(points)


def test_roster_attaches_buildings_by_id_or_exact_name(tmp_path: Path) -> None:
    raw = tmp_path / "roster.zip"
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("school-p01.html", LIST_PAGE.encode("cp1251"))
        archive.writestr("map.html", MAP_PAGE.encode("cp1251"))
    roster = klgd.load_roster(raw)
    assert sorted(b.map_id for b in roster.buildings_of("101")) == ["101", "555"]
    assert roster.qa["institutions_without_coordinates"] == ["102"]
    assert roster.qa["map_points_not_in_founder_lists"] == [
        {"map_id": "777", "kind": "kindergarten", "name": "МАДОУ д/с № 99"}
    ]
    assert roster.record_hash("101") == klgd.load_roster(raw).record_hash("101")


def test_municipal_forms() -> None:
    assert klgd.is_municipal("МАОУ СОШ № 7")
    assert klgd.is_municipal("МБДОУ д/с № 16")
    assert not klgd.is_municipal("ГАОУ КО Лицей")
    assert not klgd.is_municipal("ООО Детский клуб")
