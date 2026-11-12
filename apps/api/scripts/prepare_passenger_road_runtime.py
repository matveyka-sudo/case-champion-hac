"""Publish domestic static car routes and an observed scheduled bus corridor."""

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
    AcceptedIntercityCar,
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
from assemble_compact_runtime import write

ZONE = ZoneInfo("Europe/Moscow")
BUS_STATIONS = ("s9876347", "s9623281")
PAIRS = (
    ("city", "moscow", "novorossiysk", "moscow-novorossiysk", False),
    ("city", "novorossiysk", "moscow", "novorossiysk-moscow", False),
    ("region", "moscow", "krasnodar", "moscow-krasnodar", False),
    ("region", "krasnodar", "moscow", "krasnodar-moscow", False),
    ("region", "moscow", "krasnodar", "salarievo-kras-bus", True),
)
IDS = {
    "moscow": ("moscow", "region-ru-mow"),
    "novorossiysk": ("novorossiysk", ""),
    "krasnodar": ("city-q3646", "region-ru-kda"),
}


def bus_rows(captures: Path, start: datetime):
    rows = {}
    for path in sorted(captures.glob("20??-??-??-moscow-krasnodar.json")):
        record = json.loads(path.read_text())
        for page in record["pages"]:
            for row in page["segments"]:
                thread = row.get("thread", {})
                if row.get("has_transfers") is not False or thread.get("transport_type") != "bus":
                    continue
                if (row["from"]["code"], row["to"]["code"]) != BUS_STATIONS:
                    continue
                depart = datetime.fromisoformat(row["departure"])
                arrive = datetime.fromisoformat(row["arrival"])
                if (
                    depart < start
                    or arrive <= depart
                    or (arrive - depart).total_seconds() != row["duration"]
                ):
                    continue
                key = (thread["number"], depart)
                existing = rows.get(key)
                if existing is not None and existing[1] != arrive:
                    raise ValueError("Conflicting bus execution")
                rows[key] = (depart, arrive, record["observed_at"], thread["uid"])
    return rows


