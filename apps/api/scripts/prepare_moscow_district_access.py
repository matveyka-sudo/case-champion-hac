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
    DistrictDestination,
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
from shapely.geometry import Point as ShapePoint
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
        inside = set()
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
                point = points.get(cell_id)
                if point and boundary.covers(ShapePoint(point["lon"], point["lat"])):
                    inside.add(cell_id)
                    required_cells.add(cell_id)
        weights[district["id"]] = (district["name"], allocated, unknown_population, inside)
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
        return cell_id, [score for score in scores if score.index is not None]

    print(f"Calculating {len(required_cells)} representative cells", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = dict(pool.map(calculate, sorted(required_cells)))
    path = catalogue_path().with_name("districts.json")
    reports = json.loads(path.read_text()) if path.exists() else {}
    for district_id, (name, allocated, unknown_population, inside) in weights.items():
        numerator = defaultdict(float)
        covered = defaultdict(float)
        population = sum(allocated.values())
        destinations = {}
        for cell_id in sorted(inside):
            weight = allocated[cell_id]
            for row in values[cell_id]:
                assert row.index is not None
                numerator[row.category] += row.index * weight
                covered[row.category] += weight
                if row.destination is not None:
                    target = row.destination
                    destinations[row.category, target.id, target.line] = DistrictDestination(
                        **target.model_dump(), category=row.category
                    )
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
        samples = [
            Point(lat=points[cell_id]["lat"], lon=points[cell_id]["lon"])
            for cell_id in sorted(inside)
            if values[cell_id]
        ]
        reports[district_id] = DistrictResult(
            district=District(id=district_id, name=name),
            index=index,
            categories=estimates,
            population_covered=coverage,
            population=population,
            sample_count=len(samples),
            sample_points=samples,
            destinations=list(destinations.values()),
            source=f"{source}; {args.bundle.name}; GHSL 2025",
        ).model_dump()
        print(f"{name}: population={population:.0f}, covered={coverage}", flush=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    temporary.replace(path)


if __name__ == "__main__":
    main()
