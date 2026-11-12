"""Assemble reviewed passenger chains from dated schedules and routed transfers."""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from shutil import copytree
from zoneinfo import ZoneInfo

from a2_api.adapters.product_inputs import InputDocument, IntercityInputs
from a2_api.adapters.product_runtime import Manifest, Profile, input_fingerprints, validate_inputs
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_engine import (
    IntercityAnalysisScope,
    PhysicalService,
    ReferenceCalibration,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNetwork,
    ReferenceScope,
    ReferenceState,
    RoadBuild,
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
    TransportModeBinding,
)
from a2_engine.intercity.analysis.models import CapitalBoundary, PhysicalDeparture
from a2_engine.routing.reference.models import ReferenceOperation
from a2_engine.routing.scheduled import ScheduledRoadAccess
from a2_engine.routing.timetable_models import TimetableFootpath, TimetableTransfer
from assemble_compact_runtime import write


def read_road(root, name, coordinates, costing):
    road = json.loads((root / f"{name}.json").read_text())
    if "subroutes" in road:
        if costing != "auto":
            raise ValueError("Only auto routes can be assembled from multiple receipts")
        waypoints = [coordinates[0], *(tuple(point) for point in road["via"]), coordinates[-1]]
        subroutes = [
            read_road(root, segment, (a, b), costing)
            for segment, (a, b) in zip(
                road["subroutes"], zip(waypoints, waypoints[1:]), strict=True
            )
        ]
        if any(segment["status"] != subroutes[0]["status"] for segment in subroutes):
            raise ValueError("Split road routes must share a Valhalla build")
        return {
            "status": subroutes[0]["status"],
            "source": subroutes[0]["source"],
            "observed_at": max(segment["observed_at"] for segment in subroutes),
            "subroutes": subroutes,
            "seconds": sum(segment["seconds"] for segment in subroutes),
        }
    request = road["reference_request"]
    points = [(point["lon"], point["lat"]) for point in request["locations"]]
    options = request["costing_options"]
    if costing == "auto":
        valid_options = options in (
            {"auto": {"speed_types": ["freeflow"]}},
            {"auto": {"speed_types": ["freeflow"], "use_ferry": 0}},
        )
    else:
        valid_options = options in (
            {"pedestrian": {"walking_speed": 5}},
            {"pedestrian": {"walking_speed": 5, "use_ferry": 0}},
        )
    trip = road["reference"]["trip"]
    returned = [(point["lon"], point["lat"]) for point in trip["locations"]]
    summary = trip["summary"]
    if (
        [points[0], points[-1]] != list(coordinates)
        or len(points) != len(returned)
        or any(
            abs(a - b) > 1e-6
            for p, q in zip(points, returned, strict=True)
            for a, b in zip(p, q, strict=True)
        )
        or request["costing"] != costing
        or not valid_options
        or summary["has_ferry"]
        or summary["has_time_restrictions"]
        or not summary["time"] > 0
    ):
        raise ValueError(f"Unexpected routed reference: {name}")
    return {**road, "seconds": summary["time"]}


