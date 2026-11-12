"""Estimate Moscow district accessibility using walking times and GHSL cell weights."""

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from a2_api.adapters.address_access import (
    CATEGORIES,
    ROUTER,
    District,
    DistrictResult,
    Point,
    calculate_categories,
)
from a2_api.db.repositories.address_catalog import (
    catalogue,
    catalogue_path,
    district_rows,
    metadata,
)
from shapely.geometry import shape
from shapely.strtree import STRtree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--name", action="append", help="Exact district or okrug name")
    selection.add_argument(
        "--all", action="store_true", help="All Moscow territories in the catalogue"
    )
    args = parser.parse_args()
    cells = json.loads((args.bundle / "population_cells.json").read_text())
    points = {
        row["cell_id"]: row for row in json.loads((args.bundle / "sample_points.json").read_text())
    }
    geometries = [shape(cell["geometry"]) for cell in cells]
    spatial = STRtree(geometries)
    weights = {}
    required_cells = set()
    with catalogue() as db:
        h = float(metadata(db, "moscow_h_seconds"))
        districts = [row for row in district_rows(db) if not row["id"].startswith("oblast-")]
        source = metadata(db, "source")
    for district in districts:
        if not args.all and district["name"] not in args.name:
            continue
        boundary = shape(json.loads(district["geometry"]))
        allocated = {}
        unknown_population = False
        for index in spatial.query(boundary, predicate="intersects"):
            cell = cells[int(index)]
            cell_id = cell["cell_id"]
            intersection = boundary.intersection(geometries[int(index)]).area
            if intersection <= 0:
                continue
            if cell["population"] is None:
                unknown_population = True
                continue
            # For an okrug use published GHSL pixel shares. Smaller districts
            # allocate the available one-kilometre cell estimate by area.
            share = next(
                (row for row in cell["district_shares"] if row["territory_id"] == district["id"]),
                None,
            )
            if share is not None and share["population"] is None:
                unknown_population = True
                continue
            population = (
                share["population"]
                if share
                else cell["population"] * intersection / geometries[int(index)].area
            )
            if population > 0:
                allocated[cell_id] = population
                if cell_id in points:
                    required_cells.add(cell_id)
        weights[district["id"]] = (district["name"], allocated, unknown_population)
    if not args.all and len(weights) != len(set(args.name)):
        raise ValueError("One or more district names are absent or ambiguous")

    def calculate(cell_id):
        row = points[cell_id]
        with (
            catalogue() as db,
            httpx.Client(base_url=os.getenv("A2_ADDRESS_ROUTER_URL", ROUTER), timeout=15) as client,
        ):
            scores = calculate_categories(
                db, client, Point(lat=row["lat"], lon=row["lon"]), CATEGORIES, h
            )
        return cell_id, {score.category: score.index for score in scores if score.index is not None}

    print(f"Calculating {len(required_cells)} representative cells", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = dict(pool.map(calculate, sorted(required_cells)))
    path = catalogue_path().with_name("districts.json")
    reports = json.loads(path.read_text()) if path.exists() else {}
    for district_id, (name, allocated, unknown_population) in weights.items():
        numerator = defaultdict(float)
        covered = defaultdict(float)
        population = sum(allocated.values())
        for cell_id, weight in allocated.items():
            for category, score in values.get(cell_id, {}).items():
                numerator[category] += score * weight
                covered[category] += weight
        estimates = {
            category: numerator[category] / covered[category] if covered[category] else None
            for category in CATEGORIES
        }
        coverage = {
            category: min(covered[category] / population, 1) if population else 0
            for category in CATEGORIES
        }
        complete = not unknown_population and all(value >= 0.99999 for value in coverage.values())
        index = (
            sum(value for value in estimates.values() if value is not None) / len(CATEGORIES)
            if complete
            else None
        )
        reports[district_id] = DistrictResult(
            district=District(id=district_id, name=name),
            index=index,
            categories=estimates,
            population_covered=coverage,
            population=population,
            source=f"{source}; {args.bundle.name}; GHSL 2025",
        ).model_dump()
        print(f"{name}: population={population:.0f}, covered={coverage}", flush=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    temporary.replace(path)


if __name__ == "__main__":
    main()
