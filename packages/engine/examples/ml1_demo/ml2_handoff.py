"""Read the external PR 34 bundle in place and exercise its normalized urban inputs."""

from collections import Counter
from csv import DictReader
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from json import loads
from math import isclose
from pathlib import Path
from typing import Literal

from a2_engine import (
    CarInputs,
    RoadBuild,
    RoadCoordinate,
    RoadEndpoint,
    Timetable,
    TimetableFootpath,
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanVersions,
    analyze_car_responses,
    load_urban_config,
    load_urban_journey_rules,
    plan_car_routes,
)
from a2_engine.scenario.engine import create_scenario_baseline
from a2_engine.scenario.models import ScenarioInputs, ScenarioTimetable, UrbanScenarioInput
from pydantic import TypeAdapter
from synthetic_car import synthetic_response

from ml1_demo import oracle
from ml1_demo.matrix import Row
from ml1_demo.ml2_scenarios import transit_control_run
from ml1_demo.model import CATEGORIES, CONFIG_PATH, config_hash

ML2_COMMIT = "0225db8ed1f7ffbd50d3fdba7e895339f3544ca7"
MANIFEST_SHA256 = "578c140cd8e82129fcec11ab8d9b41d0a8590225bb27f70b33f31d3c187a7cb6"
TABLE = TypeAdapter(Timetable)


def read_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as f:
        return tuple(DictReader(f))


def edge_times(
    rows: tuple[dict[str, str], ...],
    points: tuple[UrbanPoint, ...],
    destinations: tuple[UrbanDestination, ...],
) -> dict[tuple[str, str], float]:
    values = {(r["origin_point_id"], r["destination_id"]): float(r["time_s"]) for r in rows}
    if len(rows) != len(values) or set(values) != {
        (p.point_id, d.destination_id) for p in points for d in destinations
    }:
        raise ValueError("ML2_EDGE_ROSTER_MISMATCH")
    return values


