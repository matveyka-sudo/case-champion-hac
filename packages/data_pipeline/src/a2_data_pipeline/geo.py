"""City grid, representative sample points and population (PRODUCT_SPEC §2).

Rules implemented:
- 1000 m metric grid in the city's UTM zone, origin at UTM (0, 0), fixed version;
- a cell is the grid square clipped to the city boundary;
- at most one point per cell: the square's centre if it lies in a building,
  otherwise the nearest point of a building part inside the same clipped cell;
  ties go to the smaller stable building id; no transfer between cells;
- a cell without a building is excluded and its population is reported in QA;
- population is assigned to the original cell (not the chosen building) by
  GHSL pixel centroids; district shares use the same pixels, so they never
  exceed the cell total.

Requires ``shapely``, ``pyproj`` and ``rasterio`` (see README).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import rasterio  # type: ignore[import-untyped]
import shapely  # type: ignore[import-untyped]
from pyproj import Geod, Transformer
from rasterio.windows import from_bounds  # type: ignore[import-untyped]
from shapely.geometry import box, mapping  # type: ignore[import-untyped]
from shapely.ops import nearest_points  # type: ignore[import-untyped]

GEOD = Geod(ellps="WGS84")
GHSL_CRS = "ESRI:54009"
GHSL_NODATA_BELOW = 0.0
POPULATION_METHOD = "ghsl_r2023a_e2025_100m_pixel_centroid"
POPULATION_YEAR = 2025
# Conservative allowance for a few rounding steps in projection and overlay.
# This is a coordinate precision budget, not a minimum meaningful land area.
_EXCLUSION_OVERLAY_ULPS = 8


@dataclass
class OsmLayers:
    city: shapely.Geometry
    city_ref: str
    city_name: str
    city_population_tag: str | None
    districts: list[tuple[str, str, shapely.Geometry]]
    buildings: np.ndarray  # shapely geometries, EPSG:4326
    building_refs: list[str]
    water: np.ndarray


def _load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def load_osm_layers(
    osm_dir: Path, city_relation: int, district_relations: tuple[int, ...]
) -> OsmLayers:
    boundaries = {row["osm_ref"]: row for row in _load(osm_dir / "boundaries.parquet")}
    city_row = boundaries[f"relation/{city_relation}"]
    city = shapely.make_valid(shapely.from_wkb(city_row["wkb"]))
    districts = []
    for relation in district_relations:
        row = boundaries[f"relation/{relation}"]
        districts.append(
            (row["osm_ref"], row["name"], shapely.make_valid(shapely.from_wkb(row["wkb"])))
        )
    buildings = _load(osm_dir / "buildings.parquet")
    water = _load(osm_dir / "water.parquet")
    return OsmLayers(
        city=city,
        city_ref=city_row["osm_ref"],
        city_name=city_row["name"],
        city_population_tag=city_row["population_tag"],
        districts=districts,
        buildings=shapely.from_wkb([row["wkb"] for row in buildings]),
        building_refs=[row["osm_ref"] for row in buildings],
        water=shapely.from_wkb([row["wkb"] for row in water]),
    )


def geodesic_area_km2(geometry: shapely.Geometry) -> float:
    area, _ = GEOD.geometry_area_perimeter(geometry)
    return abs(area) / 1e6


def _projector(src: str, dst: str) -> Any:
    transformer = Transformer.from_crs(src, dst, always_xy=True)

    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return apply


def to_crs(geometries: Any, src: str, dst: str) -> Any:
    return shapely.transform(geometries, _projector(src, dst))


def building_order(ref: str) -> tuple[int, int]:
    kind, number = ref.split("/")
    return (0 if kind == "way" else 1, int(number))


def rounded_geojson(geometry: shapely.Geometry, digits: int = 6) -> dict[str, Any]:
    return dict(mapping(shapely.set_precision(geometry, 10**-digits)))


@dataclass
class CellResult:
    ix: int
    iy: int
    clipped: shapely.Geometry  # UTM
    land_area_km2: float
    population: float = 0.0  # sum of the valid pixels only
    district_population: dict[str, float] = field(default_factory=dict)
    pixels_total: int = 0  # pixel centroids in the cell, valid or not
    pixels_valid: int = 0
    point: shapely.Geometry | None = None  # UTM
    placement: str | None = None
    building_ref: str | None = None
    offset_m: float | None = None
    exclusion_reason: str | None = None


@dataclass
class GridResult:
    crs: str
    spacing_m: int
    cells: dict[tuple[int, int], CellResult]
    city_utm: shapely.Geometry
    districts_utm: list[tuple[str, str, shapely.Geometry]]
    population_total: float
    population_outside_cells: float
    pixel_count: int
    pixels_invalid: int = 0  # nodata or outside the raster: their cells are unknown


def _water_only_within_roundoff(clipped: shapely.Geometry, water: shapely.Geometry) -> bool:
    """Classify on a tiny precision grid without changing geometry or measured area.

    Coincident boundaries can differ by a few projected-coordinate ULPs after
    WGS84/UTM conversion and overlay. Eight ULPs allow those rounding steps;
    around northing 6,060,000 m the grid is 7.45 nm, not a geographic buffer.
    Fixed-precision overlay preserves resolvable strips and small land islands,
    unlike an area cutoff. land_area_km2 deliberately retains the original
    unquantized area, including a possible positive roundoff residual.
    """
    if water.is_empty:
        return False
    precision = _EXCLUSION_OVERLAY_ULPS * max(math.ulp(value) for value in clipped.bounds)
    return bool(shapely.difference(clipped, water, grid_size=precision).area <= 0)


def build_grid(layers: OsmLayers, utm_epsg: int, spacing_m: int = 1000) -> GridResult:
    crs = f"EPSG:{utm_epsg}"
    city_utm = shapely.make_valid(to_crs(layers.city, "EPSG:4326", crs))
    shapely.prepare(city_utm)
    districts_utm = [
        (ref, name, shapely.make_valid(to_crs(geom, "EPSG:4326", crs)))
        for ref, name, geom in layers.districts
    ]
    minx, miny, maxx, maxy = city_utm.bounds

    # Buildings and water intersecting the city, in UTM.
    city_4326 = layers.city
    shapely.prepare(city_4326)
    bmask = shapely.intersects(city_4326, layers.buildings)
    building_geoms = shapely.make_valid(to_crs(layers.buildings[bmask], "EPSG:4326", crs))
    building_refs = [ref for ref, keep in zip(layers.building_refs, bmask) if keep]
    wmask = shapely.intersects(city_4326, layers.water)
    water_utm = shapely.make_valid(to_crs(layers.water[wmask], "EPSG:4326", crs))
    water_union = shapely.union_all(water_utm) if len(water_utm) else shapely.Polygon()
    tree = shapely.STRtree(building_geoms)

    cells: dict[tuple[int, int], CellResult] = {}
    for ix in range(math.floor(minx / spacing_m), math.floor(maxx / spacing_m) + 1):
        for iy in range(math.floor(miny / spacing_m), math.floor(maxy / spacing_m) + 1):
            square = box(ix * spacing_m, iy * spacing_m, (ix + 1) * spacing_m, (iy + 1) * spacing_m)
            if not city_utm.intersects(square):
                continue
            clipped = shapely.intersection(city_utm, square)
            if clipped.area <= 0:
                continue
            land = shapely.difference(clipped, water_union)
            cell = CellResult(ix=ix, iy=iy, clipped=clipped, land_area_km2=land.area / 1e6)
            _place_point(cell, square.centroid, tree, building_geoms, building_refs)
            if cell.point is None:
                water_only = land.area <= 0 or _water_only_within_roundoff(clipped, water_union)
                cell.exclusion_reason = "WATER_ONLY" if water_only else "NO_BUILDING"
            cells[(ix, iy)] = cell
    return GridResult(
        crs=crs,
        spacing_m=spacing_m,
        cells=cells,
        city_utm=city_utm,
        districts_utm=districts_utm,
        population_total=0.0,
        population_outside_cells=0.0,
        pixel_count=0,
    )


def _place_point(
    cell: CellResult,
    center: shapely.Point,
    tree: shapely.STRtree,
    geoms: np.ndarray,
    refs: list[str],
) -> None:
    candidates = tree.query(cell.clipped, predicate="intersects")
    best: tuple[float, tuple[int, int], shapely.Point, str] | None = None
    for index in candidates:
        part = shapely.intersection(geoms[index], cell.clipped)
        if part.is_empty:
            continue
        ref = refs[index]
        if part.covers(center):
            distance, point = 0.0, center
        else:
            point = nearest_points(part, center)[0]
            distance = point.distance(center)
        key = (distance, building_order(ref))
        if best is None or key < (best[0], best[1]):
            best = (distance, building_order(ref), point, ref)
    if best is None:
        return
    distance, _, point, ref = best
    cell.point = point
    cell.offset_m = distance
    cell.building_ref = ref
    cell.placement = "cell_center" if distance == 0 else "nearest_building_point"


def assign_population(grid: GridResult, ghsl_zip: Path, tif_name: str) -> None:
    """Assign GHSL pixels to cells by pixel centroid; no redistribution of excluded cells.

    Coverage is kept apart from the sum: every pixel centroid inside the city counts
    for its cell (``pixels_total``), only valid pixels add population
    (``pixels_valid``). A pixel that is nodata or lies outside the raster (the window
    is read boundless, filled with nodata) makes its cell unknown, not zero.
    """

    to_ghsl = Transformer.from_crs(grid.crs, GHSL_CRS, always_xy=True)
    minx, miny, maxx, maxy = grid.city_utm.bounds
    xs, ys = to_ghsl.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
    margin = 500.0
    with rasterio.open(f"/vsizip/{ghsl_zip}/{tif_name}") as src:
        window = (
            from_bounds(
                min(xs) - margin,
                min(ys) - margin,
                max(xs) + margin,
                max(ys) + margin,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
        )
        nodata = src.nodata
        fill = nodata if nodata is not None else GHSL_NODATA_BELOW - 1
        values = src.read(1, window=window, boundless=True, fill_value=fill).astype("float64")
        transform = src.window_transform(window)
    rows, cols = np.nonzero(np.ones_like(values, dtype=bool))
    px = transform.c + (cols + 0.5) * transform.a
    py = transform.f + (rows + 0.5) * transform.e
    vals = values[rows, cols]
    valid = vals >= GHSL_NODATA_BELOW
    if nodata is not None:
        valid &= vals != nodata
    to_utm = Transformer.from_crs(GHSL_CRS, grid.crs, always_xy=True)
    ux, uy = to_utm.transform(px, py)
    inside = shapely.contains_xy(grid.city_utm, ux, uy)
    ux, uy, vals, valid = ux[inside], uy[inside], vals[inside], valid[inside]
    grid.pixel_count = int(valid.sum())
    grid.pixels_invalid = int((~valid).sum())
    grid.population_total = float(vals[valid].sum())
    ix = np.floor(ux / grid.spacing_m).astype(int)
    iy = np.floor(uy / grid.spacing_m).astype(int)
    district_masks = [
        (ref, shapely.contains_xy(geom, ux, uy)) for ref, _, geom in grid.districts_utm
    ]
    outside = 0.0
    for k in range(len(vals)):
        cell = grid.cells.get((int(ix[k]), int(iy[k])))
        if cell is None:
            if valid[k]:
                outside += float(vals[k])
            continue
        cell.pixels_total += 1
        if not valid[k]:
            continue
        cell.pixels_valid += 1
        cell.population += float(vals[k])
        for ref, mask in district_masks:
            if mask[k]:
                cell.district_population[ref] = cell.district_population.get(ref, 0.0) + float(
                    vals[k]
                )
    grid.population_outside_cells = outside
