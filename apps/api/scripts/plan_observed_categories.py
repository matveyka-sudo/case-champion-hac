"""Bounded geographic candidate selection for existing real walking executors.

This is an observed-route plan, never a shortest-time or coverage certificate.
The complete snapshot is retained by the runtime, including unplanned points.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any


def distance(a: dict[str, float], b: dict[str, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a["lon"], a["lat"], b["lon"], b["lat"]))
    value = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(min(1, math.sqrt(value)))


def plan(
    endpoints: list[dict[str, Any]], audit: list[dict[str, Any]], categories: list[str]
) -> dict[str, Any]:
    matched = {row["entity_id"] for row in audit if row["state"] == "matched"}
    known = [
        row
        for row in endpoints
        if row["entity_id"] in matched
        and row["connector"] is not None
        and row["connector"]["traversable"] is True
        and row["connector"]["seconds"] is not None
    ]
    origins = sorted(
        (row for row in known if row["entity_kind"] == "point"),
        key=lambda row: row["entity_id"],
    )
    selected: set[tuple[str, str]] = set()
    counts = {}
    for category in sorted(set(categories)):
        destinations = [
            row
            for row in known
            if row["entity_kind"] == "destination" and category in row["categories"]
        ]
        counts[category] = {"known_targets": len(destinations), "planned_origins": 0}
        for point in origins:
            if not destinations:
                continue
            target = min(
                destinations,
                key=lambda row: (
                    distance(
                        point["connector"]["physical_location"],
                        row["connector"]["physical_location"],
                    ),
                    row["entity_id"],
                ),
            )
            selected.add((point["entity_id"], target["entity_id"]))
            counts[category]["planned_origins"] += 1
    if len(selected) > 32768:
        raise ValueError("The approved category catalog cannot exceed 32768 physical pairs")
    return {
        "schema_version": "waydex-observed-category-plan/1",
        "selection": "one_geographically_nearest_known_target_per_origin_and_category",
        "minimum_proven": False,
        "complete_catalog_search": False,
        "categories": counts,
        "pairs": [
            {"point_id": point, "destination_id": target} for point, target in sorted(selected)
        ],
        "unknown_points_must_remain_in_result": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoints", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--category", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = plan(
        json.loads(args.endpoints.read_bytes()), json.loads(args.audit.read_bytes()), args.category
    )
    with args.output.open("x") as target:
        json.dump(result, target, ensure_ascii=False, sort_keys=True)
        target.write("\n")


if __name__ == "__main__":
    main()
