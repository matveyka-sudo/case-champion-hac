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


def write_ghsl(tmp_path, value_at, cover_until_x: float = 3800.0):  # type: ignore[no-untyped-def]
    """A 100 m Mollweide raster over the synthetic city; ``value_at(x)`` by UTM x offset."""

    import zipfile

    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin

    to_moll = Transformer.from_crs(CRS, "ESRI:54009", always_xy=True)
    to_utm = Transformer.from_crs("ESRI:54009", CRS, always_xy=True)
    xs, ys = to_moll.transform(
        [X0 - 800, X0 + cover_until_x, X0 - 800, X0 + cover_until_x],
        [Y0 - 800, Y0 - 800, Y0 + 1800, Y0 + 1800],
    )
    west, north = np.floor(min(xs) / 100) * 100, np.ceil(max(ys) / 100) * 100
    width, height = int((max(xs) - west) // 100) + 1, int((north - min(ys)) // 100) + 1
    data = np.zeros((height, width), dtype="float32")
    for r in range(height):
        for c in range(width):
            x, _ = to_utm.transform(west + (c + 0.5) * 100, north - (r + 0.5) * 100)
            data[r, c] = value_at(x - X0)
    tif = tmp_path / "ghsl.tif"
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="ESRI:54009",
        transform=from_origin(west, north, 100, 100),
        nodata=-200.0,
    ) as dst:
        dst.write(data, 1)
    archive = tmp_path / "ghsl.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(tif, "ghsl.tif")
    return archive


def test_nodata_is_unknown_not_zero(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from a2_data_pipeline.geo import assign_population

    # cell 0: 10 per pixel; cell 1: left half nodata (partial coverage); cell 2: a true zero
    def value(x: float) -> float:
        if x < 1000:
            return 10.0
        if x < 1500:
            return -200.0
        return 0.0 if x >= 2000 else 5.0

    grid = build_grid(layers([], []), utm_epsg=32634)
    assign_population(grid, write_ghsl(tmp_path, value), "ghsl.tif")
    first, second, third = (grid.cells[k] for k in sorted(grid.cells))
    assert first.pixels_valid == first.pixels_total > 0 and first.population > 0
    assert 0 < second.pixels_valid < second.pixels_total  # partial: the sum is not the cell's
    assert third.pixels_valid == third.pixels_total > 0 and third.population == 0  # a true zero
    assert grid.pixels_invalid == second.pixels_total - second.pixels_valid


def test_city_beyond_the_raster_is_unknown(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from a2_data_pipeline.geo import assign_population

    # the raster stops inside cell 2: its off-raster pixels are filled with nodata
    grid = build_grid(layers([], []), utm_epsg=32634)
    assign_population(grid, write_ghsl(tmp_path, lambda x: 1.0, cover_until_x=2300), "ghsl.tif")
    third = grid.cells[sorted(grid.cells)[2]]
    assert third.pixels_valid < third.pixels_total
    assert grid.pixels_invalid > 0


def test_population_cell_rejects_a_sum_over_nodata() -> None:
    from a2_data_pipeline.bundle import PopulationCell

    fields = {
        "cell_id": "c1",
        "city_id": "syn",
        "grid_id": "g",
        "ix": 0,
        "iy": 0,
        "geometry": {"type": "Point", "coordinates": [20.0, 54.0]},
        "square_bbox": (20.0, 54.0, 20.01, 54.01),
        "land_area_km2": 1.0,
        "population_method": "m",
        "population_year": 2025,
        "district_shares": [],
        "status": "sampled",
        "exclusion_reason": None,
        "evidence_ids": ["e1"],
        "raster_pixels_total": 100,
        "raster_pixels_valid": 60,
    }
    PopulationCell(**fields, population=None, population_status="unknown")
    with pytest.raises(ValueError, match="nodata"):
        PopulationCell(**fields, population=42.0, population_status="estimated")
