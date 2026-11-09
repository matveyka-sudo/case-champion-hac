"""Urban freight rules (freight-osm-rules-v2) on synthetic OSM rows and a synthetic port page."""

import json
from typing import Any

import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")

from a2_data_pipeline import freight  # noqa: E402
from a2_data_pipeline.geo import to_crs  # noqa: E402

CRS = "EPSG:32637"
X0, Y0 = 400_000, 4_950_000
CITY = shapely.box(X0, Y0, X0 + 5000, Y0 + 5000)

# The markup of a Rosmorport branch page: the stevedore list sits in an HTML comment.
PAGE = """
<div class="list_parent"><a>Морской порт Тестовый</a></div>
<div>Регистрационный номер морского порта</div>
<div>Регистрационный номер морского порта</div>
<div>Т-1</div>
<div>&nbsp;- наливные: 10 тыс. тонн в год;<br>&nbsp;- сухие:</div><div>5 тыс. тонн в год;</div>
<div>&nbsp;- контейнеры: 7 тыс. ДФЭ в год;</div>
<div class="list_parent li-two"><a>Стивидорные услуги</a></div>
<div class="list_parent-info ul-two"><div class="li-three"><!---ООО «Терминал «ТТК»<br>
 АО «Синтетический морской торговый порт»<br>
 АО «КТК-Синтетик»<br>---></div></div>
<div class="list_parent li-two"><a>Лоцманские услуги</a></div>
<div class="list_parent-info ul-two"><div class="li-three"><a href="https://example.org">ФГУП «Тест»</a><br></div></div>
<p>Выносные причальные устройства КТК</p>
<div class="list_parent"><a>Морской порт Соседний</a></div>
<div class="list_parent li-two"><a>Стивидорные услуги</a></div>
<div class="list_parent-info ul-two"><div class="li-three">ООО «Чужой терминал»<br></div></div>
"""
PUBLISHED = PAGE.replace("<!---", "").replace("--->", "")


def box(x: float, y: float, size: float) -> Any:
    square = shapely.box(X0 + x, Y0 + y, X0 + x + size, Y0 + y + size)
    return to_crs(square, CRS, "EPSG:4326")


def point(x: float, y: float) -> Any:
    return to_crs(shapely.Point(X0 + x, Y0 + y), CRS, "EPSG:4326")


def row(ref: str, geometry: Any, **tags: str) -> dict[str, Any]:
    return {
        "osm_ref": ref,
        "tags": json.dumps(tags, ensure_ascii=False),
        "wkb": shapely.to_wkb(geometry),
        "content_hash": "0" * 64,
    }


ROWS = [
    row("way/1", box(0, 0, 400), landuse="harbour", name="Тестовый порт, участок 1"),
    row("way/2", box(100, 100, 100), landuse="industrial"),  # inside the port area
    row("way/3", box(1000, 0, 200), landuse="industrial", name="Контейнерный терминал ТТК"),
    row("way/4", box(2000, 0, 300), landuse="industrial", industrial="oil", name="Сооружения КТК"),
    # an explicit pipeline operator name wins over a wrong product tag
    row("way/5", box(2500, 0, 300), landuse="industrial", industrial="gas", name="ПНБ Тестовая"),
    row("way/6", box(0, 1000, 150), landuse="industrial", name="Завод"),
    row("way/7", box(20, 1020, 40), building="industrial"),  # inside the site
    row("way/8", box(0, 2000, 50), landuse="industrial"),  # 0.25 ha
    row("way/9", box(1000, 2000, 200), landuse="industrial", industrial="depot"),
    row("way/10", box(1500, 2000, 200), landuse="industrial", name="Очистные сооружения"),
    row("way/11", box(2000, 2000, 40), building="warehouse", name="Склад"),  # 1600 m²
    row("way/12", box(2500, 2000, 20), building="warehouse"),  # 400 m²
    row("way/13", box(3000, 2000, 200), landuse="industrial", disused="yes"),
    row("way/14", box(9000, 9000, 200), landuse="industrial"),  # outside the city
    row("node/15", point(3000, 3000), man_made="pier"),
    row("way/17", box(3500, 0, 200), landuse="industrial", name="Тестовый морской торговый порт"),
    # «аэропорт» is not a port; the airport is not a production site either
    row("way/18", box(4000, 1000, 200), landuse="industrial", name="Аэропорт Тестовый"),
]
STATIONS = [row("node/16", point(4000, 4000), railway="station", name="Тестовая")]


def test_operator_keys_are_distinctive_words() -> None:
    assert freight.operator_keys("ОАО «КТ НУТЭП»") == {"нутэп"}
    assert freight.operator_keys("АО «Зерновой терминал «КСК»") == {"кск"}
    assert freight.operator_keys("ПАО «Новороссийский морской торговый порт»") == {"торговый"}


