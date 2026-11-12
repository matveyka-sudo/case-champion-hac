"""Build bounded profiles from dated rail threads or direct Aeroflot flights."""

import argparse
import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
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
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
    TransportModeBinding,
)
from a2_engine.intercity.analysis.models import CapitalBoundary, PhysicalDeparture
from a2_engine.routing.reference.models import ReferenceOperation
from a2_engine.routing.timetable_models import TimetableFootpath, TimetableTransfer
from assemble_compact_runtime import write

REGIONS = {
    "moscow": "region-ru-mow",
    "kaliningrad": "region-ru-kgd",
    "krasnodar": "region-ru-kda",
}
CITY_IDS = {"krasnodar": "city-q3646"}
NODES = {
    "s2000006": ("moscow", "node-wd-q2522328"),
    "s2000003": ("moscow", "node-wd-q21844902"),
    "s9623137": ("kaliningrad", "node-wd-q800979"),
    "s9613022": ("novorossiysk", "node-wd-q3728238"),
    "s9613602": ("krasnodar", "node-wd-q4238511"),
}
AIRPORTS = {
    "moscow": ("s9600213", "node-wd-q4523105"),
    "kaliningrad": ("s9631119", "node-wd-q1431650"),
    "krasnodar": ("s9623123", "node-wd-q1629051"),
}
CHECKIN_SECONDS = 40 * 60
CHECKIN_SOURCE = "https://www.aeroflot.ru/info/checkin/"
SVO_TRANSFER_SOURCE = "https://www.aeroflot.ru/info/transit/"
# Separate tickets in terminal B, with a conservative airport reprocessing buffer.
SVO_TRANSFER_SECONDS = 2 * 3600
DIRECTORY = Path("data/directories/russia-intercity-nodes-2026-11-09.2/nodes.json")
# Allow time for navigation, security and boarding beyond the routed exterior walk.
# This is a conservative planning assumption, not a carrier-guaranteed connection.
STATION_BUFFER_SECONDS = 30 * 60


def parse(record):
    row, thread = record["row"], record["thread"]
    stops = thread["stops"]
    codes = [s["station"]["code"] for s in stops]
    start, end = codes.index(row["from"]["code"]), codes.index(row["to"]["code"])
    departure = datetime.fromisoformat(row["departure"])
    arrival = datetime.fromisoformat(row["arrival"])
    selected = stops[start : end + 1]
    running, dwelling = [], []
    for i, stop in enumerate(selected[1:], 1):
        dwell = stop["stop_time"]
        if dwell is None:
            if i != len(selected) - 1 or stop["departure"] is not None:
                raise ValueError("Missing scheduled dwell")
            dwell = 0
        running.append(stop["duration"] - dwell - selected[i - 1]["duration"])
        if i < len(selected) - 1:
            dwelling.append(dwell)
    if (
        not running
        or min(running) <= 0
        or sum(running) + sum(dwelling) != (arrival - departure).total_seconds()
        or thread["uid"] != row["thread"]["uid"]
        or thread["start_date"] != row["start_date"]
    ):
        raise ValueError("Thread and dated segment disagree")
    return codes[start : end + 1], running, dwelling, departure, arrival


def flight_observations(capture: Path):
    observations = defaultdict(list)
    for path in sorted(capture.glob("20??-??-??-*.json")):
        record = json.loads(path.read_text())
        left, right = record["origin"], record["destination"]
        if left not in AIRPORTS or right not in AIRPORTS or "moscow" not in (left, right):
            continue
        a, b = AIRPORTS[left][0], AIRPORTS[right][0]
        by_number = defaultdict(list)
        for page in record["pages"]:
            for row in page["segments"]:
                thread = row.get("thread", {})
                number = thread.get("number", "")
                if (
                    row.get("has_transfers") is not False
                    or thread.get("transport_type") != "plane"
                    or thread.get("carrier", {}).get("title") != "Аэрофлот"
                    or not number.startswith("SU ")
                    or (row["from"]["code"], row["to"]["code"]) != (a, b)
                ):
                    continue
                depart = datetime.fromisoformat(row["departure"])
                arrive = datetime.fromisoformat(row["arrival"])
                duration = (arrive - depart).total_seconds()
                if (
                    depart.utcoffset() is None
                    or arrive.utcoffset() is None
                    or duration <= 0
                    or duration != row["duration"]
                ):
                    continue
                by_number[number].append((row, ([a, b], [duration], [], depart, arrive)))
        for rows in by_number.values():
            if len({tuple(parsed[-2:]) for _, parsed in rows}) != 1:
                continue
            row, parsed = rows[0]
            source = {k: record[k] for k in ("origin", "destination", "date", "observed_at")}
            observations[left, right].append((source | {"row": row}, parsed))
    return observations


