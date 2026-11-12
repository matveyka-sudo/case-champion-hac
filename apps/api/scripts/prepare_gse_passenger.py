"""Prepare bounded direct passenger analyses from dated operator timetables.

Reference movement is calibrated per adjacent station pair. Scheduled dwell
minima are an explicit bounded-model assumption, not universal technical minima.
Only through travel on the selected train is admitted; no intermediate transfers.
"""

import argparse
import hashlib
import html
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
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
    analyze_intercity_passenger,
    build_reference,
    load_intercity_passenger_rules,
    load_passenger_config,
)
from a2_engine.intercity.analysis.models import PhysicalDeparture
from a2_engine.routing.reference.models import ReferenceOperation
from assemble_compact_runtime import write

ZONE = ZoneInfo("Europe/Moscow")
START = datetime(2026, 11, 16, tzinfo=ZONE)
END = datetime(2026, 11, 18, tzinfo=ZONE)


def stations(raw: bytes) -> list[tuple[str, int, int]]:
    rows = []
    previous = 0
    for block in re.split(r'<div class="way_station(?: date)?">', raw.decode())[1:]:
        name = re.search(r'<div class="name">(.*?)</div>', block, re.S)
        if name is None:
            continue
        clocks = []
        for kind in ("arrival", "departure"):
            match = re.search(r'<div class="' + kind + r'">(.*?)</div>', block, re.S)
            value = match[1].strip() if match else ""
            clocks.append(value)
        clocks = [clocks[0] or clocks[1], clocks[1] or clocks[0]]
        seconds = []
        for value in clocks:
            if not re.fullmatch(r"\d{2}:\d{2}", value):
                raise ValueError("Missing operator event")
            hour, minute = map(int, value.split(":"))
            current = hour * 3600 + minute * 60
            while current < previous:
                current += 86400
            seconds.append(current)
            previous = current
        rows.append((html.unescape(name[1].strip()), *seconds))
    if len(rows) < 3:
        raise ValueError("Full operator route required")
    return rows


def node(name: str) -> str:
    return "gse-station-" + hashlib.sha256(name.encode()).hexdigest()[:16]


