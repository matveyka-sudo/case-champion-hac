"""Assemble a compact accepted runtime using the two November Waydex handoffs."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from itertools import islice
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import httpx
from a2_api.adapters.product_inputs import (
    EngineInputs,
    InputDocument,
    IntercityInputs,
    UrbanCargoInputs,
    UrbanInputs,
)
from a2_api.adapters.product_runtime import (
    FileRef,
    Manifest,
    Profile,
    SnapshotRef,
    input_fingerprints,
    validate_inputs,
)
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import LonLat
from a2_contracts.v2.dataset import AcceptedData, AccessNode, Destination, Evidence
from a2_data_pipeline.compact import (
    ZONES,
    compact_snapshot,
    distance,
    gtfs_lineage,
    nearest,
    passenger_selection,
)
from a2_data_pipeline.gtfs_inputs import normalize_gtfs
from a2_data_pipeline.store import load_bundle
from a2_data_pipeline.walk_projection import project_walk_connectors
from a2_engine import (
    Timetable,
    TimetableFootpath,
    TimetableTransfer,
    UrbanArea,
    UrbanCandidate,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLeg,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanVersions,
    load_urban_config,
    load_urban_journey_rules,
)
from compact_kudikina import FIRST_DAY as KUDIKINA_FIRST_DAY
from compact_kudikina import NEXT_DAY as KUDIKINA_NEXT_DAY
from compact_kudikina import load_kudikina
from pydantic import HttpUrl

ROUTES = ("141500134", "4349")
START, END = date(2026, 11, 10), date(2026, 11, 13)
ACTIVE_START, ACTIVE_END = date(2026, 11, 11), date(2026, 11, 13)
WALK_SPEED = 5.0


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported input type: {type(value).__name__}")


def write(root: Path, filename: str, payload: Any) -> dict[str, str]:
    content = (
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=encode,
        )
        + "\n"
    ).encode()
    (root / filename).write_bytes(content)
    return {"path": filename, "sha256": hashlib.sha256(content).hexdigest()}


def load(path: Path) -> Any:
    return json.loads(path.read_bytes())


class RoadGraph:
    def __init__(self, client: httpx.Client, profile: Profile, graph_dir: Path) -> None:
        self.client = client
        binding = profile.truck_routing
        assert binding is not None
        self.endpoint = str(binding.endpoint_url).rstrip("/")
        manifest = graph_dir / "valhalla-build-manifest.json"
        digest = sha(manifest)
        if digest != binding.graph_manifest_sha256:
            raise ValueError("GRAPH_MANIFEST_MISMATCH")
        status = client.get(self.endpoint + "/status")
        status.raise_for_status()
        if (status.json()["version"], status.json()["tileset_last_modified"]) != (
            binding.engine_version,
            binding.tileset_last_modified,
        ):
            raise ValueError("VALHALLA_RUNTIME_GRAPH_MISMATCH")
        self.evidence = Evidence.model_validate(
            {
                "evidence_id": "compact-static-valhalla-" + digest,
                "source_id": "valhalla-static-" + digest,
                "source_version": binding.engine_version + ":freeflow",
                "url": "https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/",
                "retrieved_at": load(manifest)["finished_at"],
                "content_hash": digest,
                "valid_period": {"start": None, "end": None},
                "license": "OpenStreetMap ODbL 1.0; Valhalla MIT",
                "verified": True,
            }
        )

    def route(self, mode: str, origin: LonLat, target: LonLat) -> float | None:
        if origin == target:
            return 0.0
        options: dict[str, Any] = {"speed_types": ["freeflow"]}
        if mode == "pedestrian":
            options["walking_speed"] = WALK_SPEED
        response = self.client.post(
            self.endpoint + "/route",
            json={
                "locations": [origin.model_dump(), target.model_dump()],
                "costing": mode,
                "costing_options": {mode: options},
                "units": "kilometers",
            },
        )
        if response.status_code == 400:
            # An unlocated endpoint or no path in the transferred tiles is unknown.
            return None
        response.raise_for_status()
        summary = response.json()["trip"]["summary"]
        seconds = float(summary["time"])
        return seconds if isfinite(seconds) and seconds >= 0 else None

    def collect(
        self, tasks: dict[tuple[str, str, str], tuple[LonLat, LonLat]]
    ) -> dict[tuple[str, str, str], float]:
        def run(key: tuple[str, str, str]) -> float | None:
            return self.route(key[0], *tasks[key])

        with ThreadPoolExecutor(max_workers=6) as pool:
            return {
                key: seconds
                for key, seconds in zip(tasks, pool.map(run, tasks), strict=True)
                if seconds is not None
            }


def clocks(snapshot: AcceptedData, start: date, end: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(snapshot.timezone)
    return datetime.combine(start, time.min, zone), datetime.combine(end, time.min, zone)


def make_urban(
    snapshot: AcceptedData,
    mode: str,
    edges: dict[tuple[str, str, str], float],
    connectors: dict[str, dict[str, Any]],
    start: datetime,
    evidence_id: str,
    categories: dict[str, list[str]],
    timetable: Timetable | None = None,
) -> UrbanInputs:
    city = snapshot.territories[0]
    context = UrbanContext(
        city.territory_id,
        city.territory_id,
        start,
        "static",
        UrbanVersions(
            snapshot_id=snapshot.snapshot_id,
            graph_version=snapshot.provenance.graph_version,
            calendar_version=snapshot.provenance.calendar_version,
            population_version=snapshot.provenance.population_version,
            geometry_version=city.geometry.version,
            traffic_version="static:valhalla-freeflow",
            methodology_version=ProductRegistry().methodology.version,
            config_hash=ProductRegistry().config_hash,
        ),
        "selected-original-cells-v1",
    )
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    points = tuple(
        UrbanPoint(
            point_id=p.point_id,
            cell_id=p.cell_id,
            territory_id=p.territory_id,
            origin_node_id="physical:point:" + p.point_id,
            access_node_id=p.access_node_id,
            access_distance_m=p.access_distance_m,
            population=cells[p.cell_id].population,
            population_year=cells[p.cell_id].population_year,
            population_method=cells[p.cell_id].population_method,
            source_refs=tuple(sorted(set(p.source_refs + cells[p.cell_id].source_refs))),
        )
        for p in snapshot.sample_points
    )
    destinations = tuple(
        UrbanDestination(
            destination_id=d.destination_id,
            object_identity=d.object_identity,
            categories=frozenset(d.categories),
            access_node_id=d.access_node_id,
            territory_id=d.territory_id,
            eligible=True,
            in_service_scope=True,
            source_refs=tuple(d.source_refs),
            physical_node_id="physical:destination:" + d.destination_id,
        )
        for d in snapshot.destinations
    )
    area = UrbanArea(
        city.territory_id, city.area_km2, city.geometry.version, tuple(city.source_refs)
    )
    # Runtime checks excluded weight against the derivative snapshot. The parent
    # count/weight are also recorded in the separate selection receipt.
    population = UrbanPopulationCoverage(
        context, False, 0, tuple(city.source_refs), frozenset(p.point_id for p in points)
    )
    coverage = tuple(
        UrbanCoverage(
            UrbanSearchScope(
                context,
                point.point_id,
                category,
                mode,
                load_urban_journey_rules().max_total_wait_seconds,
            ),
            False,
            False,
            (evidence_id,),
            frozenset(ids),
        )
        for point in points
        for category, ids in categories.items()
        if ids
    )
    candidates = []
    if timetable is None:
        for p in points:
            for d in destinations:
                origin, target = p.origin_node_id, d.physical_node_id
                assert target is not None
                seconds = (
                    edges.get(("pedestrian", p.access_node_id, d.access_node_id))
                    if mode == "walk"
                    else edges.get(("auto", p.access_node_id, d.access_node_id))
                )
                if seconds is None:
                    continue
                left, right = connectors.get(origin), connectors.get(target)
                if not left or not right or left["seconds"] is None or right["seconds"] is None:
                    continue
                rows = [
                    (origin, p.access_node_id, left["seconds"], "walk"),
                    (p.access_node_id, d.access_node_id, seconds, mode),
                    (d.access_node_id, target, right["seconds"], "walk"),
                ]
                evidence = {
                    evidence_id,
                    *p.source_refs,
                    *d.source_refs,
                    *left["evidence_refs"],
                    *right["evidence_refs"],
                }
                refs = tuple(sorted(evidence))
                legs = []
                current = start
                for source, end, duration, leg_mode in rows:
                    arrival = current + timedelta(seconds=duration)
                    legs.append(
                        UrbanLeg(
                            leg_mode,
                            "walk" if leg_mode == "walk" else "ride",
                            source,
                            end,
                            current,
                            arrival,
                            duration,
                            None,
                            refs,
                        )
                    )
                    current = arrival
                for category in sorted(d.categories):
                    candidates.append(
                        UrbanCandidate(
                            canonical_hash(
                                [snapshot.snapshot_id, mode, p.point_id, d.destination_id, category]
                            ),
                            p.point_id,
                            d.destination_id,
                            category,
                            mode,
                            context,
                            origin,
                            target,
                            start,
                            current,
                            tuple(legs),
                            refs,
                        )
                    )
    return UrbanInputs(
        kind="urban_passenger",
        context=context,
        points=points,
        destinations=destinations,
        candidates=tuple(candidates),
        coverage=coverage,
        area=area,
        population_coverage=population,
        timetable=timetable,
        timetable_service_clock="gtfs_noon_elapsed_v1" if timetable else None,
        resource_limit=20000 if timetable else None,
    )


def add_graph_evidence(
    snapshot: AcceptedData,
    graph: RoadGraph,
    parent: AcceptedData,
    connectors: dict[str, dict[str, Any]],
) -> AcceptedData:
    existing = {row.evidence_id for row in snapshot.evidence}
    references = {ref for row in connectors.values() for ref in row["evidence_refs"]}
    extra = [row for row in parent.evidence if row.evidence_id in references - existing]
    return AcceptedData.model_validate(
        {
            **snapshot.model_dump(),
            "evidence": [*snapshot.evidence, *extra, graph.evidence],
            "source_versions": {
                **snapshot.source_versions,
                graph.evidence.source_id: graph.evidence.source_version,
            },
        }
    )


def routed_destinations(
    parent: AcceptedData,
    city: str,
    points: list[str],
    categories: dict[str, list[str]],
    traversable: set[str],
    graph: RoadGraph,
) -> tuple[
    dict[str, list[str]],
    dict[tuple[str, str, str], float],
    dict[str, Any],
]:
    nodes = {node.node_id: node.location for node in parent.access_nodes}
    starts = {p.access_node_id for p in parent.sample_points if p.point_id in points}
    selected: dict[str, list[str]] = {}
    edges: dict[tuple[str, str, str], float] = {}
    report: dict[str, Any] = {}
    for category in categories:
        accepted = [d for d in parent.destinations if category in d.categories]
        ready = [d for d in accepted if "physical:destination:" + d.destination_id in traversable]
        ranked = iter(nearest(ready, "destination_id", city, len(ready)))
        picked: list[str] = []
        rejected = {}
        while len(picked) < 5:
            batch = list(islice(ranked, 5 - len(picked)))
            if not batch:
                break
            required = {}
            for d in batch:
                target = d.access_node_id
                required[d.destination_id] = {
                    key
                    for start in starts
                    for key in (("pedestrian", start, target), ("auto", start, target))
                }
            routes = graph.collect(
                {
                    key: (nodes[key[1]], nodes[key[2]])
                    for key in sorted(set().union(*required.values()))
                }
            )
            for d in batch:
                keys = required[d.destination_id]
                missing = keys - routes.keys()
                if missing:
                    rejected[d.destination_id] = {
                        mode: sum(key[0] == mode for key in missing)
                        for mode in ("pedestrian", "auto")
                    }
                    continue
                picked.append(d.destination_id)
                edges.update({key: routes[key] for key in sorted(keys)})
        selected[category] = picked
        report[category] = {
            "accepted_objects": len(accepted),
            "confirmed_walk_connectors": len(ready),
            "rejected_missing_routes": rejected,
        }
    return selected, edges, report


def add_tonnelnaya(
    parent: AcceptedData, connectors: dict[str, dict[str, Any]], graph_evidence_id: str
) -> AcceptedData:
    """Bind the passenger station to its documented entrance from the settlement."""
    station_id = "novorossiysk-rail_terminal-node-10671626478"
    if any(destination.destination_id == station_id for destination in parent.destinations):
        return parent
    official = Evidence(
        evidence_id="dzvr:tonnelnaya:2026-11-12",
        source_id="dzvr-tonnelnaya",
        source_version="2026-11-12",
        url=HttpUrl("https://tonnelnaya.dzvr.ru/"),
        retrieved_at=datetime.fromisoformat("2026-11-12T06:07:25+00:00"),
        valid_period=parent.valid_period,
        license="Public factual station information",
        content_hash="a413a08233171ccde36b70927f7be9700396e4f8e054e99a8890d70cd4d8a60f",
        verified=True,
    )
    refs = [official.evidence_id, "osm:dataset:osm_geofabrik.south-fed-district-261107"]
    # OSM footway 588439254 starts at the mapped street junction and ends
    # on the settlement side of station building 336652360. Approximate the
    # unmarked door by projecting the end of the footway onto that facade.
    street = LonLat(lon=37.6539446, lat=44.838153)
    footway_end = LonLat(lon=37.6541874, lat=44.8379255)
    entrance = LonLat(lon=37.6543066, lat=44.8378205)
    access_id = "osm:node/5620100101"
    city = "novorossiysk"
    access = AccessNode(
        node_id=access_id, kind="road", territory_id=city, location=street, source_refs=refs
    )
    destination = Destination(
        destination_id=station_id,
        object_identity="osm:node/10671626478",
        categories=["rail_terminal"],
        access_node_id=access_id,
        territory_id=city,
        location=entrance,
        source_refs=refs,
    )
    length = distance(street, (footway_end.lon, footway_end.lat)) + distance(
        footway_end, (entrance.lon, entrance.lat)
    )
    connectors["physical:destination:" + station_id] = {
        "access_node_id": access_id,
        "seconds": round(length / (WALK_SPEED / 3.6), 1),
        "evidence_refs": [*refs, graph_evidence_id],
    }
    return AcceptedData.model_validate(
        {
            **parent.model_dump(),
            "access_nodes": [*parent.access_nodes, access],
            "destinations": [*parent.destinations, destination],
            "evidence": [*parent.evidence, official],
            "source_versions": {
                **parent.source_versions,
                official.source_id: official.source_version,
            },
        }
    )


def prepare_passenger(
    city: str,
    parent: AcceptedData,
    graph: RoadGraph,
    gtfs: Path | None,
    previous_transit: UrbanInputs | None,
    kudikina_capture: Path | None,
) -> tuple[list[tuple[AcceptedData, UrbanInputs, datetime, datetime]], dict[str, Any]]:
    bundle = load_bundle(parent.snapshot_id)
    graph_id = graph.evidence.evidence_id
    connectors = {
        row["physical_node_id"]: row
        for row in project_walk_connectors(
            bundle,
            walking_speed_kmh=WALK_SPEED,
            speed_evidence_refs=[graph_id],
            profile_version="valhalla-pedestrian-5kmh",
        )
    }
    if city == "novorossiysk":
        parent = add_tonnelnaya(parent, connectors, graph_id)
    traversable = {identity for identity, row in connectors.items() if row["seconds"] is not None}
    points, categories = passenger_selection(
        parent, city, traversable, load_urban_config().categories
    )
    categories, edges, routing_selection = routed_destinations(
        parent, city, points, categories, traversable, graph
    )
    subset = compact_snapshot(parent, point_ids=points, destination_ids=categories)
    physical = {
        **{"physical:point:" + p.point_id: p.location for p in subset.sample_points},
        **{"physical:destination:" + d.destination_id: d.location for d in subset.destinations},
    }
    connectors = {key: row for key, row in connectors.items() if key in physical}
    subset = add_graph_evidence(subset, graph, parent, connectors)
    nodes = {node.node_id: node.location for node in subset.access_nodes}
    pairs = {}
    for p in subset.sample_points:
        for d in subset.destinations:
            a, b = p.access_node_id, d.access_node_id
            pairs["pedestrian", a, b] = nodes[a], nodes[b]
            if city == "moscow" or kudikina_capture is not None:
                pairs["pedestrian", b, a] = nodes[b], nodes[a]
            pairs["auto", a, b] = nodes[a], nodes[b]
    edges.update(graph.collect({key: pair for key, pair in pairs.items() if key not in edges}))
    first_day, last_day = subset.valid_period.start, subset.valid_period.end
    if first_day is None or last_day is None:
        raise ValueError("COMPACT_PARENT_VALIDITY_REQUIRED")
    start, end = clocks(subset, first_day, last_day + timedelta(days=1))
    result = [
        (
            subset,
            make_urban(subset, mode, edges, connectors, start, graph_id, categories),
            start,
            end,
        )
        for mode in ("walk", "private_car")
    ]
    report = {
        "parent": parent.snapshot_id,
        "zone_anchor_lon_lat": ZONES[city],
        "sample_points": [p.model_dump(mode="json") for p in subset.sample_points],
        "original_cells": [c.model_dump(mode="json") for c in subset.population_cells],
        "parent_point_count": len(parent.sample_points),
        "parent_cell_count": len(parent.population_cells),
        "population_source_versions": parent.source_versions,
        "destinations": categories,
        "destination_routing_selection": routing_selection,
        "graph_sha256": graph.evidence.content_hash,
        "road_edges": len(edges),
        "road_edge_queries": len(pairs),
        "city_complete": False,
    }
    if city != "moscow" and kudikina_capture is not None:
        source_snapshot, table, source_report = load_kudikina(kudikina_capture, city, parent)
        transit_snapshot = compact_snapshot(
            source_snapshot, point_ids=points, destination_ids=categories
        )
        transit_snapshot = add_graph_evidence(transit_snapshot, graph, parent, connectors)
        transit, policy = build_transit(
            transit_snapshot, table, graph, edges, connectors, physical, None
        )
        start, end = clocks(transit_snapshot, KUDIKINA_FIRST_DAY, KUDIKINA_NEXT_DAY)
        result.append(
            (
                transit_snapshot,
                make_urban(
                    transit_snapshot,
                    "public_transport",
                    {},
                    connectors,
                    start,
                    graph_id,
                    categories,
                    transit,
                ),
                start,
                end,
            )
        )
        report["kudikina"] = {
            **source_report,
            **policy,
            "snapshot_id": transit_snapshot.snapshot_id,
        }
    if city == "moscow":
        if gtfs is None or not gtfs.is_file() or not bundle.manifest.timetables:
            report["gtfs_unavailable"] = "ACCEPTED_GTFS_ARCHIVE_OR_REFERENCE_MISSING"
            return result, report
        ref = bundle.manifest.timetables[0]
        try:
            normalized = normalize_gtfs(bundle, ref, gtfs, route_ids=ROUTES, start=START, end=END)
        except (OSError, ValueError):
            report["gtfs_unavailable"] = "GTFS_HASH_ROUTE_OR_CALENDAR_VALIDATION_FAILED"
            return result, report
        if normalized.report["excluded_trips"]:
            report["gtfs_unavailable"] = "GTFS_SELECTION_HAS_INCOMPLETE_TRIPS"
            return result, report
        transit_snapshot = compact_snapshot(
            normalized.accepted_data, point_ids=points, destination_ids=categories
        )
        transit_snapshot = add_graph_evidence(transit_snapshot, graph, parent, connectors)
        prior = previous_transit.timetable if previous_transit else None
        transit, policy = build_transit(
            transit_snapshot,
            normalized.timetable,
            graph,
            edges,
            connectors,
            physical,
            prior,
        )
        start, end = clocks(transit_snapshot, ACTIVE_START, ACTIVE_END)
        result.append(
            (
                transit_snapshot,
                make_urban(
                    transit_snapshot,
                    "public_transport",
                    {},
                    connectors,
                    start,
                    graph_id,
                    categories,
                    transit,
                ),
                start,
                end,
            )
        )
        report["gtfs"] = {
            **normalized.report,
            **policy,
            "snapshot_id": transit_snapshot.snapshot_id,
            "request_start_local": start.isoformat(),
            "request_end_local_exclusive": end.isoformat(),
            "unknown_dates": [day.isoformat() for day in ref.unknown_dates],
            "source_lineage": gtfs_lineage(gtfs, ROUTES),
        }
    return result, report


def build_transit(
    snapshot: AcceptedData,
    table: Timetable,
    graph: RoadGraph,
    edges: dict[tuple[str, str, str], float],
    connectors: dict[str, dict[str, Any]],
    physical: dict[str, LonLat],
    prior: Timetable | None,
) -> tuple[Timetable, dict[str, Any]]:
    nodes = {node.node_id: node.location for node in snapshot.access_nodes}
    stops = {node for node in table.scope.node_ids}
    graph_id = graph.evidence.evidence_id
    footpaths: dict[tuple[str, str], TimetableFootpath] = {}

    def path(source: str, target: str, seconds: float, refs: tuple[str, ...]) -> None:
        if source != target:
            footpaths[source, target] = TimetableFootpath(
                canonical_hash([source, target]), source, target, seconds, True, refs
            )

    for (mode, a, b), seconds in edges.items():
        if mode == "pedestrian":
            path(a, b, seconds, (graph_id,))
    tasks = {}
    for identity, connector in connectors.items():
        seconds = connector["seconds"]
        if seconds is None:
            continue
        access = connector["access_node_id"]
        refs = tuple(connector["evidence_refs"])
        path(identity, access, seconds, refs)
        path(access, identity, seconds, refs)
        loc = nodes[access]
        centre = loc.lon, loc.lat
        nearby = sorted(stops, key=lambda stop: (distance(nodes[stop], centre), stop))[:3]
        for stop in nearby:
            if distance(nodes[stop], centre) <= 1100:
                tasks["pedestrian", access, stop] = loc, nodes[stop]
                tasks["pedestrian", stop, access] = nodes[stop], loc
    for (_, a, b), seconds in graph.collect(tasks).items():
        path(a, b, seconds, (graph_id,))

    runs = tuple(replace(run, snapshot_id=snapshot.snapshot_id) for run in table.runs)
    by_execution = {(run.trip_id, run.service_date): run.run_id for run in runs}
    transfers: list[TimetableTransfer] = []
    if prior is not None:
        identities = {run.run_id: (run.trip_id, run.service_date) for run in prior.runs}
        for rule in prior.transfers:
            # The previous consumer policy is approved for this dated pair only.
            if rule.from_run_id is None or rule.to_run_id is None:
                continue
            incoming = by_execution.get(identities[rule.from_run_id])
            outgoing = by_execution.get(identities[rule.to_run_id])
            if incoming is not None and outgoing is not None:
                transfers.append(replace(rule, from_run_id=incoming, to_run_id=outgoing))
    return Timetable(
        replace(
            table.scope,
            snapshot_id=snapshot.snapshot_id,
            node_ids=table.scope.node_ids | frozenset(nodes) | frozenset(physical),
            footpath_version="compact-valhalla-footpaths-v1",
            transfer_version="november-handoff-dated-transfer",
            evidence_refs=tuple(sorted({*table.scope.evidence_refs, graph_id})),
        ),
        runs,
        tuple(footpaths.values()),
        tuple(transfers),
    ), {"footpaths": len(footpaths), "transfer_rules": [r.rule_id for r in transfers]}


def via_moscow(
    left: IntercityInputs, right: IntercityInputs, origin: str, destination: str
) -> IntercityInputs | None:
    a, b = left.car, right.car
    if a is None or b is None or left.car_geometry is None or right.car_geometry is None:
        return None
    if left.car_geometry.coordinates[-1] != right.car_geometry.coordinates[0]:
        # A known gap requires its own directed approach time.
        return None
    refs = tuple(sorted(set(a.evidence_refs + b.evidence_refs)))
    network = replace(left.reference_graph.network, evidence_refs=refs)
    boundary = replace(
        left.reference_scope,
        network=network,
        destination_node_ids=(b.destination_node_id,),
        destination_territory_id=destination,
    )
    table = replace(
        left.timetable,
        scope=replace(
            left.timetable.scope,
            node_ids=frozenset((a.origin_node_id, b.destination_node_id)),
            evidence_refs=refs,
            limitations=(*left.timetable.scope.limitations, "ROAD_CHAIN_VIA_MOSCOW"),
        ),
    )
    return left.model_copy(
        update={
            "scope": replace(left.scope, comparison_scope=boundary, evidence_refs=refs),
            "timetable": table,
            "reference_graph": replace(
                left.reference_graph,
                network=network,
                states=(
                    next(s for s in left.reference_graph.states if s.node_id == a.origin_node_id),
                    next(
                        s
                        for s in right.reference_graph.states
                        if s.node_id == b.destination_node_id
                    ),
                ),
                evidence_refs=refs,
            ),
            "reference_scope": boundary,
            "car": replace(
                a,
                chain_id=f"{origin}-{destination}-via-moscow",
                comparison_scope=boundary,
                destination_node_id=b.destination_node_id,
                path_seconds=a.path_seconds + b.path_seconds,
                evidence_refs=refs,
            ),
            "car_geometry": left.car_geometry.model_copy(
                update={
                    "coordinates": left.car_geometry.coordinates
                    + right.car_geometry.coordinates[1:],
                    "evidence_refs": list(refs),
                }
            ),
        }
    )


def regional_cargo_inventory() -> list[dict[str, Any]]:
    capitals = {
        "region-ru-mow": "moscow",
        "region-ru-kgd": "kaliningrad",
        "region-ru-kda": "city-q3646",
    }
    return [
        {
            "origin_id": a,
            "destination_id": b,
            "origin_city_id": capitals[a],
            "destination_city_id": capitals[b],
            "status": "unknown",
            "missing": {
                "road": ["TRUCK_CHAIN_AND_TERMINAL_OPERATIONS"]
                + (
                    ["INTERNATIONAL_TRANSIT_COMPATIBILITY_AND_BORDER_WAIT_OR_FERRY_CHAIN"]
                    if "region-ru-kgd" in (a, b)
                    else []
                ),
                "rail": ["ACCEPTED_FREIGHT_SERVICE_AND_STATION_OPERATIONS"],
                "air": ["ACCEPTED_FREIGHT_CAPACITY_AND_TERMINAL_OPERATIONS"],
                "water": ["BOOKABLE_FREIGHT_SAILINGS_AND_LAND_LEGS_AND_PORT_OPERATIONS"],
                "pipeline": ["DIRECTED_INTAKE_DELIVERY_POINTS_AND_PRODUCT"],
            },
        }
        for a in capitals
        for b in capitals
        if a != b
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True, help="Restored November 11 runtime")
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--gtfs", type=Path)
    parser.add_argument("--kudikina-capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--handoff-manifest-sha256", required=True)
    parser.add_argument("--verified-urban-cargo", type=Path, help="Verified cargo manifest file")
    parser.add_argument("--verified-urban-cargo-sha256")
    args = parser.parse_args()
    if sha(args.handoff / "manifest.json") != args.handoff_manifest_sha256:
        raise ValueError("NOVEMBER_HANDOFF_MANIFEST_MISMATCH")
    source_manifest = Manifest.model_validate(load(args.handoff / "manifest.json"))
    cargo_profiles_by_id = {row.profile_id: row for row in source_manifest.cargo_profiles}
    verified_cargo = None
    if args.verified_urban_cargo is not None:
        if sha(args.verified_urban_cargo) != args.verified_urban_cargo_sha256:
            raise ValueError("VERIFIED_CARGO_MANIFEST_MISMATCH")
        verified_cargo = Manifest.model_validate(load(args.verified_urban_cargo))
        if any(
            row.analysis_kind != "urban_cargo"
            or row.city_id not in ZONES
            or row.executor != "accepted_inputs"
            or row.scenarios
            for row in verified_cargo.profiles
        ):
            raise ValueError("VERIFIED_CARGO_REQUIRES_URBAN_INPUTS")
        for row in verified_cargo.cargo_profiles:
            if cargo_profiles_by_id.setdefault(row.profile_id, row) != row:
                raise ValueError("VERIFIED_CARGO_PROFILE_ID_COLLISION")
    base = ProductRegistry(cargo_profiles=list(cargo_profiles_by_id.values()))
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    refs: dict[str, SnapshotRef] = {}
    profiles: list[Profile] = []
    receipt: dict[str, Any] = {
        "source_manifest_sha256": sha(args.handoff / "manifest.json"),
        "cities": {},
        "urban_cargo": {},
    }

    def source_snapshot(
        profile: Profile, root: Path = args.handoff, manifest: Manifest = source_manifest
    ) -> AcceptedData:
        row = next(item for item in manifest.snapshots if item.snapshot_id == profile.snapshot_id)
        path = (root / row.path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("PARENT_SNAPSHOT_OUTSIDE_HANDOFF")
        snapshot = AcceptedData.model_validate_json(path.read_bytes())
        if (
            sha(path) != row.sha256
            or canonical_hash(snapshot.model_dump(mode="json")) != row.content_hash
        ):
            raise ValueError("PARENT_SNAPSHOT_IDENTITY_MISMATCH")
        return snapshot

    def add(snapshot: AcceptedData, profile: Profile, inputs: EngineInputs) -> None:
        index = len(profiles)
        data = snapshot.model_dump(mode="json")
        content_hash = canonical_hash(data)
        if snapshot.snapshot_id in refs and refs[snapshot.snapshot_id].content_hash != content_hash:
            raise ValueError("COMPACT_SNAPSHOT_ID_COLLISION")
        if snapshot.snapshot_id not in refs:
            refs[snapshot.snapshot_id] = SnapshotRef.model_validate(
                {
                    **write(out, f"snapshot-{len(refs)}.json", data),
                    "snapshot_id": snapshot.snapshot_id,
                    "content_hash": content_hash,
                }
            )
        profile = profile.model_copy(
            update={
                "compact": True,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_hash": refs[snapshot.snapshot_id].content_hash,
                "inputs": FileRef.model_validate(
                    write(
                        out,
                        f"inputs-{index}.json",
                        InputDocument(
                            schema_version="waydex-engine-inputs/1",
                            inputs=inputs,
                        ).model_dump(mode="python"),
                    )
                ),
            }
        )
        graph, costing = input_fingerprints(profile, inputs, base)
        profile = profile.model_copy(
            update={
                "graph_fingerprint": graph,
                "routing_profile_fingerprint": costing,
            }
        )
        validate_inputs(profile, inputs, snapshot, base)
        profiles.append(profile)

    def source_inputs(profile: Profile, root: Path = args.handoff) -> EngineInputs:
        assert profile.inputs is not None
        path = (root / profile.inputs.path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("PARENT_INPUT_OUTSIDE_HANDOFF")
        if sha(path) != profile.inputs.sha256:
            raise ValueError("PARENT_INPUT_IDENTITY_MISMATCH")
        return InputDocument.model_validate_json(path.read_bytes()).inputs

    transit_profile = next(
        p for p in source_manifest.profiles if p.passenger_modes == ["public_transport"]
    )
    previous_transit = source_inputs(transit_profile)
    assert isinstance(previous_transit, UrbanInputs)
    with httpx.Client(timeout=20, trust_env=False) as client:
        for city in ZONES:
            cargo_profiles = [
                p
                for p in source_manifest.profiles
                if p.analysis_kind == "urban_cargo" and p.city_id == city
            ]
            parent_profile = next(
                p
                for p in source_manifest.profiles
                if p.city_id == city
                and p.analysis_kind == "urban_passenger"
                and p.executor == "walk_batches"
            )
            parent = source_snapshot(parent_profile)
            graph = RoadGraph(client, cargo_profiles[0], args.graphs / city)
            prepared, report = prepare_passenger(
                city,
                parent,
                graph,
                args.gtfs,
                previous_transit if city == "moscow" else None,
                args.kudikina_capture,
            )
            for snapshot, inputs, start, end in prepared:
                mode = cast(
                    Literal["walk", "private_car", "public_transport"],
                    inputs.coverage[0].scope.passenger_mode,
                )
                refs_in_transfers = (
                    {ref for rule in inputs.timetable.transfers for ref in rule.evidence_refs}
                    if inputs.timetable
                    else set()
                )
                categories = (
                    {candidate.category for candidate in inputs.candidates}
                    if not inputs.timetable
                    else {c for d in inputs.destinations for c in d.categories}
                )
                if not categories:
                    continue
                add(
                    snapshot,
                    Profile(
                        profile_id=f"compact-{city}-{mode}",
                        analysis_kind="urban_passenger",
                        city_id=city,
                        passenger_modes=[mode],
                        categories=sorted(categories),
                        valid_from=start,
                        valid_to=end,
                        snapshot_id=snapshot.snapshot_id,
                        snapshot_hash="0" * 64,
                        graph_fingerprint="0" * 64,
                        routing_profile_fingerprint="0" * 64,
                        static_observations=True,
                        max_route_pairs=450,
                        evidence=[
                            row
                            for row in transit_profile.evidence
                            if row.evidence_id in refs_in_transfers
                        ],
                    ),
                    inputs,
                )
            receipt["cities"][city] = report
            accepted = (
                [p for p in verified_cargo.profiles if p.city_id == city] if verified_cargo else []
            )
            if accepted:
                assert verified_cargo is not None and args.verified_urban_cargo is not None
                root = args.verified_urban_cargo.parent
                prepared_scopes: list[dict[str, Any]] = []
                for profile in accepted:
                    snapshot = source_snapshot(profile, root, verified_cargo)
                    prepared_cargo = source_inputs(profile, root)
                    if (
                        not isinstance(prepared_cargo, UrbanCargoInputs)
                        or not prepared_cargo.receipts
                    ):
                        raise ValueError("VERIFIED_CARGO_REQUIRES_PREPARED_ROUTE_PROOFS")
                    add(snapshot, profile, prepared_cargo)
                    prepared_scopes.append(
                        {
                            "profile": profile.profile_id,
                            "cargo_profile": profile.cargo_profile_id,
                            "direction": profile.direction,
                            "sites": [site.site_id for site in prepared_cargo.sites],
                            "nodes": [node.node_id for node in prepared_cargo.nodes],
                            "evidence": list(prepared_cargo.coverage.evidence_refs),
                        }
                    )
                receipt["urban_cargo"][city] = {
                    "verified_manifest_sha256": args.verified_urban_cargo_sha256,
                    "scopes": prepared_scopes,
                }
                verified_keys = {(p.cargo_profile_id, p.direction) for p in accepted}
                cargo_profiles = [
                    p
                    for p in cargo_profiles
                    if (p.cargo_profile_id, p.direction) not in verified_keys
                ]
                if not cargo_profiles:
                    continue
            freight_parent = source_snapshot(cargo_profiles[0])
            sites = {s.site_id for s in nearest(freight_parent.freight_sites, "site_id", city, 3)}
            cargo_inputs = source_inputs(cargo_profiles[0])
            assert isinstance(cargo_inputs, UrbanCargoInputs)
            usable_nodes = {node.node_id for node in cargo_inputs.nodes}
            nodes = {
                n.freight_node_id
                for n in nearest(
                    (
                        node
                        for node in freight_parent.freight_nodes
                        if node.freight_node_id in usable_nodes
                    ),
                    "freight_node_id",
                    city,
                    3,
                )
            }
            freight = compact_snapshot(freight_parent, site_ids=sites, freight_node_ids=nodes)
            observed = {
                "parent": freight_parent.snapshot_id,
                "sites": sorted(sites),
                "nodes": sorted(nodes),
                "site_sources": {s.site_id: s.source_refs for s in freight.freight_sites},
                "node_sources": {n.freight_node_id: n.source_refs for n in freight.freight_nodes},
                "limitations": ["COMPATIBILITY_UNVERIFIED"]
                if nodes
                else ["NO_ACCEPTED_FREIGHT_NODES"],
            }
            if accepted:
                receipt["urban_cargo"][city]["network_only"] = observed
            else:
                receipt["urban_cargo"][city] = observed
            for profile in cargo_profiles if nodes else ():
                assert profile.inputs is not None and profile.truck_routing is not None
                old = source_inputs(profile)
                assert isinstance(old, UrbanCargoInputs)
                context = replace(old.coverage.context, snapshot_id=freight.snapshot_id)
                kept_nodes = tuple(
                    replace(node, context=context) for node in old.nodes if node.node_id in nodes
                )
                kept_sites = tuple(
                    replace(site, context=context) for site in old.sites if site.site_id in sites
                )
                cargo = old.model_copy(
                    update={
                        "sites": kept_sites,
                        "nodes": kept_nodes,
                        "coverage": replace(
                            old.coverage,
                            context=context,
                            expected_site_ids=tuple(sorted(sites)),
                            evidence_refs=tuple(
                                sorted(
                                    {e for s in kept_sites for e in s.evidence_refs}
                                    | {e for n in kept_nodes for e in n.mapping_evidence_refs}
                                )
                            ),
                        ),
                    }
                )
                add(
                    freight,
                    profile.model_copy(
                        update={
                            "max_route_pairs": 9,
                            "truck_routing": profile.truck_routing.model_copy(
                                update={"max_pairs": 9}
                            ),
                        }
                    ),
                    cargo,
                )
            print(city, "prepared", flush=True)

    road: dict[tuple[str, str], tuple[Profile, AcceptedData, IntercityInputs]] = {}
    for profile in source_manifest.profiles:
        if profile.analysis_kind != "intercity_passenger" or profile.inputs is None:
            continue
        snapshot = source_snapshot(profile)
        road_inputs = source_inputs(profile)
        assert isinstance(road_inputs, IntercityInputs)
        assert profile.origin_id is not None and profile.destination_id is not None
        road[profile.origin_id, profile.destination_id] = (profile, snapshot, road_inputs)
        add(snapshot, profile, road_inputs)
    road_receipt = load(args.handoff / "intercity-road/collection.json")
    receipt["intercity_road"] = road_receipt["rows"]
    for origin, destination in (("kaliningrad", "novorossiysk"), ("novorossiysk", "kaliningrad")):
        first, snapshot, left = road[origin, "moscow"]
        last, _, right = road["moscow", destination]
        combined = via_moscow(left, right, origin, destination)
        if combined is not None:
            assert first.inputs is not None and last.inputs is not None
            add(
                snapshot,
                first.model_copy(
                    update={
                        "profile_id": f"compact-{origin}-{destination}-via-moscow",
                        "destination_id": destination,
                        "valid_from": max(first.valid_from, last.valid_from),
                        "valid_to": min(first.valid_to, last.valid_to),
                    }
                ),
                combined,
            )
            receipt["intercity_road"].append(
                {
                    "origin": origin,
                    "destination": destination,
                    "via": "moscow",
                    "inputs": [first.inputs.path, last.inputs.path],
                    "minimum_proven": False,
                }
            )
    receipt["intercity_cargo_regions"] = regional_cargo_inventory()
    selection = FileRef.model_validate(write(out, "selection.json", receipt))
    manifest = source_manifest.model_copy(
        update={
            "compact": True,
            "selection": selection,
            "areas": [],
            "snapshots": list(refs.values()),
            "profiles": profiles,
            "cargo_profiles": list(cargo_profiles_by_id.values()),
            "presets": [],
        }
    )
    print(write(out, "manifest.json", manifest.model_dump(mode="json")))


if __name__ == "__main__":
    main()
