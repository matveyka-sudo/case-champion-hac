"""Grid and sample point rules on a synthetic city (synthetic geometry, not real data)."""

import numpy as np
import pytest

shapely = pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")

from a2_data_pipeline.geo import OsmLayers, build_grid, to_crs  # noqa: E402
from shapely.geometry import box  # noqa: E402

CRS = "EPSG:32634"
X0, Y0 = 500_000, 6_060_000  # UTM 34N, multiples of 1000 m


def utm_box(x0: float, y0: float, x1: float, y1: float) -> shapely.Geometry:
    return to_crs(box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1), CRS, "EPSG:4326")


def layers(
    buildings: list[tuple[str, shapely.Geometry]], water: list[shapely.Geometry]
) -> OsmLayers:
    city = utm_box(0, 0, 3000, 1000)  # three cells side by side
    return OsmLayers(
        city=city,
        city_ref="relation/1",
        city_name="Синтетический",
        city_population_tag=None,
        districts=[("relation/2", "Район", city)],
        buildings=np.array([geometry for _, geometry in buildings], dtype=object),
        building_refs=[ref for ref, _ in buildings],
        water=np.array(water, dtype=object),
    )


def test_center_nearest_tie_and_exclusion() -> None:
    buildings = [
        # cell 0: building covers the centre (500, 500)
        ("way/20", utm_box(450, 450, 550, 550)),
        # cell 1: two buildings at equal distance from the centre (1500, 500): smaller id wins
        ("way/31", utm_box(1700, 480, 1750, 520)),
        ("way/30", utm_box(1250, 480, 1300, 520)),
        # building in cell 1 that is not nearest
        ("way/10", utm_box(1900, 900, 1950, 950)),
    ]
    water = [utm_box(2000, 0, 3000, 1000)]  # cell 2 is water
    grid = build_grid(layers(buildings, water), utm_epsg=32634)
    cells = grid.cells
    assert sorted(cells) == [(500, 6060), (501, 6060), (502, 6060)]
    first, second, third = (cells[key] for key in sorted(cells))
    assert first.placement == "cell_center" and first.offset_m == 0
    assert first.building_ref == "way/20"
    assert second.placement == "nearest_building_point"
    assert second.building_ref == "way/30"
    assert second.offset_m == pytest.approx(200, abs=0.5)
    assert third.point is None and third.exclusion_reason == "WATER_ONLY"


def test_point_never_moves_to_another_cell() -> None:
    # Only building of cell 0 straddles into cell 1; cell 1 must use its own part.
    buildings = [("way/1", utm_box(900, 400, 1100, 600))]
    grid = build_grid(layers(buildings, []), utm_epsg=32634)
    second = grid.cells[(501, 6060)]
    assert second.point is not None
    assert second.point.x >= X0 + 1000 - 1e-6
    third = grid.cells[(502, 6060)]
    assert third.point is None and third.exclusion_reason == "NO_BUILDING"