def test_parse_seaport_reads_only_its_section() -> None:
    record = freight.parse_seaport(PAGE, "Тестовый")
    assert record.registration_number == "Т-1"
    # the first name right after the comment opener is kept
    assert [o.name for o in record.operators] == [
        "ООО «Терминал «ТТК»",
        "АО «Синтетический морской торговый порт»",
        "АО «КТК-Синтетик»",
    ]
    assert record.operators_published is False
    assert record.facts["liquid_capacity"] == "10 тыс. тонн в год;"  # value on the same line
    assert record.facts["dry_capacity"] == "5 тыс. тонн в год;"  # value on the next line
    assert record.facts["container_capacity"] == "7 тыс. ДФЭ в год;"
    assert record.facts["mentions_ktk_berths"] is True
    assert freight.parse_seaport(PUBLISHED, "Тестовый").operators_published is True
    with pytest.raises(ValueError, match="not found"):
        freight.parse_seaport(PAGE, "Несуществующий")


def test_hidden_text_is_not_a_fact() -> None:
    page = PAGE.replace("<p>Выносные причальные устройства КТК</p>", "")
    # «КТК» now appears only inside the hidden operator list
    assert freight.parse_seaport(page, "Тестовый").facts["mentions_ktk_berths"] is False


def test_classify_nodes_and_sites() -> None:
    record = freight.parse_seaport(PUBLISHED, "Тестовый")
    nodes, sites, counts = freight.classify(ROWS, STATIONS, CITY, CRS, record)
    found = {n.osm_refs[0]: (n.kind, n.operator, n.rule, n.reasons) for n in nodes}
    assert found == {
        "way/1": ("port", None, "port_area", []),
        "way/3": ("port", "ООО «Терминал «ТТК»", "operator_name", []),
        "way/4": ("pipeline_terminal", None, "oil+pipeline_name", []),
        "way/5": ("pipeline_terminal", None, "oil+pipeline_name", []),
        "way/17": ("port", None, "port_name", []),
        "node/16": ("rail_terminal", None, "railway_station", ["FREIGHT_OPERATIONS_UNVERIFIED"]),
    }
    assert [(s.osm_ref, s.kind, s.name) for s in sites] == [
        ("way/6", "industrial", "Завод"),
        ("way/11", "warehouse", "Склад"),
    ]
    assert sites[0].area_m2 == pytest.approx(22_500, rel=1e-3)
    assert counts == {
        "port": 3,
        "pipeline_terminal": 2,
        "rail_terminal": 1,
        "site_inside_node": 1,
        "site_too_small": 1,
        "site_not_production": 3,
        "building_inside_industrial_site": 1,
        "building_too_small": 1,
        "sites": 2,
    }


def test_hidden_operator_list_does_not_confirm_a_node() -> None:
    nodes, _, _ = freight.classify(ROWS, [], CITY, CRS, freight.parse_seaport(PAGE, "Тестовый"))
    reasons = {n.osm_refs[0]: n.reasons for n in nodes if n.kind == "port"}
    # found by the operator name only: quarantined; port tags and a port name still count
    assert reasons == {"way/1": [], "way/3": ["OPERATOR_LIST_UNPUBLISHED"], "way/17": []}


def test_port_without_seaport_record_is_quarantined() -> None:
    nodes, sites, _ = freight.classify(ROWS, [], CITY, CRS, None)
    reasons = {n.osm_refs[0]: n.reasons for n in nodes if n.kind == "port"}
    assert reasons == {"way/1": ["SEAPORT_RECORD_MISSING"], "way/17": ["SEAPORT_RECORD_MISSING"]}
    # without the operator list a terminal-like name proves nothing: it stays a site
    assert "way/3" in {s.osm_ref for s in sites}


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"railway": "station"}, True),
        ({"railway": "station", "station": "subway"}, False),
        ({"railway": "station", "disused": "yes"}, False),
        ({"railway": "halt"}, False),
    ],
)
def test_is_rail_station(tags: dict[str, str], expected: bool) -> None:
    assert freight.is_rail_station(tags) is expected


def test_empty_capacity_label_is_not_a_value() -> None:
    page = (
        "<div><a>Морской порт Пустой</a></div>"
        "<p>– наливные:<br>– сухие:<br>3 тыс. тонн в год;<br>"
        "– контейнеры:<br>• Пропускная способность пассажирских терминалов:</p>"
    )
    facts = freight.parse_seaport(page, "Пустой").facts
    assert "liquid_capacity" not in facts  # the next line is another label
    assert facts["dry_capacity"] == "3 тыс. тонн в год;"  # en dash label, value below
    assert "container_capacity" not in facts
