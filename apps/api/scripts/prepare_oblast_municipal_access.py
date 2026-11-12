"""Aggregate walking accessibility estimates by municipality with GHSL weights."""

import argparse
import json
import math
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import httpx
import numpy as np
import rasterio
from a2_api.adapters.address_access import Point, calculate_categories
from a2_api.db.repositories.address_catalog import (
    catalogue,
    catalogue_path,
    district_rows,
    metadata,
)
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape
from shapely.ops import transform, unary_union

CORE = ("hospital", "school", "kindergarten")


def population_tiles(paths, municipalities, localities):
    """Assign 100 m cell centres to the mapped boundaries in the GHSL CRS."""
    chunks = defaultdict(list)
    footprints = []
    for path in paths:
        with ZipFile(path) as archive:
            member = next(name for name in archive.namelist() if name.lower().endswith(".tif"))
        with rasterio.open(f"zip://{path.resolve()}!{member}") as raster:
            project = Transformer.from_crs("EPSG:4326", raster.crs, always_xy=True).transform
            footprint = box(*raster.bounds)
            footprints.append(footprint)
            boundaries = [
                transform(project, shape(json.loads(row["geometry"]))) for row in municipalities
            ]
            intersecting = [
                index for index, polygon in enumerate(boundaries) if polygon.intersects(footprint)
            ]
            if not intersecting:
                continue
            region = unary_union([boundaries[index] for index in intersecting]).intersection(
                footprint
            )
            bounds = from_bounds(*region.bounds, transform=raster.transform)
            left, top = math.floor(bounds.col_off), math.floor(bounds.row_off)
            right = math.ceil(bounds.col_off + bounds.width)
            bottom = math.ceil(bounds.row_off + bounds.height)
            requested = Window(left, top, right - left, bottom - top)
            window = Window(0, 0, raster.width, raster.height).intersection(requested)
            population = raster.read(1, window=window, masked=True)
            affine = raster.window_transform(window)
            municipality_ids = rasterize(
                [(boundaries[index], index + 1) for index in intersecting],
                out_shape=population.shape,
                transform=affine,
                dtype="uint16",
            )
            city_ids = rasterize(
                [
                    (transform(project, shape(row["geometry"])), index + 1)
                    for index, row in enumerate(localities)
                ],
                out_shape=population.shape,
                transform=affine,
                dtype="uint16",
            )
            valid = (population.filled(0) > 0) & (municipality_ids > 0)
            for index in intersecting:
                rows, cols = np.where(valid & (municipality_ids == index + 1))
                x, y = rasterio.transform.xy(affine, rows, cols)
                chunks[index].append(
                    np.column_stack((x, y, population.data[rows, cols], city_ids[rows, cols]))
                )
    union = unary_union(footprints)
    return chunks, [union.covers(polygon) for polygon in boundaries], raster.crs