def prepare(base: Path, data_file: Path, road_root: Path, out: Path, day: date):
    bundle = json.loads(data_file.read_text())
    manifest = json.loads((base / "manifest.json").read_text())
    template = next(p for p in manifest["profiles"] if p["analysis_kind"] == "intercity_passenger")
    snapshot_ref = next(
        r for r in manifest["snapshots"] if r["snapshot_id"] == template["snapshot_id"]
    )
    parent = json.loads((base / snapshot_ref["path"]).read_text())
    catalog = {item["territory_id"]: item for item in manifest["territories"]} | bundle[
        "foreign_territories"
    ]
    stations = bundle["stations"]
    copytree(base, out)
    for chain in bundle["chains"]:
        aviation_access = chain.get("road_air_access", False)
        water_access = bundle.get("water_with_ground_access", False) and not aviation_access
        road_access = chain.get("road_access", False)
        parts = [bundle["segments"][name] for name in chain["segments"]]
        bounds = [tuple(part["nodes"]) for part in parts]
        leg_modes = [part.get("mode", "road") for part in parts]
        modes = tuple(sorted(set(leg_modes)))
        transport_types = {"road": "bus", "rail": "train", "water": "ferry", "air": "plane"}
        if water_access and ("water" not in modes or len(modes) < 2):
            raise ValueError("A ferry profile needs the sailing and its land connections")
        if road_access and (
            len(parts) < 3
            or not all(part.get("on_demand") for part in (parts[0], parts[-1]))
            or any(part.get("on_demand") for part in parts[1:-1])
        ):
            raise ValueError("Expected two on-demand road legs at chain boundaries")
        node_ids = sorted({node for pair in bounds for node in pair})
        orig, dest = chain["territories"]
        zone = ZoneInfo(catalog[orig]["timezone"])
        start = datetime.combine(day, time.min, zone)
        end = start + timedelta(days=1)
        coordinate = {node: tuple(stations[node]["location"]) for node in node_ids}
        roads = [
            read_road(road_root, part["road"], (coordinate[a], coordinate[b]), "auto")
            if mode == "road"
            else None
            for part, (a, b), mode in zip(parts, bounds, leg_modes, strict=True)
        ]
        walks = [
            read_road(road_root, name, (coordinate[a], coordinate[b]), "pedestrian")
            if name
            else None
            for name, ((_, a), (b, _)) in zip(chain["walks"], zip(bounds, bounds[1:]), strict=True)
        ]
        routed = [receipt for receipt in (*roads, *walks) if receipt]
        build = routed[0]["status"]
        if any(receipt["status"] != build for receipt in routed):
            raise ValueError("Road references and transfers must use one Valhalla build")
        transit_sources = bundle["transit"]
        if road_access:
            transit_sources = [
                (source, url)
                for source, url in transit_sources
                if source.startswith("aeroflot-") == aviation_access
                and (
                    aviation_access
                    or source.startswith("don-") == (chain.get("water_service") == "don")
                )
            ]
        normalized_receipts = [
            [
                {k: v for k, v in receipt.items() if k != "seconds"} if receipt else None
                for receipt in group
            ]
            for group in (roads, walks)
        ]
        digest = canonical_hash(
            [
                day.isoformat(),
                chain,
                parts,
                *normalized_receipts,
                transit_sources,
                *(["terminal-road-access-v3"] if road_access else []),
            ]
        )
        sid = (
            "terminal-access-"
            if road_access
            else "ferry-chain-"
            if water_access
            else "rasp-bus-chain-"
        ) + digest[:24]
        graph_version = f"fosgis-valhalla:{build['version']}:tiles-{build['tileset_last_modified']}"
        evidence = []

        def evidence_ref(source, url, observed_at, license, content_hash):
            ident = sid + "-" + str(len(evidence) + 1)
            evidence.append(
                dict(
                    evidence_id=ident,
                    source_id=source,
                    source_version=digest,
                    url=url,
                    retrieved_at=observed_at,
                    content_hash=content_hash,
                    license=license,
                    verified=True,
                )
            )
            return ident

        route_refs = [
            evidence_ref(
                "valhalla",
                road["source"],
                road["observed_at"],
                "OpenStreetMap ODbL",
                canonical_hash({k: v for k, v in road.items() if k != "seconds"}),
            )
            if road
            else None
            for road in roads
        ]
        walk_refs = [
            evidence_ref(
                "valhalla",
                walk["source"],
                walk["observed_at"],
                "OpenStreetMap ODbL",
                canonical_hash({k: v for k, v in walk.items() if k != "seconds"}),
            )
            if walk
            else None
            for walk in walks
        ]
        schedule_refs = [
            evidence_ref(
                part["source_id"],
                part["url"],
                part["observed_at"],
                "Published schedule",
                canonical_hash(part),
            )
            for part in parts
        ]
        transit_refs = [
            evidence_ref(
                source,
                url,
                bundle["reviewed_at"],
                "Public passenger and border information",
                canonical_hash([source, url]),
            )
            for source, url in transit_sources
        ]
        refs = tuple(entry["evidence_id"] for entry in evidence)
        road_build = (
            RoadBuild(
                sid,
                graph_version,
                graph_version,
                graph_version,
                "passenger-auto-freeflow",
                "auto",
                json.dumps({"speed_types": ["freeflow"]}),
                tuple(ref for ref in route_refs if ref),
                build["version"],
            )
            if any(roads)
            else None
        )
        network = ReferenceNetwork(
            graph_version,
            sid,
            sid,
            "air-with-road-access"
            if aviation_access
            else "ferry-with-land-connections"
            if water_access
            else "bounded-international-bus-chain",
            sid,
            "a2-methodology-v3",
            "edge-calibrated-v1",
            refs,
            road_build,
        )
        scope = ReferenceScope(
            network, orig, dest, (bounds[0][0],), (bounds[-1][1],), modes, "exact_node_pair"
        )
        states, edges, ops = [], [], []
        runs, identities, services = [], [], []
        transfers, footpaths = [], []
        for i, ((a, b), part, road, route_ref, schedule_ref) in enumerate(
            zip(
                bounds,
                parts,
                roads,
                route_refs,
                schedule_refs,
                strict=True,
            )
        ):
            mode = leg_modes[i]
            first, last, edge_id = f"leg-{i}-start", f"leg-{i}-end", f"leg-{i}"
            edge_proof = route_ref or schedule_ref
            if road:
                seconds = road["seconds"]
            else:
                seconds = min(
                    (
                        datetime.fromisoformat(row["arrival"])
                        - datetime.fromisoformat(row["departure"])
                    ).total_seconds()
                    for row in part["departures"]
                )
            states.extend(
                (
                    ReferenceState(first, a, "departure", mode, edge_id),
                    ReferenceState(last, b, "arrival", mode, edge_id),
                )
            )
            sample = ReferenceCalibration(
                "freeflow-" + edge_id,
                edge_id,
                first,
                last,
                network,
                seconds,
                "static_freeflow_elapsed" if road else "running_time",
                (edge_proof,),
                road_build=road_build if road else None,
            )
            edges.append(
                ReferenceEdge(
                    edge_id,
                    first,
                    last,
                    (sample,),
                    (edge_proof,),
                    compatible=True,
                    boundary_semantics="logical_connection",
                )
            )
            run_zone = ZoneInfo(stations[a]["timezone"])
            if not part.get("on_demand"):
                for row in part["departures"]:
                    depart, arrive = (
                        datetime.fromisoformat(row[key]) for key in ("departure", "arrival")
                    )
                    if (
                        not start
                        <= depart
                        < start + timedelta(days=12 if water_access or road_access else 7)
                        or not depart < arrive
                    ):
                        continue
                    local_day = depart.astimezone(run_zone).date()
                    midnight = datetime.combine(local_day, time.min, run_zone)
                    trip = ("bus-" if mode == "road" else mode + "-") + canonical_hash(
                        [part["source_id"], row["record_id"], a, b, row["departure"]]
                    )[:24]
                    events = (
                        TimetableEvent(0, a, depart, depart, True, False, (schedule_ref,)),
                        TimetableEvent(1, b, arrive, arrive, False, True, (schedule_ref,)),
                    )
                    run = TimetableRun(
                        sid,
                        part["source_id"],
                        trip,
                        local_day,
                        str(run_zone),
                        transport_types[mode],
                        events,
                        True,
                        (schedule_ref,),
                    )
                    runs.append(run)
                    identities.append(
                        PhysicalService(
                            trip,
                            (run.run_id,),
                            (PhysicalDeparture(0, trip, (schedule_ref,)),),
                            (schedule_ref,),
                        )
                    )
                    services.append(
                        dict(
                            service_id=trip,
                            trip_id=trip,
                            mode=mode,
                            timezone=str(run_zone),
                            service_dates=[local_day.isoformat()],
                            trip_identity_confirmed=True,
                            source_refs=[schedule_ref],
                            stop_times=[
                                dict(
                                    node_id=e.node_id,
                                    sequence=e.sequence,
                                    arrival_seconds=int((e.arrival_at - midnight).total_seconds()),
                                    departure_seconds=int(
                                        (e.departure_at - midnight).total_seconds()
                                    ),
                                    pickup=e.pickup,
                                    dropoff=e.dropoff,
                                )
                                for e in events
                            ],
                        )
                    )
            if i == len(bounds) - 1:
                continue
            c = bounds[i + 1][0]
            walk = walks[i]
            wait = chain["transfer_seconds"][i]
            proof = tuple(
                item for item in (schedule_ref, schedule_refs[i + 1], walk_refs[i]) if item
            )
            if "water" in (mode, leg_modes[i + 1]):
                proof += tuple(transit_refs)
            if bool(walk) != (b != c) or wait <= 0:
                raise ValueError(
                    "Every transfer needs a positive buffer and a route between different stops"
                )
            if walk:
                seconds = walk["reference"]["trip"]["summary"]["time"]
                footpaths.append(
                    TimetableFootpath(f"walk-{i}", b, c, seconds, True, (walk_refs[i],))
                )
                wait += seconds
            if not (part.get("on_demand") or parts[i + 1].get("on_demand")):
                transfers.append(TimetableTransfer(f"transfer-{i}", b, c, wait, True, proof))
            ops.append(
                ReferenceOperation(
                    f"change-{i}",
                    last,
                    f"leg-{i + 1}-start",
                    network,
                    wait,
                    "routed_walk_and_boarding_allowance" if walk else "same_station_boarding",
                    proof,
                    compatible=True,
                )
            )
        table_end = max(start + timedelta(days=7), *(run.events[-1].arrival_at for run in runs))
        if road_access:
            table_end += timedelta(seconds=roads[-1]["seconds"] + chain["transfer_seconds"][-1])
        valid = {"start": day.isoformat(), "end": table_end.date().isoformat()}
        for entry in evidence:
            entry["valid_period"] = valid
        table = Timetable(
            TimetableScope(
                sid,
                graph_version,
                sid,
                sid,
                "reviewed-air-transfers"
                if aviation_access
                else "reviewed-ferry-transfers"
                if water_access
                else "reviewed-bus-transfers",
                sid,
                str(zone),
                start,
                table_end,
                frozenset(node_ids),
                False,
                False,
                False,
                False,
                False,
                refs,
                ("SELECTED_DATED_FLIGHTS",)
                if aviation_access
                else ("SELECTED_DATED_FERRY_CONNECTIONS", "DON_PASSENGER_VESSEL")
                if chain.get("water_service") == "don"
                else ("SELECTED_DATED_FERRY_CONNECTIONS",)
                if water_access
                else ("SELECTED_DATED_BUSES_ONLY", "INTERNATIONAL_BUS_VIA_POLAND"),
            ),
            tuple(runs),
            tuple(footpaths),
            tuple(transfers),
        )
        capitals = (
            tuple(
                CapitalBoundary(region, stations[node]["territory_id"], (node,), refs)
                for region, node in ((orig, bounds[0][0]), (dest, bounds[-1][1]))
            )
            if chain["level"] == "region"
            else ()
        )
        inputs = IntercityInputs(
            kind="intercity_passenger",
            scope=IntercityAnalysisScope(
                scope, modes, False, refs, "regions" if capitals else "cities", capitals
            ),
            timetable=table,
            timetable_service_clock="local_service_midnight_v1",
            transport_modes=tuple(
                TransportModeBinding(
                    transport_types[mode],
                    mode,
                    tuple(
                        ref
                        for ref, leg_mode in zip(schedule_refs, leg_modes, strict=True)
                        if leg_mode == mode
                    ),
                )
                for mode in modes
            ),
            mixed_only=water_access or aviation_access,
            water_with_ground_access=water_access,
            aviation_with_ground_access=aviation_access,
            road_access=(
                ScheduledRoadAccess(
                    *bounds[0],
                    roads[0]["seconds"],
                    chain["transfer_seconds"][0],
                    (route_refs[0], *transit_refs),
                ),
                ScheduledRoadAccess(
                    *bounds[-1],
                    roads[-1]["seconds"],
                    chain["transfer_seconds"][-1],
                    (route_refs[-1], *transit_refs),
                ),
            )
            if road_access
            else None,
            physical_services=tuple(identities),
            reference_graph=ReferenceGraph(
                network, tuple(states), tuple(edges), tuple(ops), "partial", refs
            ),
            reference_scope=scope,
            frequency_start_at=start,
            frequency_end_at=start + timedelta(days=7),
        )
        territory_ids = {orig, dest, *(stations[n]["territory_id"] for n in node_ids)}
        known = {row["territory_id"] for row in parent["territories"]}
        extra = []
        for ident in sorted(territory_ids - known):
            t = catalog[ident]
            extra.append(
                dict(
                    territory_id=ident,
                    name=t["name"],
                    level=t["level"],
                    timezone=t["timezone"],
                    area_km2=None,
                    source_refs=list(transit_refs),
                    geometry=dict(
                        geometry_id=ident + ":unavailable-boundary",
                        version=sid,
                        source_refs=list(transit_refs),
                    ),
                )
            )
        accepted = AcceptedData.model_validate(
            parent
            | dict(
                snapshot_id=sid,
                geographic_scope=sorted(territory_ids),
                valid_period=valid,
                territories=parent["territories"] + extra,
                scheduled_services=services,
                access_nodes=[
                    dict(
                        node_id=node,
                        kind=stations[node].get("kind", "bus_station"),
                        territory_id=stations[node]["territory_id"],
                        location=dict(zip(("lon", "lat"), coordinate[node])),
                        source_refs=list(refs),
                    )
                    for node in node_ids
                ],
                evidence=parent["evidence"] + evidence,
                source_versions=parent["source_versions"]
                | {entry["source_id"]: entry["source_version"] for entry in evidence},
                provenance=dict(
                    graph_version=graph_version,
                    calendar_version=sid,
                    geometry_version=sid,
                    population_version=None,
                ),
            )
        )
        profile = Profile.model_validate(
            template
            | dict(
                profile_id=sid,
                origin_id=orig,
                destination_id=dest,
                territory_level=chain["level"],
                snapshot_id=sid,
                snapshot_hash=canonical_hash(accepted.model_dump(mode="json")),
                valid_from=start.isoformat(),
                valid_to=end.isoformat(),
                static_observations=False,
            )
        )
        registry = ProductRegistry(snapshots=[accepted])
        graph_hash, costing_hash = input_fingerprints(profile, inputs, registry)
        profile = profile.model_copy(
            update=dict(graph_fingerprint=graph_hash, routing_profile_fingerprint=costing_hash)
        )
        validate_inputs(profile, inputs, accepted, registry)
        document = InputDocument(schema_version="waydex-engine-inputs/1", inputs=inputs).model_dump(
            mode="json"
        )
        document["inputs"]["timetable"]["scope"]["node_ids"].sort()
        manifest["snapshots"].append(
            write(out, sid + "-snapshot.json", accepted.model_dump(mode="json"))
            | dict(snapshot_id=sid, content_hash=profile.snapshot_hash)
        )
        if road_access:
            kept = []
            for previous in manifest["profiles"]:
                if (
                    previous["analysis_kind"] == "intercity_passenger"
                    and (
                        previous["territory_level"],
                        previous["origin_id"],
                        previous["destination_id"],
                    )
                    == (chain["level"], orig, dest)
                    and previous.get("inputs")
                ):
                    payload = json.loads((out / previous["inputs"]["path"]).read_text())["inputs"]
                    match = (
                        (
                            payload.get("aviation_with_ground_access")
                            or (
                                not payload.get("mixed_only")
                                and payload["scope"]["applicable_modes"] == ["air"]
                            )
                        )
                        if aviation_access
                        else payload.get("water_with_ground_access")
                    )
                    if (
                        match
                        and datetime.fromisoformat(previous["valid_from"]) == start
                        and datetime.fromisoformat(previous["valid_to"]) == end
                    ):
                        continue
                kept.append(previous)
            manifest["profiles"] = kept
        manifest["profiles"].append(
            profile.model_dump(mode="json")
            | dict(inputs=write(out, sid + "-inputs.json", document))
        )
        print(chain["level"], orig, dest, len(runs), len(transfers))
    write(out, "manifest.json", Manifest.model_validate(manifest).model_dump(mode="json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "data", "road", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    prepare(args.base, args.data, args.road, args.out, args.day)
