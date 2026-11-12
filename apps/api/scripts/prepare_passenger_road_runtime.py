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
TRANSIT_PAIRS = (
    ("city", "moscow", "kaliningrad", "moscow-kaliningrad", False),
    ("city", "kaliningrad", "moscow", "kaliningrad-moscow", False),
    ("city", "novorossiysk", "kaliningrad", "novorossiysk-kaliningrad", False),
    ("city", "kaliningrad", "novorossiysk", "kaliningrad-novorossiysk", False),
)
TRANSIT_ROUTE_PARTS = {
    "novorossiysk-kaliningrad": ("novorossiysk-moscow", "moscow-kaliningrad"),
    "kaliningrad-novorossiysk": ("kaliningrad-moscow", "moscow-novorossiysk"),
}
MEDININKAI = (25.7219, 54.5415)
KYBARTAI = (22.74493, 54.64234)
REVIEWED_TRANSIT_DAY = date(2026, 11, 16)
TRANSIT_SOURCES = (
    (
        "lt-ftd",
        "https://ru.mfa.lt/ru/konsulskaya-informatsiya/glavnaya-konsulskaya-informatsiya/uproshchennye-tranzitnye-dokumenty/182",
    ),
    ("lt-road-crossings", "https://pkpd.lrv.lt/en/borders-queues-info/"),
    (
        "lt-medininkai",
        "https://vsat.lrv.lt/lt/naujienos/atidaromi-salcininku-ir-medininku-pasienio-kontroles-punktai/",
    ),
    ("ru-chernyshevskoye", "https://www.rosgranstroy.ru/checkpoints/360"),
    ("lt-russian-plates", "https://www.gov.uk/foreign-travel-advice/lithuania/entry-requirements"),
)
IDS = {
    "moscow": ("moscow", "region-ru-mow"),
    "kaliningrad": ("kaliningrad", "region-ru-kgd"),
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


def published_bus_rows(source: dict, start: datetime):
    """Read dated observations from a reviewed terminal-to-terminal schedule."""
    rows = {}
    for entry in source["departures"]:
        depart = datetime.fromisoformat(entry["departure"])
        arrive = datetime.fromisoformat(entry["arrival"])
        if depart.utcoffset() is None or arrive.utcoffset() is None:
            raise ValueError("Bus schedule must include local UTC offsets")
        if depart < start or depart >= start + timedelta(days=8) or arrive <= depart:
            raise ValueError("Invalid dated bus departure")
        key = (entry["number"], depart)
        if key in rows:
            raise ValueError("Duplicate published bus execution")
        rows[key] = (depart, arrive, source["observed_at"], entry["record_id"])
    return rows


def route_costs(road: Path, route: str, *, transit: bool, to_kaliningrad: bool):
    parts = TRANSIT_ROUTE_PARTS.get(route, (route,)) if transit else (route,)
    receipts = [json.loads((road / f"{part}.json").read_text()) for part in parts]
    status = receipts[0]["status"]
    ref_options = {"speed_types": ["freeflow"]}
    ref_seconds = path_seconds = 0.0
    boundaries = []
    for receipt in receipts:
        request = receipt["reference_request"]
        locations = request["locations"]
        coords = [(p["lon"], p["lat"]) for p in locations]
        if (
            receipt["status"] != status
            or locations != receipt["observation_request"]["locations"]
            or request["costing"] != "auto"
            or receipt["observation_request"]["costing"] != "auto"
            or receipt["observation_request"].get("costing_options", {})
            or request["costing_options"]["auto"] != ref_options
        ):
            raise ValueError("Road observation and reference build, mode or boundaries differ")
        border = len(coords) == 4
        if border:
            ordered = (MEDININKAI, KYBARTAI) if to_kaliningrad else (KYBARTAI, MEDININKAI)
            if not transit or tuple(coords[1:-1]) != ordered:
                raise ValueError("Required border corridor does not match")
        elif len(coords) != 2:
            raise ValueError("Unsupported route boundary")
        if boundaries and boundaries[-1][-1] != locations[0]:
            raise ValueError("Disconnected route at the intermediate point")
        boundaries.append(locations)
        for key in ("reference", "observation"):
            trip = receipt[key]["trip"]
            summary = trip["summary"]
            if (
                [(p["lon"], p["lat"]) for p in trip["locations"]] != coords
                or summary["time"] <= 0
                or summary["has_ferry"]
                or summary["has_time_restrictions"]
            ):
                raise ValueError("Route is outside the supported static scope")
            if border:
                if len(trip["legs"]) != 3 or not 0 < trip["legs"][1]["summary"]["time"] < 24 * 3600:
                    raise ValueError("Lithuanian driving segment exceeds the FTD transit window")
            if key == "reference":
                ref_seconds += summary["time"]
            else:
                path_seconds += summary["time"]
    if transit and sum(len(points) == 4 for points in boundaries) != 1:
        raise ValueError("One confirmed border corridor is required")
    return receipts, [boundaries[0][0], boundaries[-1][-1]], ref_seconds, path_seconds


def prepare(
    base: Path,
    road: Path,
    captures: Path,
    out: Path,
    day: date,
    *,
    transit: bool = False,
    bus_corridors: Path | None = None,
):
    if transit and day != REVIEWED_TRANSIT_DAY:
        raise ValueError("Transit rules must be reviewed for the selected date")
    manifest = json.loads((base / "manifest.json").read_text())
    template = next(p for p in manifest["profiles"] if p["analysis_kind"] == "intercity_passenger")
    snapshot = next(r for r in manifest["snapshots"] if r["snapshot_id"] == template["snapshot_id"])
    parent = json.loads((base / snapshot["path"]).read_text())
    copytree(base, out)
    published = json.loads(bus_corridors.read_text()) if bus_corridors else {}
    pairs = (
        tuple(
            (row["level"], row["from"], row["to"], route, True) for route, row in published.items()
        )
        if bus_corridors
        else TRANSIT_PAIRS
        if transit
        else PAIRS
    )
    for level, left, right, route, bus in pairs:
        zone = ZoneInfo("Europe/Kaliningrad") if left == "kaliningrad" else ZONE
        start = datetime.combine(day, time.min, zone)
        end = start + timedelta(days=1)
        receipts, locations, ref_seconds, path_seconds = route_costs(
            road,
            route,
            transit=transit,
            to_kaliningrad=right == "kaliningrad",
        )
        receipt = receipts[0]
        ref_options = {"speed_types": ["freeflow"]}
        source = published.get(route)
        dated = (
            published_bus_rows(source, start)
            if source
            else bus_rows(captures, start)
            if bus
            else {}
        )
        if bus and not dated:
            raise ValueError("Dated bus departures required")
        digest = canonical_hash(
            [
                level,
                route,
                day.isoformat(),
                receipts,
                TRANSIT_SOURCES if transit else [],
                *([source] if source else []),
                [
                    (d.isoformat(), a.isoformat(), observed, uid)
                    for d, a, observed, uid in dated.values()
                ],
            ]
        )
        sid = "rasp-road-" + digest[:24]
        proof, schedule_proof = sid + "-freeflow", sid + "-bus"
        refs = (proof, schedule_proof) if bus else (proof,)
        transit_refs = tuple(sid + "-" + source for source, _ in TRANSIT_SOURCES) if transit else ()
        refs += transit_refs
        nodes = (
            tuple(source["nodes"])
            if source
            else BUS_STATIONS
            if bus
            else (
                f"road-center-{left}",
                f"road-center-{right}",
            )
        )
        if source and (
            len(nodes) != 2
            or nodes[0] == nodes[1]
            or [(point["lon"], point["lat"]) for point in locations]
            != [tuple(point) for point in source["coordinates"]]
        ):
            raise ValueError("Bus stops and road reference endpoints must match")
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
            "bounded-ftd-road-movement" if transit else "bounded-domestic-road",
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
            ref_seconds,
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
            local_day = depart.astimezone(zone).date()
            midnight = datetime.combine(local_day, time.min, zone)
            trip_id = "bus-" + canonical_hash([number, depart.isoformat(), nodes])[:24]
            events = (
                TimetableEvent(0, nodes[0], depart, depart, True, False, (schedule_proof,)),
                TimetableEvent(1, nodes[1], arrive, arrive, False, True, (schedule_proof,)),
            )
            run = TimetableRun(
                sid,
                source["source_id"] if source else "yandex-rasp",
                trip_id,
                local_day,
                str(zone),
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
                    timezone=str(zone),
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
                str(zone),
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
                else ("FTD_TRANSIT_ROAD_MOVEMENT_ONLY", "ROAD_CHAIN_VIA_MOSCOW")
                if transit and route in TRANSIT_ROUTE_PARTS
                else ("FTD_TRANSIT_ROAD_MOVEMENT_ONLY",)
                if transit
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
                path_seconds,
                False,
                refs,
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
                            source["source_id"] if source else "yandex-rasp",
                            source["url"] if source else "https://rasp.yandex.ru/",
                            max(row[2] for row in dated.values()),
                            source["license"]
                            if source
                            else "Данные предоставлены сервисом Яндекс.Расписания",
                        )
                    ]
                    if bus
                    else []
                ),
            ]
        ]
        if transit:
            for (source, url), eid in zip(TRANSIT_SOURCES, transit_refs, strict=True):
                evidence.append(
                    dict(
                        evidence_id=eid,
                        source_id=source,
                        source_version=digest,
                        url=url,
                        retrieved_at=receipt["observed_at"],
                        content_hash=digest,
                        valid_period=valid,
                        license="Public official transit information",
                        verified=True,
                    )
                )
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
        input_document = doc.model_dump(mode="json")
        if source:
            input_document["inputs"]["timetable"]["scope"]["node_ids"].sort()
        manifest["profiles"].append(
            profile.model_dump(mode="json")
            | {
                "inputs": write(out, sid + "-inputs.json", input_document),
            }
        )
        print(level, route, "bus" if bus else "car")
    # Keep other modes and territory levels, replacing only matching old diagnostic car profiles.
    city_directions = {(a, b) for level, a, b, _, bus in pairs if level == "city" and not bus}
    manifest["profiles"] = [
        profile
        for profile in manifest["profiles"]
        if profile["profile_id"].startswith("rasp-road-")
        or not (
            profile["analysis_kind"] == "intercity_passenger"
            and profile["static_observations"]
            and (profile["origin_id"], profile["destination_id"]) in city_directions
            and profile["territory_level"] == "city"
        )
    ]
    write(out, "manifest.json", Manifest.model_validate(manifest).model_dump(mode="json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("base", "road", "captures", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--kaliningrad-transit", action="store_true")
    modes.add_argument("--bus-corridors", type=Path)
    args = parser.parse_args()
    prepare(
        args.base,
        args.road,
        args.captures,
        args.out,
        args.day,
        transit=args.kaliningrad_transit,
        bus_corridors=args.bus_corridors,
    )
