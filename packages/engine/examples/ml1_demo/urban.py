"""Urban walks and transfers on a finite timetable, plus accepted static car arcs."""

from dataclasses import replace
from datetime import timedelta
from typing import Literal

from a2_engine import (
    CarInputs,
    RoadBuild,
    RoadCoordinate,
    RoadEndpoint,
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanDistrict,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    UrbanVersions,
    analyze_car_responses,
    load_urban_config,
    load_urban_journey_rules,
    plan_car_routes,
)
from a2_engine.routing.timetable_models import TimetableFootpath, TimetableRun, TimetableTransfer
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    ScenarioResourceLink,
    UrbanScenarioInput,
)
from a2_engine.scenario.overlay import execution_hash
from a2_engine.urban.car.models import CarAnalysisResult
from synthetic_car import synthetic_response

from ml1_demo.model import (
    CATEGORIES,
    CONFIG_PATH,
    DAY,
    PRIORITIES,
    PROOF,
    START,
    VERSION,
    City,
    clock,
    config_hash,
    dataset,
    identity,
    resource_proof,
    run,
)

TransitVariant = Literal["regular", "connectivity", "unknown"]


def urban_inputs(city: City, variant: TransitVariant = "regular") -> ScenarioInputs:
    cfg, rules = load_urban_config(CONFIG_PATH), load_urban_journey_rules(CONFIG_PATH)
    k = 60 * city.number
    snapshot = city.snapshot
    runs: tuple[TimetableRun, ...] = (
        run(snapshot, "line-1", (("S", 240), ("X", 1200 + k)), "bus"),
        run(snapshot, "line-2-early", (("X", 1320 + k), ("D", 1920 + k)), "bus"),
        run(snapshot, "line-2-backup", (("X", 2040 + k), ("D", 2640 + k)), "bus"),
        run(snapshot, "line-2-previous-duty", (("X", -1200), ("D", -600)), "bus"),
        run(snapshot, "line-2-next-duty", (("X", 4200), ("D", 4800)), "bus"),
    )
    stops = tuple(f"O{i}" for i in range(6))
    points = tuple(
        UrbanPoint(node, f"cell:{node}", node, node, 0, city.id, population, 2026, VERSION, PROOF)
        for node, population in zip(stops, city.population, strict=True)
    )
    destinations = tuple(
        UrbanDestination(
            f"d{j:02d}",
            f"object:{j:02d}",
            frozenset({category}),
            f"D{j:02d}",
            city.id,
            True,
            True,
            PROOF,
        )
        for j, category in enumerate(category for category in CATEGORIES for _ in range(2))
    )
    paths = (
        tuple(
            TimetableFootpath(
                f"access:{i}", node, "S" if i < 3 else "X", 60 * (1 + i % 3), True, PROOF
            )
            for i, node in enumerate(stops)
        )
        + tuple(
            TimetableFootpath(f"walk:{i}", node, "D", 3600 + 120 * i + k, True, PROOF)
            for i, node in enumerate(stops)
        )
        + tuple(
            TimetableFootpath(
                f"object:{j}", "D", d.access_node_id, 30 * (j // 2) + 90 * (j % 2), True, PROOF
            )
            for j, d in enumerate(destinations)
        )
    )
    transfers: tuple[TimetableTransfer, ...] = (
        TimetableTransfer("transfer-X", "X", "X", 240, True, PROOF),
    )
    if variant != "regular":
        # Only the feeder points rely on the shifted trip. Neighbours connect via the
        # early bus and a downstream departure that a shifted bus will miss.
        runs = runs[:2] + (run(snapshot, "downstream", (("D", 2100 + k), ("Z", 2700 + k)), "bus"),)
        paths = tuple(
            replace(p, from_node_id="Z") if p.path_id.startswith("object:") else p
            for p in paths
            if not p.path_id.startswith("walk:")
        )
        # The early bus gives O3–O5 the only viable path to a pair of isolated
        # objects, whereas O0–O2 gain a path to the ordinary D-side objects.
        paths = tuple(
            replace(p, from_node_id="D")
            if p.path_id not in ("object:0", "object:1") and p.path_id.startswith("object:")
            else p
            for p in paths
        )
        transfers += (TimetableTransfer("transfer-D", "D", "D", 120, True, PROOF),)
    ds = dataset(snapshot, runs, transfers, paths, mode="road")
    if variant == "unknown":
        ds = replace(
            ds,
            timetable=replace(
                ds.timetable,
                scope=replace(
                    ds.timetable.scope,
                    schedule_complete=False,
                ),
            ),
        )
    versions = UrbanVersions(
        snapshot,
        ds.timetable.scope.graph_version,
        ds.timetable.scope.calendar_version,
        f"{snapshot}:population",
        f"{snapshot}:geometry",
        cfg.methodology_version,
        config_hash(),
        f"static:{snapshot}:roads",
    )
    context = UrbanContext(city.id, city.id, START, "static", versions, f"{VERSION}:search")
    modes = ("walk", "public_transport") if variant == "regular" else ("public_transport",)
    coverage = tuple(
        UrbanCoverage(
            UrbanSearchScope(context, p.point_id, cat, mode, rules.max_total_wait_seconds),
            True,
            True,
            PROOF,
            frozenset(d.destination_id for d in destinations if cat in d.categories),
        )
        for p in points
        for cat in CATEGORIES
        for mode in modes
    )
    cells = tuple(
        UrbanOriginalCell(
            p.cell_id, city.id, "grid", p.population, 2026, VERSION, p.point_id, False, PROOF
        )
        for p in points
    )
    districts = tuple(
        UrbanDistrict(name, city.id, name, f"{snapshot}:{name}:boundary", PROOF)
        for name in ("west", "east")
    )
    urban = UrbanScenarioInput(
        f"{snapshot}:urban",
        ds.network_id,
        context,
        points,
        destinations,
        coverage,
        UrbanArea(city.id, city.area, versions.geometry_version, PROOF),
        tuple(PRIORITIES.items()),
        modes,
        UrbanPopulationCoverage(context, True, 0, PROOF, frozenset(stops)),
        cfg,
        rules,
        thresholds=tuple(
            UrbanThreshold(
                city.id, mode, versions, "static", context.search_parameters_id, 2800, PROOF
            )
            for mode in modes
        ),
        original_cells=cells,
        districts=districts,
        district_shares=tuple(
            UrbanDistrictShare(p.cell_id, districts[i // 3].district_id, city.population[i], PROOF)
            for i, p in enumerate(points)
        ),
        district_coverage=UrbanDistrictCoverage(
            context,
            f"{snapshot}:districts",
            f"{snapshot}:distribution",
            frozenset({"grid"}),
            {d.district_id: d.boundary_version for d in districts},
            frozenset(d.district_id for d in districts),
            frozenset(p.cell_id for p in points),
            frozenset(d.district_id for d in districts),
            True,
            PROOF,
        ),
    )
    return ScenarioInputs(timetables=(ds,), urban=(urban,))


def urban_operation(
    baseline: ScenarioBaseline,
    template: Literal["coordinate_transfer", "additional_departure"],
    *,
    conflict: bool = False,
) -> tuple[ScenarioOperation, ScenarioFeasibilityInputs | None]:
    ds = baseline.inputs.timetables[0]
    runs = {r.trip_id: r for r in ds.timetable.runs}
    extra = template == "additional_departure"
    source = runs["line-2-backup" if extra else "line-2-early"]
    shift = -480 if extra else 240
    new = (
        replace(
            source,
            trip_id="line-2-added",
            events=tuple(
                replace(
                    e,
                    arrival_at=e.arrival_at + timedelta(seconds=shift),
                    departure_at=e.departure_at + timedelta(seconds=shift),
                )
                for e in source.events
            ),
        )
        if extra
        else None
    )
    operation = ScenarioOperation(
        baseline.baseline_id,
        f"{ds.network_id}:{template}",
        template,
        "timetable",
        ds.network_id,
        source.run_id,
        DAY,
        "0",
        execution_hash(baseline, "timetable", ds.network_id, source.run_id),
        shift,
        runs["line-1"].run_id,
        "1",
        PROOF,
        new_run=new,
        new_physical_service=identity(new) if new is not None else None,
        demand_evidence_refs=(f"{VERSION}:feeder-population-demand",) if extra else (),
    )
    if new is None:
        return operation, None
    previous = runs["line-2-early" if conflict else "line-2-previous-duty"].run_id
    following = runs["line-2-next-duty"].run_id
    block = ScenarioResourceBlock(
        "spare-bus-1",
        ds.network_id,
        (previous, new.run_id, following),
        clock(-3600),
        clock(6000),
        (
            ScenarioResourceLink(previous, new.run_id, 480, PROOF),
            ScenarioResourceLink(new.run_id, following, 480, PROOF),
        ),
        PROOF,
        480,
        480,
    )
    return operation, resource_proof(baseline, operation, block, (new.run_id,))


def analyze_car(job: UrbanScenarioInput, city: City) -> CarAnalysisResult:
    """The model road graph has one directed network arc per OD pair."""
    versions = job.context.versions
    build = RoadBuild(
        versions.snapshot_id,
        f"{city.snapshot}:road-source",
        f"{city.snapshot}:roads",
        versions.graph_version,
        f"{VERSION}:auto",
        "auto",
        '{"speed_types":["freeflow"]}',
        PROOF,
    )
    inputs = CarInputs(
        job.context,
        job.points,
        job.destinations,
        tuple(
            (
                p.point_id,
                RoadEndpoint(
                    build,
                    p.origin_node_id,
                    p.access_node_id,
                    RoadCoordinate(i * 0.01, 0),
                    f"entry:{i}",
                    "entry",
                    60,
                    PROOF,
                ),
            )
            for i, p in enumerate(job.points)
        ),
        tuple(
            (
                d.destination_id,
                RoadEndpoint(
                    build,
                    d.access_node_id,
                    d.access_node_id,
                    RoadCoordinate(j * 0.01, 1),
                    f"exit:{j}",
                    "exit",
                    30,
                    PROOF,
                ),
            )
            for j, d in enumerate(job.destinations)
        ),
        job.area,
        job.population_coverage,
        tuple((c, True, PROOF) for c in CATEGORIES),
        "UTC",
    )
    plan = plan_car_routes(inputs, categories=CATEGORIES)
    # Endpoints occupy two disjoint layers and no edge leaves an exit. Each
    # response replays the only network arc between its declared endpoints.
    responses = tuple(
        synthetic_response(
            r,
            540 + 60 * city.number + 15 * int(r.origin_id[1:]) + 30 * int(r.destination_id[1:]),
        )
        for r in plan.requests
    )
    return analyze_car_responses(
        plan,
        responses,
        selected_categories=PRIORITIES,
        config=job.config,
        rules=job.rules,
    )