def urban_baseline(
    root: Path,
    preset: Row,
    snapshots: dict[str, Row],
    controls: list[Row],
) -> Row:
    city = preset["city_id"]
    snapshot = snapshots[preset["snapshot_id"]]
    accepted = loads((root / snapshot["path"]).read_bytes())
    receipt = loads((root / snapshot["receipt"]).read_bytes())
    if receipt["snapshot_hash"] != snapshot["content_hash"]:
        raise ValueError("ML2_RECEIPT_CONTENT_HASH_MISMATCH")
    table = TABLE.validate_python(receipt["model"])
    directory = root / "tables" / city
    origins, objects = (
        read_rows(directory / "origins.csv"),
        read_rows(directory / "destinations.csv"),
    )
    if (
        len(origins) != 6
        or len(objects) != 18
        or Counter(category for r in objects for category in r["categories"].split("|"))
        != Counter({category: 2 for category in CATEGORIES})
    ):
        raise ValueError("ML2_URBAN_DIMENSIONS_MISMATCH")
    mode: Literal["walk", "private_car", "public_transport"] = preset["passenger_mode"]

    # Walk/car tables describe complete OD arcs. Transit receipt endpoints use
    # the existing model's access-node locations for the timetable connectors.
    def node(row: dict[str, str], key: str) -> str:
        return row["access_node_id"] if mode == "public_transport" else f"{key}:{row[key]}"

    proof = table.scope.evidence_refs
    points = tuple(
        UrbanPoint(
            r["point_id"],
            r["cell_id"],
            node(r, "point_id"),
            node(r, "point_id"),
            0,
            city,
            float(r["population_persons"]),
            int(r["population_year"]),
            "ml2_demo_population",
            proof,
        )
        for r in origins
    )
    destinations = tuple(
        UrbanDestination(
            r["destination_id"],
            r["object_identity"],
            frozenset(r["categories"].split("|")),
            node(r, "destination_id"),
            city,
            True,
            True,
            proof,
        )
        for r in objects
    )
    if set(preset["inputs"]["origin_point_ids"]) != {p.point_id for p in points} or set(
        preset["inputs"]["destination_ids"]
    ) != {d.destination_id for d in destinations}:
        raise ValueError("ML2_PRESET_ROSTER_MISMATCH")
    point_by_id = {p.point_id: p for p in points}
    object_by_id = {d.destination_id: d for d in destinations}
    cfg, rules = load_urban_config(CONFIG_PATH), load_urban_journey_rules(CONFIG_PATH)
    snapshot_id = table.scope.snapshot_id
    build_id = f"{snapshot_id}:static-model-road"
    versions = UrbanVersions(
        snapshot_id,
        table.scope.graph_version,
        table.scope.calendar_version,
        f"{snapshot_id}:population",
        accepted["territories"][0]["geometry"]["version"],
        cfg.methodology_version,
        config_hash(),
        f"static:{build_id}",
    )
    context = UrbanContext(
        city,
        city,
        datetime.fromisoformat(preset["departure_utc"]),
        "static",
        versions,
        preset["preset_id"],
    )
    population = UrbanPopulationCoverage(context, True, 0, proof, frozenset(point_by_id))
    area = UrbanArea(city, accepted["territories"][0]["area_km2"], versions.geometry_version, proof)
    # The published preset selects all categories but does not assign priorities.
    selected = dict.fromkeys(preset["categories"], "low")
    seconds: dict[tuple[str, str], float] = {}
    comparisons: dict[str, Row] = {}
    if mode == "private_car":
        seconds = edge_times(read_rows(directory / "car_edges.csv"), points, destinations)
        build = RoadBuild(
            snapshot_id,
            f"{snapshot_id}:car-edges",
            build_id,
            versions.graph_version,
            "ml2-static-model-road",
            "auto",
            '{"speed_types":["freeflow"]}',
            proof,
        )
        car = CarInputs(
            context,
            points,
            destinations,
            tuple(
                (
                    r["point_id"],
                    RoadEndpoint(
                        build,
                        node(r, "point_id"),
                        node(r, "point_id"),
                        RoadCoordinate(float(r["lon"]), float(r["lat"])),
                        r["point_id"],
                        "entry",
                        0,
                        proof,
                    ),
                )
                for r in origins
            ),
            tuple(
                (
                    r["destination_id"],
                    RoadEndpoint(
                        build,
                        node(r, "destination_id"),
                        node(r, "destination_id"),
                        RoadCoordinate(float(r["lon"]), float(r["lat"])),
                        r["destination_id"],
                        "exit",
                        0,
                        proof,
                    ),
                )
                for r in objects
            ),
            area,
            population,
            tuple((c, True, proof) for c in selected),
            preset["timezone"],
        )
        plan = plan_car_routes(car, categories=tuple(selected))
        result = analyze_car_responses(
            plan,
            tuple(
                synthetic_response(r, seconds[r.origin_id, r.destination_id]) for r in plan.requests
            ),
            selected_categories=selected,
            config=cfg,
            rules=rules,
        )
        analysis, states = result.analysis, None
    else:
        if mode == "walk":
            seconds = edge_times(read_rows(directory / "walk_edges.csv"), points, destinations)
            paths = tuple(
                TimetableFootpath(
                    f"{p}->{d}",
                    point_by_id[p].origin_node_id,
                    object_by_id[d].access_node_id,
                    value,
                    True,
                    proof,
                )
                for (p, d), value in seconds.items()
            )
            table = Timetable(
                replace(
                    table.scope,
                    node_ids=frozenset(n for f in paths for n in (f.from_node_id, f.to_node_id)),
                    footpath_version=f"{snapshot_id}:walk-edges",
                    end_at=context.requested_departure_at
                    + timedelta(seconds=max(seconds.values()) + 1),
                ),
                (),
                paths,
                (),
            )
        coverage = tuple(
            UrbanCoverage(
                UrbanSearchScope(context, p.point_id, c, mode, rules.max_total_wait_seconds),
                True,
                True,
                proof,
                frozenset(d.destination_id for d in destinations if c in d.categories),
            )
            for p in points
            for c in selected
        )
        ds = ScenarioTimetable(f"{snapshot_id}:network", table)
        job = UrbanScenarioInput(
            preset["preset_id"],
            ds.network_id,
            context,
            points,
            destinations,
            coverage,
            area,
            tuple(selected.items()),
            (mode,),
            population,
            cfg,
            rules,
        )
        baseline = create_scenario_baseline(ScenarioInputs(timetables=(ds,), urban=(job,)))
        result_urban = baseline.before.urban[0]
        for declaration in controls:
            control = loads((root / declaration["control"]).read_bytes())
            try:
                comparisons[declaration["comparison_id"]] = transit_control_run(baseline, control)
            except ValueError as exc:
                comparisons[declaration["comparison_id"]] = {
                    "replayed": False,
                    "engine_rejection": str(exc),
                }
        analysis = result_urban.analysis
        states = sum(r.search.expanded_states for r in result_urban.searches)
    group = analysis.modes[0]
    if seconds:
        for p in group.points:
            for c in p.categories:
                expected = min(
                    seconds[p.point.point_id, d.destination_id]
                    for d in destinations
                    if c.category in d.categories
                )
                if (
                    c.best is None
                    or c.best.journey.total_seconds != expected
                    or c.index.value is None
                    or analysis.h_seconds.value is None
                    or not isclose(c.index.value, oracle.index(expected, analysis.h_seconds.value))
                ):
                    raise ValueError(
                        f"ML2_DIRECT_EDGE_ORACLE_MISMATCH: {city} {mode} {p.point.point_id} {c.category} "
                        f"expected {expected}, got {c.best.journey.total_seconds if c.best else None}"
                    )
    return {
        "id": preset["preset_id"],
        "city": city,
        "mode": mode,
        "scope": "synthetic_ml2_slice",
        "status": analysis.status,
        "index_status": group.index.status,
        "index": group.index.value,
        "eligible_within_model": group.ranking_eligible,
        "expanded_states": states,
        "h_seconds": analysis.h_seconds.value,
        "population": sum(p.population for p in points if p.population is not None),
        "category_priorities": selected,
        "direct_arc_oracle": "verified" if seconds else None,
        "reachable_categories": sum(c.best is not None for p in group.points for c in p.categories),
        "limitations": analysis.limitations,
        "comparison_replays": comparisons,
        "categories": [
            {
                "point": p.point.point_id,
                "category": c.category,
                "time_s": c.best.journey.total_seconds if c.best else None,
                "status": c.index.status,
            }
            for p in group.points
            for c in p.categories
        ],
    }