def prepare(base: Path, captures: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Output must be a new directory")
    records = json.loads((captures / "capture.json").read_text())
    manifest = json.loads((base / "manifest.json").read_text())
    original = next(p for p in manifest["profiles"] if p["analysis_kind"] == "intercity_passenger")
    original_ref = next(
        r for r in manifest["snapshots"] if r["snapshot_id"] == original["snapshot_id"]
    )
    parent = json.loads((base / original_ref["path"]).read_text())
    directory = json.loads(
        Path("data/directories/russia-intercity-nodes-2026-11-09.2/nodes.json").read_text()
    )
    endpoints = {n["node_id"]: n for n in directory}
    copytree(base, output)
    for train, origin, destination, left, right in (
        ("172М", "moscow", "novorossiysk", "node-wd-q21844902", "node-wd-q3728238"),
        ("172С", "novorossiysk", "moscow", "node-wd-q3728238", "node-wd-q21844902"),
    ):
        selected = [r for r in records if r["train"] == train]
        expected_days = {(START + timedelta(days=i)).date().isoformat() for i in range(9)}
        if len(selected) != 9 or {record["day"] for record in selected} != expected_days:
            raise ValueError("Nine explicitly dated observations required")
        version = canonical_hash(selected)
        sid = "gse-" + version[:24]
        proof = "gse-source-" + version[:24]
        refs = (proof,)
        parsed = []
        for record in selected:
            raw = (captures / record["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("Capture hash mismatch")
            calendar = (captures / record["calendar_file"]).read_bytes()
            if hashlib.sha256(calendar).hexdigest() != record["calendar_sha256"]:
                raise ValueError("Calendar hash mismatch")
            dates = re.search(rb"ALL_COURSE_DATES:\s*(\[[^]]+\])", calendar)
            day = datetime.fromisoformat(record["day"]).replace(tzinfo=ZONE)
            if dates is None or int(day.timestamp() * 1000) not in json.loads(dates[1]):
                raise ValueError("Service date absent from operator calendar")
            parsed.append((record, stations(raw)))
        names = [r[0] for r in parsed[0][1]]
        if any([r[0] for r in rows] != names for _, rows in parsed):
            raise ValueError("Different station sequences require separate corridors")
        first, last = node(names[0]), node(names[-1])
        network = ReferenceNetwork(
            sid,
            sid,
            sid,
            "selected-gse-corridor",
            sid,
            "a2-methodology-v3",
            "edge-calibrated-v1",
            refs,
        )
        scope = ReferenceScope(
            network, origin, destination, (first,), (last,), ("rail",), "exact_node_pair"
        )
        states, edges, operations = [], [], []
        for i, name in enumerate(names):
            ident = node(name)
            states.extend(
                (
                    ReferenceState(ident + ":arr", ident, "arrival", "rail", train),
                    ReferenceState(ident + ":dep", ident, "departure", "rail", train),
                )
            )
            if 0 < i < len(names) - 1:
                dwell = min(rows[i][2] - rows[i][1] for _, rows in parsed)
                operations.append(
                    ReferenceOperation(
                        ident + ":dwell",
                        ident + ":arr",
                        ident + ":dep",
                        network,
                        dwell,
                        "observed_scheduled_dwell_minimum",
                        refs,
                        compatible=True,
                    )
                )
            if i:
                before = node(names[i - 1]) + ":dep"
                after = ident + ":arr"
                edge = before + "--" + after
                samples = tuple(
                    ReferenceCalibration(
                        edge + ":" + record["day"],
                        edge,
                        before,
                        after,
                        network,
                        rows[i][1] - rows[i - 1][2],
                        "running_time",
                        refs,
                    )
                    for record, rows in parsed
                )
                edges.append(ReferenceEdge(edge, before, after, samples, refs, compatible=True))
        graph = ReferenceGraph(
            network, tuple(states), tuple(edges), tuple(operations), "partial", refs
        )
        runs, services, physical = [], [], []
        for record, rows in parsed:
            day = datetime.fromisoformat(record["day"]).replace(tzinfo=ZONE)
            trip = train + ":" + record["day"]
            # Through-service compression retains all running and dwell time.
            events = (
                TimetableEvent(
                    0,
                    first,
                    day + timedelta(seconds=rows[0][2]),
                    day + timedelta(seconds=rows[0][2]),
                    True,
                    False,
                    refs,
                ),
                TimetableEvent(
                    1,
                    last,
                    day + timedelta(seconds=rows[-1][1]),
                    day + timedelta(seconds=rows[-1][1]),
                    False,
                    True,
                    refs,
                ),
            )
            run = TimetableRun(
                sid, "grandtrain", trip, day.date(), "Europe/Moscow", "train", events, True, refs
            )
            runs.append(run)
            physical.append(
                PhysicalService(
                    trip, (run.run_id,), (PhysicalDeparture(0, trip + ":departure", refs),), refs
                )
            )
            services.append(
                dict(
                    service_id=trip,
                    trip_id=trip,
                    mode="rail",
                    timezone="Europe/Moscow",
                    service_dates=[record["day"]],
                    trip_identity_confirmed=True,
                    source_refs=[proof],
                    stop_times=[
                        dict(
                            node_id=e.node_id,
                            sequence=e.sequence,
                            arrival_seconds=int((e.arrival_at - day).total_seconds()),
                            departure_seconds=int((e.departure_at - day).total_seconds()),
                            pickup=e.pickup,
                            dropoff=e.dropoff,
                        )
                        for e in events
                    ],
                )
            )
        snapshot = dict(parent)
        directory_hash = hashlib.sha256(
            Path("data/directories/russia-intercity-nodes-2026-11-09.2/nodes.json").read_bytes()
        ).hexdigest()
        snapshot.update(
            snapshot_id=sid,
            source_versions=parent["source_versions"]
            | {"grandtrain": sid, "wikidata-stations": directory_hash},
            provenance=dict(
                graph_version=sid,
                calendar_version=sid,
                geometry_version=sid,
                population_version=None,
            ),
            scheduled_services=services,
            access_nodes=[
                dict(
                    node_id=ident,
                    kind="rail_station",
                    territory_id=territory,
                    location=dict(
                        lon=endpoints[original_id]["lon"], lat=endpoints[original_id]["lat"]
                    ),
                    source_refs=[proof, "gse-endpoint-directory"],
                )
                for ident, territory, original_id in (
                    (first, origin, left),
                    (last, destination, right),
                )
            ],
            valid_period=dict(start="2026-11-16", end="2026-11-26"),
            qa=dict(
                acceptance_state="accepted",
                accepted_at=selected[-1]["retrieved_at"],
                accepted_by="Waydex coordinator: dated official direct GSE services",
                checks=[
                    "dated_operator_capture_hash",
                    "station_sequence",
                    "calendar_dates",
                    "through_train_identity",
                    "per_edge_reference_calibration",
                    "bounded_scheduled_dwell_assumption",
                ],
            ),
        )
        snapshot["evidence"] = parent["evidence"] + [
            dict(
                evidence_id="gse-endpoint-directory",
                source_id="wikidata-stations",
                source_version=directory_hash,
                url="https://www.wikidata.org/",
                retrieved_at=selected[-1]["retrieved_at"],
                content_hash=directory_hash,
                license="CC0-1.0",
                valid_period=snapshot["valid_period"],
                verified=True,
            ),
            dict(
                evidence_id=proof,
                source_id="grandtrain",
                source_version=sid,
                url=selected[0]["url"],
                retrieved_at=selected[-1]["retrieved_at"],
                content_hash=version,
                license="Official published timetable facts; attribution retained",
                valid_period=snapshot["valid_period"],
                verified=True,
            ),
        ]
        accepted = AcceptedData.model_validate(snapshot)
        table = Timetable(
            TimetableScope(
                sid,
                sid,
                sid,
                sid,
                "not-collected",
                sid,
                "Europe/Moscow",
                START,
                max(r.events[-1].arrival_at for r in runs),
                frozenset((first, last)),
                False,
                False,
                False,
                False,
                False,
                refs,
                (
                    "SELECTED_GSE_DIRECT_CORRIDOR_ONLY",
                    "SCHEDULED_DWELL_MINIMUM_ASSUMPTION",
                    "OTHER_MODES_AND_TRANSFERS_NOT_COVERED",
                ),
            ),
            tuple(runs),
            (),
            (),
        )
        inputs = IntercityInputs(
            kind="intercity_passenger",
            scope=IntercityAnalysisScope(scope, ("rail",), False, refs),
            timetable=table,
            timetable_service_clock="local_service_midnight_v1",
            transport_modes=(TransportModeBinding("train", "rail", refs),),
            physical_services=tuple(physical),
            reference_graph=graph,
            reference_scope=scope,
            frequency_start_at=START,
            frequency_end_at=START + timedelta(days=7),
        )
        profile = Profile.model_validate(
            original
            | dict(
                profile_id=sid,
                origin_id=origin,
                destination_id=destination,
                snapshot_id=sid,
                snapshot_hash=canonical_hash(accepted.model_dump(mode="json")),
                valid_from=START.isoformat(),
                valid_to=END.isoformat(),
                static_observations=False,
                max_route_pairs=20,
            )
        )
        registry = ProductRegistry(snapshots=[accepted])
        fingerprints = input_fingerprints(profile, inputs, registry)
        profile = profile.model_copy(
            update=dict(
                graph_fingerprint=fingerprints[0], routing_profile_fingerprint=fingerprints[1]
            )
        )
        validate_inputs(profile, inputs, accepted, registry)
        reference = build_reference(graph, scope)
        for day_offset in (0, 1):
            for hour in (8, 13, 18, 23):
                result = analyze_intercity_passenger(
                    inputs.scope,
                    START + timedelta(days=day_offset, hours=hour),
                    table,
                    inputs.transport_modes,
                    inputs.physical_services,
                    reference,
                    config=load_passenger_config(),
                    rules=load_intercity_passenger_rules(),
                    frequency_start_at=inputs.frequency_start_at,
                    frequency_end_at=inputs.frequency_end_at,
                )
                if result.public.best_known is None:
                    raise ValueError("No calculated passenger index for selected departure")
        snapshot_ref = write(output, sid + "-snapshot.json", accepted.model_dump(mode="json"))
        manifest["snapshots"].append(
            snapshot_ref | dict(snapshot_id=sid, content_hash=profile.snapshot_hash)
        )
        input_ref = write(
            output,
            sid + "-inputs.json",
            InputDocument(schema_version="waydex-engine-inputs/1", inputs=inputs).model_dump(
                mode="json"
            ),
        )
        manifest["profiles"].append(profile.model_dump(mode="json") | dict(inputs=input_ref))
        write(
            output,
            sid + "-sources.json",
            dict(
                records=selected,
                station_sequence=names,
                limitations=list(table.scope.limitations),
                reference=asdict(graph),
            ),
        )
    checked = Manifest.model_validate(manifest)
    print(write(output, "manifest.json", checked.model_dump(mode="json")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.base, args.captures, args.output)
