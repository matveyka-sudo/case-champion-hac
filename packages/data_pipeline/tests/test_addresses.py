"""Russian address parsing and settlement-safe matching (synthetic addresses)."""

from pathlib import Path

import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from a2_data_pipeline.addresses import (  # noqa: E402
    AddressIndex,
    HouseKey,
    house_key,
    parse_address,
    street_tokens,
)
from shapely.geometry import Point, box  # noqa: E402


@pytest.mark.parametrize(
    ("address", "settlement", "street", "house"),
    [
        (
            "236008, Калининградская область, г. Калининград, А.Невского ул, д. 117-123",
            "калининград",
            {"невского"},
            HouseKey(117, None),
        ),
        (
            "236022, Калининградская область, Калининград, ул.Генделя, д.6",
            "калининград",
            {"генделя"},
            HouseKey(6, None),
        ),
        (
            "236011, Калининградская область, г. Калининград, Л.Шевцовой б-р, 92А",
            "калининград",
            {"шевцовой"},
            HouseKey(92, "а"),
        ),
        (
            "236004, Калининградская область, г. Калининград, Дзержинского ул, Дом 104-104В",
            "калининград",
            {"дзержинского"},
            HouseKey(104, None),
        ),
        (
            "236039, Калининградская область, Калининград, Октябрьская, д.71-73",
            "калининград",
            {"октябрьская"},
            HouseKey(71, None),
        ),
        (
            "238150, Калининградская область, Черняховск, ул. Ленина, д. 18",
            "черняховск",
            {"ленина"},
            HouseKey(18, None),
        ),
        (
            "236001, Калининградская область, городской округ город Калининград, г Калининград, ул. Адмирала Егорова, зд. 2",
            "калининград",
            {"адмирала", "егорова"},
            HouseKey(2, None),
        ),
        # postcode glued to the town, «г.» without a space
        ("353900 г.Новороссийск, ул. Мира, 32", "новороссийск", {"мира"}, HouseKey(32, None)),
        # the house number ends the street component
        ("353913, г. Новороссийск, пр. Ленина 63", "новороссийск", {"ленина"}, HouseKey(63, None)),
        ("Новороссийск, с.Глебовское, ул. Чехова 17", "глебовское", {"чехова"}, HouseKey(17, None)),
    ],
)
def test_parse_address(address: str, settlement: str, street: set[str], house: HouseKey) -> None:
    parsed = parse_address(address)
    assert parsed is not None
    assert parsed.settlement == settlement
    assert set(parsed.street) == street
    assert parsed.house == house


def test_tokens_and_houses() -> None:
    assert street_tokens("улица Александра Невского") >= street_tokens("А.Невского ул")
    assert house_key("1а") == HouseKey(1, "а")
    assert house_key("д. 2-А, пом. 1А") == HouseKey(2, None)
    assert HouseKey(24, None).matches(HouseKey(24, "б"))
    assert not HouseKey(24, "а").matches(HouseKey(24, "б"))


def write_addresses(
    directory: Path, rows: list[tuple[str, str, str, str | None, float, float]]
) -> None:
    table = [
        {
            "osm_ref": ref,
            "street": street,
            "housenumber": house,
            "city": city,
            "wkb": shapely.to_wkb(box(lon, lat, lon + 0.0005, lat + 0.0003)),
            "is_building": True,
            "content_hash": "0" * 64,
        }
        for ref, street, house, city, lon, lat in rows
    ]
    pq.write_table(pa.Table.from_pylist(table), directory / "addresses.parquet")


def test_index_respects_settlements_and_nearest_number(tmp_path: Path) -> None:
    city = box(20.40, 54.68, 20.60, 54.78)
    area = box(20.30, 54.60, 20.70, 54.85)
    write_addresses(
        tmp_path,
        [
            ("way/1", "улица Ленина", "18", None, 20.50, 54.70),  # in the city
            ("way/2", "улица Ленина", "18", None, 20.65, 54.82),  # untagged village outside
            ("way/3", "улица Ленина", "18", "Гурьевск", 20.62, 54.80),  # tagged other town
            ("way/4", "улица Марины Расковой", "8", None, 20.47, 54.72),
        ],
    )
    index = AddressIndex(tmp_path, area, city, "Калининград")
    in_city = index.locate("236000, Калининградская обл., г. Калининград, ул. Ленина, д. 18")
    assert in_city is not None and in_city.osm_ref == "way/1" and in_city.method == "exact"
    other = index.locate("238300, Калининградская обл., г. Гурьевск, ул. Ленина, д. 18")
    assert other is not None and other.osm_ref == "way/3"
    # a town without tagged addresses must not borrow a namesake street nearby
    assert index.locate("238150, Калининградская обл., Черняховск, ул. Ленина, д. 18") is None
    near = index.locate("236017, г Калининград, ул М.Расковой, д. 10")
    assert near is not None and near.osm_ref == "way/4" and near.method == "nearest_number"
    assert index.locate("236017, г Калининград, ул М.Расковой, д. 9") is None  # other parity
    assert isinstance(near.point, Point)
    # an address without a settlement is found only when the caller names the city
    assert index.locate("236000, ул. Ленина, д. 18") is None
    own = index.locate("236000, ул. Ленина, д. 18", default_settlement="Калининград")
    assert own is not None and own.osm_ref == "way/1"