def check_ml2_bundle(root: Path) -> Row:
    raw = (root / "manifest.json").read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != MANIFEST_SHA256:
        raise ValueError("UNPINNED_ML2_DEMO_MANIFEST")
    manifest = loads(raw)
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root.resolve())
            or sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"ML2_DEMO_FILE_HASH_MISMATCH: {name}")
    presets = {r["preset_id"]: r for r in manifest["presets"]}
    snapshots = {s["snapshot_id"]: s for s in manifest["snapshots"]}
    baseline = [
        urban_baseline(
            root,
            p,
            snapshots,
            [c for c in manifest["comparisons"] if c["preset_id"] == p["preset_id"]],
        )
        for p in presets.values()
        if p["analysis_kind"] == "urban_passenger"
    ]
    replays = {
        key: result for row in baseline for key, result in row.pop("comparison_replays").items()
    }
    return {
        "scope": "external_ml2_synthetic_bundle",
        "source_commit": ML2_COMMIT,
        "dataset_id": manifest["dataset_id"],
        "seed": manifest["generator"]["seed"],
        "manifest_sha256": digest,
        "verified_files": len(manifest["files"]),
        "declared_baselines": manifest["counts"]["base_presets"],
        "baseline_rows_replayed": baseline,
        "comparisons": [
            {
                "id": c["comparison_id"],
                "operation": c["operation_type"],
                "declared_status": c["contract_status"],
                "analysis_kind": presets[c["preset_id"]]["analysis_kind"],
                "control_path": c["control"],
                **replays.get(c["comparison_id"], {"replayed": False}),
            }
            for c in manifest["comparisons"]
        ],
    }
