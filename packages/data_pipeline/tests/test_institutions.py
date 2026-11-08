"""Roster ↔ OSM matching rules on synthetic geometry."""

import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")

from a2_data_pipeline.categories import Candidate  # noqa: E402
from a2_data_pipeline.geo import to_crs  # noqa: E402
from a2_data_pipeline.institutions import (  # noqa: E402
    generic_name,
    institution_numbers,
    match_roster,
)
from a2_data_pipeline.klgd import Building  # noqa: E402
from shapely.geometry import Point  # noqa: E402

CRS = "EPSG:32634"
X0, Y0 = 500_000, 6_060_000


def lonlat(dx: float, dy: float) -> Point:
    return to_crs(Point(X0 + dx, Y0 + dy), CRS, "EPSG:4326")


def building(map_id: str, name: str, dx: float = 0, dy: float = 0) -> Building:
    point = lonlat(dx, dy)
    return Building(
        map_id=map_id,
        institution_id=map_id,
        kind="school",
        name=name,
        lat=point.y,
        lon=point.x,
        address_short=None,
        licence=None,
        accreditation=None,
    )


def candidate(ref: str, name: str | None, dx: float, dy: float = 0) -> Candidate:
    point = lonlat(dx, dy)
    return Candidate(
        category="school",
        osm_refs=[ref],
        name=name,
        tags={"name": name} if name else {},
        point=point,
        geometry=point,
    )


def test_numbers_and_generic_names() -> None:
    assert institution_numbers("МАОУ СОШ № 9 им. Дьякова П.М.") == {"9"}
    assert institution_numbers("МАОУ лицей 35") == {"35"}
    assert institution_numbers("Школа №07") == {"7"}
    assert generic_name("Детский сад корп. 2") and generic_name(None)
    assert not generic_name("Фикус")


def test_match_rules() -> None:
    buildings = [building("1", "МАОУ СОШ № 7"), building("2", "МАОУ СОШ № 8", dx=1000)]
    candidates = [
        candidate("node/1", "Школа № 7", dx=250),  # same number, 250 m: match
        candidate("node/2", "Школа № 8", dx=150),  # other number near 1, far from 2: no match
        candidate("node/3", None, dx=1040),  # unnamed, 40 m from 2: match
        candidate("node/4", "Фикус", dx=10),  # proper name without number: never
        candidate("node/5", "Школа", dx=80),  # generic name but 80 m: too far
    ]
    matches, unmatched, stats = match_roster(buildings, candidates, CRS)
    assert [c.osm_refs[0] for c in matches[0].candidates] == ["node/1"]
    assert [c.osm_refs[0] for c in matches[1].candidates] == ["node/3"]
    assert sorted(c.osm_refs[0] for c in unmatched) == ["node/2", "node/4", "node/5"]
    assert stats["near_but_other_number"] == 1