def rail_observations(capture: Path):
    observations = defaultdict(list)
    for path in sorted(capture.glob("thread-*.json")):
        record = json.loads(path.read_text())
        record.setdefault(
            "observed_at", datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        )
        try:
            parsed = parse(record)
        except (ValueError, KeyError, TypeError):
            print("Rejected inconsistent thread:", path.name)
            continue
        observations[record["origin"], record["destination"]].append((record, parsed))
    return observations


def prepare(
    base: Path,
    capture: Path,
    out: Path,
    day: date,
    *,
    air: bool = False,
    mixed: bool = False,
    aviation: bool = False,
    regional_air_connections: bool = False,
    road: Path | None = None,
):
    mixed = mixed or aviation
    air = air or regional_air_connections
    manifest = json.loads((base / "manifest.json").read_text())
    template = next(p for p in manifest["profiles"] if p["analysis_kind"] == "intercity_passenger")
    parent_ref = next(
        r for r in manifest["snapshots"] if r["snapshot_id"] == template["snapshot_id"]
    )
    parent = json.loads((base / parent_ref["path"]).read_text())
    directory = {r["node_id"]: r for r in json.loads(DIRECTORY.read_text())}
    # Match the published station by name where the directory has multiple Moscow nodes.
    bel = next(r for r in directory.values() if r["name"] == "Белорусский вокзал")
    walk = {} if air else json.loads((capture / "station-walk.json").read_text())
    mode = "air" if air else "rail"
    observations = flight_observations(capture) if air else rail_observations(capture)
    if mixed:
        if road is None:
            raise ValueError("Ground transfer captures are required")
        flights = flight_observations(capture)
        walk = {
            "directions": {
                key: json.loads((road / filename).read_text())
                for key, filename in (
                    ("s9623123-s9613602", "krr-krasnodar.json"),
                    ("s9613602-s9623123", "krasnodar-krr.json"),
                )
            }
        }
        for proof in walk["directions"].values():
            if proof["reference_request"]["costing"] != "pedestrian":
                raise ValueError("A routed pedestrian transfer is required")
    node_directory = (
        {code: (city, ident) for city, (code, ident) in AIRPORTS.items()} if air else NODES
    )
    if mixed:
        node_directory = NODES | {code: (city, ident) for city, (code, ident) in AIRPORTS.items()}
    copytree(base, out)
    scopes = (
        ("city", (("moscow", "kaliningrad"), ("kaliningrad", "novorossiysk"))),
        (
            "region",
            (("moscow", "kaliningrad"), ("moscow", "krasnodar"), ("kaliningrad", "krasnodar")),
        ),
    )
    if mixed:
        scopes = (("city", (("moscow", "novorossiysk"),)),)
    if aviation:
        scopes = (("city", (("moscow", "novorossiysk"), ("kaliningrad", "novorossiysk"))),)
    if regional_air_connections:
        scopes = (("region", (("kaliningrad", "krasnodar"),)),)
    for level, pairs in scopes:
        for pair in pairs:
            if (
                air
                and not regional_air_connections
                and ("moscow" not in pair or "novorossiysk" in pair)
            ):
                continue
            for left, right in (pair, pair[::-1]):
                chain = (
                    [(left, right)] if "moscow" in pair else [(left, "moscow"), ("moscow", right)]
                )
                if mixed:
                    chain = [(left, "krasnodar"), ("krasnodar", right)]
                    leg_modes = ("rail", "air") if left == "novorossiysk" else ("air", "rail")
                    if aviation and "kaliningrad" in pair:
                        if left == "kaliningrad":
                            chain = [
                                (left, "moscow"),
                                ("moscow", "krasnodar"),
                                ("krasnodar", right),
                            ]
                            leg_modes = ("air", "air", "rail")
                        else:
                            chain = [
                                (left, "krasnodar"),
                                ("krasnodar", "moscow"),
                                ("moscow", right),
                            ]
                            leg_modes = ("rail", "air", "air")
                    groups = [
                        (flights if leg_mode == "air" else observations)[leg]
                        for leg, leg_mode in zip(chain, leg_modes, strict=True)
                    ]
                else:
                    leg_modes = (mode,) * len(chain)
                    groups = [observations[leg] for leg in chain]
                svo_connection = regional_air_connections or (aviation and len(chain) == 3)
                if svo_connection:
                    # Accept only explicitly dated movements within the same SVO terminal.
                    for i, (leg, leg_mode) in enumerate(zip(chain, leg_modes, strict=True)):
                        if leg_mode != "air":
                            continue
                        terminal = (
                            "departure_terminal" if leg[0] == "moscow" else "arrival_terminal"
                        )
                        groups[i] = [(r, p) for r, p in groups[i] if r["row"].get(terminal) == "B"]
                allowed_modes = tuple(sorted(set(leg_modes)))
                starts_at_airport = leg_modes[0] == "air"
                if not all(groups):
                    print("Missing scheduled legs:", level, left, right)
                    continue
                source = [record for group in groups for record, _ in group]
                digest = canonical_hash(
                    [
                        "waydex-rasp-rail/2",
                        level,
                        left,
                        right,
                        day.isoformat(),
                        source,
                        *(
                            [{"aviation": True, "svo_transfer": SVO_TRANSFER_SECONDS}]
                            if aviation
                            else []
                        ),
                        {"checkin": CHECKIN_SECONDS, "source": CHECKIN_SOURCE} if air else walk,
                    ]
                )
                sid = (
                    f"rasp-{'aviation' if aviation else 'mixed' if mixed else mode}-" + digest[:24]
                )
                proof, walk_proof = sid + "-schedule", sid + "-walk"
                refs = (
                    (proof, walk_proof)
                    if len(chain) > 1 and not regional_air_connections
                    else (proof,)
                )
                if air or mixed:
                    refs += (sid + "-checkin",)
                if svo_connection:
                    refs += (sid + "-svo-transfer",)
                orig, dest = (REGIONS[left], REGIONS[right]) if level == "region" else (left, right)
                zone = ZoneInfo("Europe/Kaliningrad" if left == "kaliningrad" else "Europe/Moscow")
                start = datetime.combine(day, time.min, zone)
                end = start + timedelta(days=1)
                network = ReferenceNetwork(
                    sid,
                    sid,
                    sid,
                    "air-via-svo"
                    if regional_air_connections
                    else "direct-air"
                    if air
                    else "rail-and-station-walk",
                    sid,
                    "a2-methodology-v3",
                    "edge-calibrated-v1",
                    refs,
                )
                states, edges, ops, runs, identities, services = [], [], [], [], [], []
                nodes = {}
                ends = []
                for i, group in enumerate(groups):
                    leg_mode = leg_modes[i]
                    leg_transport = "plane" if leg_mode == "air" else "train"
                    patterns = defaultdict(list)
                    for record, parsed in group:
                        patterns[tuple(parsed[0])].append((record, parsed))
                    endpoints = {(p[0], p[-1]) for p in patterns}
                    if len(endpoints) != 1:
                        raise ValueError("Distinct physical termini in one selected leg")
                    a, b = next(iter(endpoints))
                    ends.append((a, b))
                    for code in (a, b):
                        city, directory_id = node_directory[code]
                        loc = bel if code == "s2000006" else directory[directory_id]
                        nodes[code] = dict(
                            node_id=code,
                            kind="airport" if leg_mode == "air" else "rail_station",
                            territory_id=CITY_IDS.get(city, city),
                            location=dict(lon=loc["lon"], lat=loc["lat"]),
                            source_refs=[proof],
                        )
                    for j, (pattern, rows) in enumerate(patterns.items()):
                        tag = f"leg{i}-pattern{j}"
                        for k, code in enumerate(pattern):
                            for phase in ("arrival", "departure"):
                                states.append(
                                    ReferenceState(f"{tag}-{k}-{phase}", code, phase, leg_mode, tag)
                                )
                            if 0 < k < len(pattern) - 1:
                                ops.append(
                                    ReferenceOperation(
                                        f"{tag}-{k}-dwell",
                                        f"{tag}-{k}-arrival",
                                        f"{tag}-{k}-departure",
                                        network,
                                        min(p[2][k - 1] for _, p in rows),
                                        "observed_scheduled_dwell_minimum",
                                        (proof,),
                                        compatible=True,
                                    )
                                )
                            if k:
                                before, after = f"{tag}-{k - 1}-departure", f"{tag}-{k}-arrival"
                                key = f"{tag}-{k}-running"
                                samples = tuple(
                                    ReferenceCalibration(
                                        f"{key}-{n}",
                                        key,
                                        before,
                                        after,
                                        network,
                                        p[1][k - 1],
                                        "running_time",
                                        (proof,),
                                    )
                                    for n, (_, p) in enumerate(rows)
                                )
                                edges.append(
                                    ReferenceEdge(
                                        key, before, after, samples, (proof,), compatible=True
                                    )
                                )
                    seen = set()
                    for record, (_, _, _, depart, arrive) in group:
                        number = record["row"]["thread"]["number"]
                        if (number, depart) in seen:
                            continue
                        seen.add((number, depart))
                        timezone = (
                            "Europe/Kaliningrad"
                            if node_directory[a][0] == "kaliningrad"
                            else "Europe/Moscow"
                        )
                        local = depart.astimezone(ZoneInfo(timezone))
                        midnight = datetime.combine(local.date(), time.min, local.tzinfo)
                        trip = f"{leg_transport}-{number.replace(' ', '-')}-{local.date()}-{a}"
                        events = (
                            TimetableEvent(0, a, depart, depart, True, False, (proof,)),
                            TimetableEvent(1, b, arrive, arrive, False, True, (proof,)),
                        )
                        run = TimetableRun(
                            sid,
                            "yandex-rasp",
                            trip,
                            local.date(),
                            timezone,
                            leg_transport,
                            events,
                            True,
                            (proof,),
                        )
                        runs.append(run)
                        identities.append(
                            PhysicalService(
                                trip,
                                (run.run_id,),
                                (PhysicalDeparture(0, trip + "-departure", (proof,)),),
                                (proof,),
                            )
                        )
                        services.append(
                            dict(
                                service_id=trip,
                                trip_id=trip,
                                mode=leg_mode,
                                timezone=timezone,
                                service_dates=[local.date().isoformat()],
                                trip_identity_confirmed=True,
                                source_refs=[proof],
                                stop_times=[
                                    dict(
                                        node_id=e.node_id,
                                        sequence=e.sequence,
                                        arrival_seconds=int(
                                            (e.arrival_at - midnight).total_seconds()
                                        ),
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
                footpaths, transfers = [], []
                for i, ((_, a), (b, _)) in enumerate(zip(ends, ends[1:])):
                    prefix = "station" if mixed else "moscow"
                    suffix = f"-{i}" if aviation else ""
                    if a == b:
                        if not svo_connection or a != AIRPORTS["moscow"][0]:
                            raise ValueError("Unreviewed same-terminal connection")
                        minimum = SVO_TRANSFER_SECONDS
                        transfer_proof = sid + "-svo-transfer"
                        operation = "same_terminal_reprocessing_allowance"
                    else:
                        walk_record = walk["directions"][f"{a}-{b}"]
                        walk_trip = walk_record["reference" if mixed else "response"]["trip"]
                        walk_seconds = walk_trip["summary"]["time"]
                        allowance = (
                            CHECKIN_SECONDS
                            if mixed and leg_modes[i + 1] == "air"
                            else STATION_BUFFER_SECONDS
                        )
                        minimum = walk_seconds + allowance
                        transfer_proof = walk_proof
                        operation = "routed_walk_and_boarding_allowance"
                        footpaths.append(
                            TimetableFootpath(
                                f"{prefix}-walk{suffix}",
                                a,
                                b,
                                walk_seconds,
                                True,
                                (walk_proof,),
                            )
                        )
                    transfers.append(
                        TimetableTransfer(
                            f"{prefix}-transfer{suffix}",
                            a,
                            b,
                            minimum,
                            True,
                            (transfer_proof,),
                        )
                    )
                    for pre in states:
                        for post in states:
                            if (pre.node_id, pre.phase, post.node_id, post.phase) == (
                                a,
                                "arrival",
                                b,
                                "departure",
                            ) and (
                                not (aviation or regional_air_connections)
                                or pre.state_id.startswith(f"leg{i}-")
                                and post.state_id.startswith(f"leg{i + 1}-")
                            ):
                                ops.append(
                                    ReferenceOperation(
                                        f"{pre.state_id}-to-{post.state_id}",
                                        pre.state_id,
                                        post.state_id,
                                        network,
                                        minimum,
                                        operation,
                                        (transfer_proof,),
                                        compatible=True,
                                    )
                                )
                scope = ReferenceScope(
                    network,
                    orig,
                    dest,
                    (ends[0][0],),
                    (ends[-1][1],),
                    allowed_modes,
                    "exact_node_pair",
                )
                capitals = (
                    tuple(
                        CapitalBoundary(REGIONS[city], CITY_IDS.get(city, city), (node,), refs)
                        for city, node in ((left, ends[0][0]), (right, ends[-1][1]))
                    )
                    if level == "region"
                    else ()
                )
                table_end = max(end + timedelta(days=7), *(r.events[-1].arrival_at for r in runs))
                table = Timetable(
                    TimetableScope(
                        sid,
                        sid,
                        sid,
                        sid,
                        "routed-station-walk",
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
                        ("REGIONAL_AIR_VIA_SVO", "SCHEDULED_BLOCK_TIME_REFERENCE")
                        if regional_air_connections
                        else ("SELECTED_DIRECT_AEROFLOT_FLIGHTS", "SCHEDULED_BLOCK_TIME_REFERENCE")
                        if air
                        else (
                            "SELECTED_AIR_RAIL_VIA_KRASNODAR",
                            "ROUTED_WALK_BETWEEN_AIRPORT_AND_STATION",
                            *(("AVIATION_WITH_GROUND_ACCESS",) if aviation else ()),
                            *(("SVO_TERMINAL_B_TRANSFER_ALLOWANCE",) if svo_connection else ()),
                        )
                        if mixed
                        else (
                            "SELECTED_DATED_TRAINS_ONLY",
                            "OBSERVED_SCHEDULED_DWELL_MINIMA",
                            "MOSCOW_STATION_BOARDING_ALLOWANCE"
                            if transfers
                            else "DIRECT_TRAINS_ONLY",
                        ),
                    ),
                    tuple(runs),
                    tuple(footpaths),
                    tuple(transfers),
                )
                valid = dict(start=day.isoformat(), end=table_end.date().isoformat())
                evidence = [
                    dict(
                        evidence_id=eid,
                        source_id=source_id,
                        source_version=digest,
                        url=url,
                        retrieved_at=max(r["observed_at"] for r in source),
                        content_hash=digest,
                        valid_period=valid,
                        license=license,
                        verified=True,
                    )
                    for eid, source_id, url, license in [
                        (
                            proof,
                            "yandex-rasp",
                            "https://rasp.yandex.ru/",
                            "Данные предоставлены сервисом Яндекс.Расписания",
                        ),
                        *(
                            (
                                [
                                    (
                                        walk_proof,
                                        "valhalla",
                                        "https://www.openstreetmap.org/copyright",
                                        "ODbL",
                                    )
                                ]
                            )
                            if footpaths
                            else []
                        ),
                    ]
                ]
                if air or mixed:
                    evidence.append(
                        dict(
                            evidence_id=sid + "-checkin",
                            source_id="aeroflot-checkin",
                            source_version=digest,
                            url=CHECKIN_SOURCE,
                            retrieved_at=max(r["observed_at"] for r in source),
                            content_hash=digest,
                            valid_period=valid,
                            license="Published carrier boarding conditions",
                            verified=True,
                        )
                    )
                if svo_connection:
                    evidence.append(
                        dict(
                            evidence_id=sid + "-svo-transfer",
                            source_id="aeroflot-transit",
                            source_version=digest,
                            url=SVO_TRANSFER_SOURCE,
                            retrieved_at=max(r["observed_at"] for r in source),
                            content_hash=digest,
                            valid_period=valid,
                            license="Carrier terminal guidance; two-hour self-transfer planning allowance",
                            verified=True,
                        )
                    )
                territory_rows = list(parent["territories"])
                required_territories = {
                    orig,
                    dest,
                    *(node["territory_id"] for node in nodes.values()),
                }
                known_territories = {entry["territory_id"] for entry in territory_rows}
                for entry in manifest["territories"]:
                    ident = entry["territory_id"]
                    if ident not in required_territories - known_territories:
                        continue
                    territory_rows.append(
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
                        valid_period=valid,
                        access_nodes=list(nodes.values()),
                        geographic_scope=sorted(required_territories),
                        territories=territory_rows,
                        source_versions=parent["source_versions"]
                        | {item["source_id"]: item["source_version"] for item in evidence},
                        provenance=dict(
                            graph_version=sid,
                            calendar_version=sid,
                            geometry_version=sid,
                            population_version=None,
                        ),
                        scheduled_services=services,
                        evidence=parent["evidence"] + evidence,
                    )
                )
                inputs = IntercityInputs(
                    kind="intercity_passenger",
                    scope=IntercityAnalysisScope(
                        scope,
                        allowed_modes,
                        False,
                        refs,
                        "regions" if capitals else "cities",
                        capitals,
                    ),
                    timetable=table,
                    timetable_service_clock="local_service_midnight_v1",
                    transport_modes=tuple(
                        TransportModeBinding("plane" if m == "air" else "train", m, (proof,))
                        for m in allowed_modes
                    ),
                    initial_airport_checkin_seconds=CHECKIN_SECONDS if starts_at_airport else None,
                    mixed_only=mixed,
                    aviation_with_ground_access=aviation,
                    physical_services=tuple(identities),
                    reference_graph=ReferenceGraph(
                        network, tuple(states), tuple(edges), tuple(ops), "partial", refs
                    ),
                    reference_scope=scope,
                    frequency_start_at=start,
                    frequency_end_at=start + timedelta(days=7),
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
                        static_observations=False,
                        max_route_pairs=20,
                    )
                )
                registry = ProductRegistry(snapshots=[accepted])
                graph_hash, routing_hash = input_fingerprints(profile, inputs, registry)
                profile = profile.model_copy(
                    update=dict(
                        graph_fingerprint=graph_hash, routing_profile_fingerprint=routing_hash
                    )
                )
                validate_inputs(profile, inputs, accepted, registry)
                snapshot_ref = write(out, sid + "-snapshot.json", accepted.model_dump(mode="json"))
                manifest["snapshots"].append(
                    snapshot_ref | dict(snapshot_id=sid, content_hash=profile.snapshot_hash)
                )
                input_document = InputDocument(
                    schema_version="waydex-engine-inputs/1",
                    inputs=inputs,
                ).model_dump(mode="json")
                if aviation:
                    node_ids = input_document["inputs"]["timetable"]["scope"]["node_ids"]
                    node_ids.sort()
                input_ref = write(out, sid + "-inputs.json", input_document)
                manifest["profiles"].append(
                    profile.model_dump(mode="json") | dict(inputs=input_ref)
                )
                print(level, left, right, len(runs), len(transfers))
    write(out, "manifest.json", Manifest.model_validate(manifest).model_dump(mode="json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "capture", "out"):
        cli.add_argument("--" + name, type=Path, required=True)
    cli.add_argument("--day", type=date.fromisoformat, required=True)
    mode_args = cli.add_mutually_exclusive_group()
    mode_args.add_argument("--air", action="store_true")
    mode_args.add_argument("--mixed", action="store_true")
    mode_args.add_argument("--aviation", action="store_true")
    mode_args.add_argument("--regional-air-connections", action="store_true")
    cli.add_argument("--road", type=Path)
    args = cli.parse_args()
    prepare(
        args.base,
        args.capture,
        args.out,
        args.day,
        air=args.air,
        mixed=args.mixed,
        aviation=args.aviation,
        regional_air_connections=args.regional_air_connections,
        road=args.road,
    )