def sample(pixels, sample_count):
    eligible = pixels[pixels[:, 3] > 0]
    if not len(eligible):
        return []
    eligible = eligible[np.lexsort((eligible[:, 0], eligible[:, 1]))]
    cumulative = np.cumsum(eligible[:, 2])
    count = min(sample_count, len(eligible))
    if count == len(eligible):
        return eligible
    weight = float(cumulative[-1]) / count
    indices = np.searchsorted(cumulative, (np.arange(count) + 0.5) * weight)
    return [
        (eligible[index, 0], eligible[index, 1], weight, eligible[index, 3]) for index in indices
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tiles", type=Path, nargs="+")
    parser.add_argument("--sample-count", type=int, default=16)
    parser.add_argument(
        "--municipality-id", action="append", help="Recalculate only these municipalities"
    )
    parser.add_argument("--router", default=os.getenv("A2_ADDRESS_ROUTER_URL"))
    args = parser.parse_args()
    if not args.router or args.sample_count < 1:
        parser.error("A router URL and positive sample count are required")

    folder = catalogue_path().parent
    localities = json.loads((folder / "localities.json").read_text())
    # Smaller nested city boundaries take precedence in the population raster.
    localities.sort(key=lambda row: (-shape(row["geometry"]).area, row["id"]))
    with catalogue() as db:
        catalogue_source = metadata(db, "source")
        municipalities = sorted(
            (row for row in district_rows(db) if row["id"].startswith("oblast-")),
            key=lambda row: row["id"],
        )
    if args.municipality_id:
        requested = set(args.municipality_id)
        municipalities = [row for row in municipalities if row["id"] in requested]
        if {row["id"] for row in municipalities} != requested:
            raise ValueError("One or more requested municipalities are absent")
    if not municipalities or not localities:
        raise ValueError("The catalogue must contain oblast municipalities and urban localities")
    chunks, raster_coverage, crs = population_tiles(args.tiles, municipalities, localities)
    unproject = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    plans = {}
    for index, row in enumerate(municipalities):
        pixels = np.concatenate(chunks[index]) if chunks[index] else np.empty((0, 4))
        plans[row["id"]] = (
            row["name"],
            float(pixels[:, 2].sum()),
            raster_coverage[index],
            list(sample(pixels, args.sample_count)),
        )
    print(
        f"{len(plans)} municipalities, {sum(len(plan[3]) for plan in plans.values())} weighted samples",
        flush=True,
    )

    def evaluate(task):
        x, y, locality = task
        lon, lat = unproject.transform(x, y)
        with catalogue() as db, httpx.Client(base_url=args.router, timeout=25) as client:
            return calculate_categories(
                db,
                client,
                Point(lat=lat, lon=lon),
                CORE,
                localities[int(locality) - 1]["h_seconds"],
            )

    tasks = dict.fromkeys(
        (x, y, city) for _, _, _, rows in plans.values() for x, y, _, city in rows
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = dict(zip(tasks, executor.map(evaluate, tasks), strict=True))

    path = folder / "districts.json"
    reports = json.loads(path.read_text()) if path.exists() else {}
    for district_id, (name, population, raster_complete, rows) in plans.items():
        numerators, covered = defaultdict(float), defaultdict(float)
        overall_numerator = overall_weight = 0.0
        for x, y, weight, city in rows:
            scores = [result for result in results[x, y, city] if result.index is not None]
            for score in scores:
                numerators[score.category] += weight * score.index
                covered[score.category] += weight
            if len(scores) == len(CORE):
                overall_numerator += weight * sum(score.index for score in scores) / len(CORE)
                overall_weight += weight
        boundary_sources = {
            localities[int(city) - 1]["id"]: localities[int(city) - 1]["source"]
            for _, _, _, city in rows
            if "source" in localities[int(city) - 1]
        }
        registry_ids = sorted(
            item["registry_id"] for item in boundary_sources.values() if "registry_id" in item
        )
        decisions = sorted(
            item["decision"] for item in boundary_sources.values() if "decision" in item
        )
        rgis_ids = sorted(
            f"{item['rgis_layer']}/{item['feature_id']}"
            for item in boundary_sources.values()
            if "rgis_layer" in item
        )
        source = f"GHSL GHS-POP R2023A E2025; {catalogue_source}"
        if registry_ids:
            source += "; ЕГРН " + ", ".join(registry_ids)
        if decisions:
            source += "; утверждённый генплан " + ", ".join(decisions)
        if rgis_ids:
            source += "; РГИС Московской области " + ", ".join(rgis_ids)
        reports[district_id] = {
            "district": {"id": district_id, "name": name},
            "index": overall_numerator / overall_weight
            if overall_weight and raster_complete
            else None,
            "categories": {
                category: numerators[category] / covered[category] if covered[category] else None
                for category in CORE
            },
            "population_covered": {
                category: min(covered[category] / population, 1) if population else 0
                for category in CORE
            },
            "overall_population_covered": min(overall_weight / population, 1) if population else 0,
            "sample_count": len({(x, y) for x, y, _, _ in rows}),
            "population": population,
            "source": source,
        }
    temporary = path.with_suffix(".municipal.tmp")
    temporary.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    temporary.replace(path)


if __name__ == "__main__":
    main()