def prepare(base: Path, road: Path, captures: Path, out: Path, day: date):
    manifest = json.loads((base / "manifest.json").read_text())
    template = next(p for p in manifest["profiles"] if p["analysis_kind"] == "intercity_passenger")
    snapshot = next(r for r in manifest["snapshots"] if r["snapshot_id"] == template["snapshot_id"])
    parent = json.loads((base / snapshot["path"]).read_text())
    start = datetime.combine(day, time.min, ZONE)
    end = start + timedelta(days=1)
    copytree(base, out)
    for level, left, right, route, bus in PAIRS:
        receipt = json.loads((road / f"{route}.json").read_text())
        reference = receipt["reference"]["trip"]
        observation = receipt["observation"]["trip"]
        locations = receipt["reference_request"]["locations"]
        if locations != receipt["observation_request"]["locations"]:
            raise ValueError("Road observation and reference boundaries differ")
        ref_options = receipt["reference_request"]["costing_options"]["auto"]
        if ref_options != {"speed_types": ["freeflow"]}:
            raise ValueError("Static free-flow reference required")
        for trip in (reference, observation):
            if (
                len(trip["locations"]) != 2
                or [(p["lon"], p["lat"]) for p in trip["locations"]]
                != [(p["lon"], p["lat"]) for p in locations]
                or trip["summary"]["time"] <= 0
                or trip["summary"]["has_ferry"]
                or trip["summary"]["has_time_restrictions"]
            ):
                raise ValueError("Route is outside the supported static domestic scope")
        dated = bus_rows(captures, start) if bus else {}
        if bus and not dated:
            raise ValueError("Dated bus departures required")
        digest = canonical_hash(
            [
                level,
                route,
                day.isoformat(),
                receipt,
                [
                    (d.isoformat(), a.isoformat(), observed, uid)
                    for d, a, observed, uid in dated.values()
                ],
            ]
        )
        sid = "rasp-road-" + digest[:24]
        proof, schedule_proof = sid + "-freeflow", sid + "-bus"
        refs = (proof, schedule_proof) if bus else (proof,)
        nodes = BUS_STATIONS if bus else (f"road-center-{left}", f"road-center-{right}")
        cities = (IDS[left][0], IDS[right][0])
        orig, dest = (IDS[left][1], IDS[right][1]) if level == "region" else cities
        version = receipt["status"]["version"]
        tiles = receipt["status"]["tileset_last_modified"]
        graph_version = f"fosgis-valhalla:{version}:tiles-{tiles}"
        build = RoadBuild(
            sid,
            graph_version,
            graph_version,
            graph_version,
            "passenger-auto-freeflow",
            "auto",
            json.dumps(ref_options),
            (proof,),
            version,
        )
        network = ReferenceNetwork(
            graph_version,
            sid,
            sid,
            "bounded-domestic-road",
            sid,
            "a2-methodology-v3",
            "edge-calibrated-v1",
            refs,
            build,
        )
        scope = ReferenceScope(
            network, orig, dest, (nodes[0],), (nodes[1],), ("road",), "exact_node_pair"
        )
        states = (
            ReferenceState("start", nodes[0], "departure", "road", route),
            ReferenceState("finish", nodes[1], "arrival", "road", route),
        )
        sample = ReferenceCalibration(
            "freeflow",
            "corridor",
            "start",
            "finish",
            network,
            reference["summary"]["time"],
            "static_freeflow_elapsed",
            (proof,),
            road_build=build,
        )
        graph = ReferenceGraph(
            network,
            states,
            (
                ReferenceEdge(
                    "corridor",
                    "start",
                    "finish",
                    (sample,),
                    (proof,),
                    compatible=True,
                    boundary_semantics="logical_connection",
                ),
            ),
            (),
            "partial",
            refs,
        )
        runs, physical, services = [], [], []
        for (number, _), (depart, arrive, _, _) in sorted(dated.items()):
            local_day = depart.astimezone(ZONE).date()
            midnight = datetime.combine(local_day, time.min, ZONE)
            trip_id = "bus-" + canonical_hash([number, depart.isoformat(), nodes])[:24]
            events = (
                TimetableEvent(0, nodes[0], depart, depart, True, False, (schedule_proof,)),
                TimetableEvent(1, nodes[1], arrive, arrive, False, True, (schedule_proof,)),
            )
            run = TimetableRun(
                sid,
                "yandex-rasp",
                trip_id,
                local_day,
                str(ZONE),
                "bus",
                events,
                True,
                (schedule_proof,),
            )
            runs.append(run)
            physical.append(
                PhysicalService(
                    trip_id,
                    (run.run_id,),
                    (PhysicalDeparture(0, trip_id, (schedule_proof,)),),
                    (schedule_proof,),
                )
            )
            services.append(
                dict(
                    service_id=trip_id,
                    trip_id=trip_id,
                    mode="road",
                    timezone=str(ZONE),
                    service_dates=[local_day.isoformat()],
                    trip_identity_confirmed=True,
                    source_refs=[schedule_proof],
                    stop_times=[
                        dict(
                            node_id=e.node_id,
                            sequence=e.sequence,
                            arrival_seconds=int((e.arrival_at - midnight).total_seconds()),
                            departure_seconds=int((e.departure_at - midnight).total_seconds()),
                            pickup=e.pickup,
                            dropoff=e.dropoff,
                        )
                        for e in events
                    ],
                )
            )
        table_end = max([end, *(r.events[-1].arrival_at for r in runs)])
        table = Timetable(
            TimetableScope(
                sid,
                graph_version,
                sid,
                sid,
                "not-collected",
                sid,
                str(ZONE),
                start,
                table_end,
                frozenset(nodes),
                False,
                False,
                False,
                False,
                False,
                refs,
                ("BOUNDED_STATIC_ROAD_CORRIDOR", "SELECTED_DATED_BUSES_ONLY")
                if bus
                else ("BOUNDED_STATIC_ROAD_CORRIDOR",),
            ),
            tuple(runs),
            (),
            (),
        )
        capitals = (
            tuple(
                CapitalBoundary(region, city, (node,), refs)
                for region, city, node in zip((orig, dest), cities, nodes, strict=True)
            )
            if level == "region"
            else ()
        )
        inputs = IntercityInputs(
            kind="intercity_passenger",
            scope=IntercityAnalysisScope(
                scope,
                ("road",),
                False,
                refs,
                "regions" if capitals else "cities",
                capitals,
            ),
            timetable=table,
            timetable_service_clock="local_service_midnight_v1",
            transport_modes=(TransportModeBinding("bus", "road", (schedule_proof,)),)
            if bus
            else (),
            physical_services=tuple(physical),
            reference_graph=graph,
            reference_scope=scope,
            frequency_start_at=start,
            frequency_end_at=min(table_end, end + timedelta(days=6)),
            car=None
            if bus
            else AcceptedIntercityCar(
                route,
                scope,
                nodes[0],
                nodes[1],
                observation["summary"]["time"],
                False,
                (proof,),
            ),
            car_departure_at=None if bus else start,
        )
        valid = {"start": day.isoformat(), "end": table_end.date().isoformat()}
        evidence = [
            dict(
                evidence_id=eid,
                source_id=source,
                source_version=digest,
                url=url,
                retrieved_at=stamp,
                content_hash=digest,
                valid_period=valid,
                license=license,
                verified=True,
            )
            for eid, source, url, stamp, license in [
                (
                    proof,
                    "valhalla",
                    receipt["source"],
                    receipt["observed_at"],
                    "OpenStreetMap ODbL",
                ),
                *(
                    [
                        (
                            schedule_proof,
                            "yandex-rasp",
                            "https://rasp.yandex.ru/",
                            max(row[2] for row in dated.values()),
                            "Данные предоставлены сервисом Яндекс.Расписания",
                        )
                    ]
                    if bus
                    else []
                ),
            ]
        ]
        required = set(cities) | {orig, dest}
        territories = list(parent["territories"])
        missing = required - {t["territory_id"] for t in territories}
        for entry in manifest["territories"]:
            ident = entry["territory_id"]
            if ident in missing:
                territories.append(
                    dict(
                        territory_id=ident,
                        name=entry["name"],
                        level=entry["level"],
                        timezone=entry["timezone"],
                        area_km2=None,
                        source_refs=[proof],
                        geometry=dict(
                            geometry_id=ident + ":unavailable-boundary",
                            version=sid,
                            source_refs=[proof],
                        ),
                    )
                )
        accepted = AcceptedData.model_validate(
            parent
            | dict(
                snapshot_id=sid,
                geographic_scope=sorted(required),
                territories=territories,
                valid_period=valid,
                scheduled_services=services,
                access_nodes=[
                    dict(
                        node_id=node,
                        kind="bus_station" if bus else "road_access",
                        territory_id=city,
                        location=point,
                        source_refs=list(refs),
                    )
                    for node, city, point in zip(nodes, cities, locations, strict=True)
                ],
                evidence=parent["evidence"] + evidence,
                source_versions=parent["source_versions"]
                | {e["source_id"]: digest for e in evidence},
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
                territory_level=level,
                snapshot_id=sid,
                snapshot_hash=canonical_hash(accepted.model_dump(mode="json")),
                valid_from=start.isoformat(),
                valid_to=end.isoformat(),
                static_observations=not bus,
            )
        )
        registry = ProductRegistry(snapshots=[accepted])
        g, p = input_fingerprints(profile, inputs, registry)
        profile = profile.model_copy(
            update={"graph_fingerprint": g, "routing_profile_fingerprint": p}
        )
        validate_inputs(profile, inputs, accepted, registry)
        snapshot_ref = write(out, sid + "-snapshot.json", accepted.model_dump(mode="json"))
        manifest["snapshots"].append(
            snapshot_ref | dict(snapshot_id=sid, content_hash=profile.snapshot_hash)
        )
        doc = InputDocument(schema_version="waydex-engine-inputs/1", inputs=inputs)
        manifest["profiles"].append(
            profile.model_dump(mode="json")
            | {
                "inputs": write(out, sid + "-inputs.json", doc.model_dump(mode="json")),
            }
        )
        print(level, route, "bus" if bus else "car")
    # Supersede only the two diagnostic car profiles overlapping the new city profiles.
    manifest["profiles"] = [
        profile
        for profile in manifest["profiles"]
        if profile["profile_id"].startswith("rasp-road-")
        or not (
            profile["analysis_kind"] == "intercity_passenger"
            and profile["static_observations"]
            and (profile["origin_id"], profile["destination_id"])
            in {("moscow", "novorossiysk"), ("novorossiysk", "moscow")}
        )
    ]
    write(out, "manifest.json", Manifest.model_validate(manifest).model_dump(mode="json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("base", "road", "captures", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    prepare(args.base, args.road, args.captures, args.out, args.day)
