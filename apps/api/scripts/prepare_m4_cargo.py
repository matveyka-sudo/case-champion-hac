"""Build a conditional, prebooked light-truck delivery scenario for the M4 corridor."""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from shutil import copytree
from zoneinfo import ZoneInfo

from a2_api.adapters.product_inputs import DeliveryInputs, InputDocument
from a2_api.adapters.product_runtime import Manifest, Profile, input_fingerprints, validate_inputs
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import DeliveryCargoProfile
from a2_engine.freight.delivery.models import (
    CargoAction,
    CargoDeliveryScope,
    CargoReadiness,
    CargoState,
    CargoWaitPolicy,
)
from a2_engine.freight.models import (
    CargoCompatibility,
    CargoContext,
    CargoProfile,
    CargoTemperature,
    CargoVehicle,
)
from assemble_compact_runtime import write

ZONE = ZoneInfo("Europe/Moscow")
NODES = {
    "moscow-south-2": (
        "moscow",
        "region-ru-mow",
        (37.644585, 55.599992),
        "https://www.dellin.ru/contacts/297/",
    ),
    "krasnodar-north-3": (
        "city-q3646",
        "region-ru-kda",
        (38.969258, 45.102928),
        "https://www.dellin.ru/contacts/388/",
    ),
}
SOURCE = {
    "direct-truck": "https://dev.dellin.ru/api/ordering/mkt/",
    "handling-benchmark": "https://fastrans.ru/city/moscow_krasnodar/",
}
COMMODITIES = {
    "nonfood": (
        "Паллета запчастей до 500 кг",
        "https://www.dellin.ru/helpcenter/?q=%D0%A3%D0%BF%D0%B0%D0%BA%D0%BE%D0%B2%D0%BA%D0%B0",
    ),
    "food": (
        "Паллета сухих макарон в заводской упаковке до 500 кг",
        "https://www.dellin.ru/amp/helpcenter/cargo-delivery/",
    ),
}
HANDLING = timedelta(minutes=45)
ROAD_BREAKS = timedelta(hours=1)


def opens(node: str, day: date) -> tuple[datetime, datetime]:
    weekday = day.weekday()
    if node == "moscow-south-2":
        hours = [(8, 24), (0, 24), (0, 24), (0, 24), (0, 24), (0, 21), (9, 21)]
    else:
        hours = [(7, 21)] * 5 + [(8, 20)] * 2
    start, end = hours[weekday]
    midnight = datetime.combine(day, time.min, ZONE)
    return midnight + timedelta(hours=start), midnight + timedelta(hours=end)


def handoff_time(node: str, earliest: datetime) -> datetime:
    day = earliest.date()
    while True:
        start, end = opens(node, day)
        at = max(start, earliest)
        if at + HANDLING <= end:
            return at
        day += timedelta(days=1)


def prepare(base: Path, captures: Path, out: Path, day: date) -> None:
    manifest = json.loads((base / "manifest.json").read_text())
    template = next(
        p
        for p in manifest["profiles"]
        if p["analysis_kind"] == "intercity_passenger"
        and p["origin_id"] == "region-ru-mow"
        and p["destination_id"] == "region-ru-kda"
    )
    parent_ref = next(
        r for r in manifest["snapshots"] if r["snapshot_id"] == template["snapshot_id"]
    )
    parent = json.loads((base / parent_ref["path"]).read_text())
    start = datetime.combine(day, time.min, ZONE)
    request_end = start + timedelta(days=5)
    end = start + timedelta(days=32)
    valid = {"start": day.isoformat(), "end": end.date().isoformat()}
    copytree(base, out)

    manifest["profiles"] = [
        p for p in manifest["profiles"] if not p["profile_id"].startswith("m4-prebooked-")
    ]
    manifest["cargo_profiles"] = [
        p for p in manifest["cargo_profiles"] if not p["profile_id"].startswith("m4-prebooked-")
    ]
    variants = [
        (goods, urgency, f"m4-prebooked-general-{'food-' if goods == 'food' else ''}{urgency}")
        for goods in COMMODITIES
        for urgency in ("urgent", "ordinary", "nonurgent")
    ]
    for goods, urgency, ident in variants:
        manifest["cargo_profiles"].append(
            dict(
                profile_id=ident,
                kind="delivery",
                food_class=goods,
                urgency=urgency,
                temperature_requirements={"controlled": False, "min_c": None, "max_c": None},
                handling_requirements=["general"],
                vehicle_constraints={
                    "max_height_m": 2.8,
                    "max_weight_tonnes": 3.5,
                    "restrictions": [],
                },
            )
        )

    for origin, destination in (tuple(NODES), tuple(reversed(NODES))):
        capture = json.loads((captures / f"{origin}-{destination}.json").read_text())
        trip = capture["response"]["trip"]
        road = trip["summary"]
        if (
            road["has_ferry"]
            or road["has_time_restrictions"]
            or not 15 * 3600 < road["time"] < 30 * 3600
            or capture["request"]["costing"] != "truck"
            or capture["request"]["costing_options"]["truck"]
            != {
                "height": 2.8,
                "width": 2.1,
                "length": 6.0,
                "weight": 3.5,
                "axle_load": 2.2,
                "speed_types": ["freeflow"],
                "use_ferry": 0,
            }
            or [(p["lon"], p["lat"]) for p in capture["request"]["locations"]]
            != [NODES[n][2] for n in (origin, destination)]
        ):
            raise ValueError("Unexpected truck routing receipt")
        assumptions = {
            "slots_local_hours": [10, 16],
            "weekdays": True,
            "prebooked_vehicle": True,
            "cargo_ready_at_request": True,
            "crew": 2,
            "handling_seconds": HANDLING.total_seconds(),
            "break_seconds": ROAD_BREAKS.total_seconds(),
        }
        version = canonical_hash(
            [day.isoformat(), NODES, SOURCE, COMMODITIES, capture, assumptions]
        )
        sid = "m4-prebooked-" + version[:20]
        proof = tuple(
            f"{sid}-{kind}"
            for kind in (
                "terminals",
                "destination",
                "carrier",
                "road",
                "benchmark",
                "model",
                "nonfood",
                "food",
            )
        )
        evidence = []
        for i, (name, url) in enumerate(
            (
                ("operator-terminal", NODES[origin][3]),
                ("operator-destination", NODES[destination][3]),
                ("operator-dedicated-road", SOURCE["direct-truck"]),
                ("valhalla-truck", capture["source"]),
                ("published-handling-benchmark", SOURCE["handling-benchmark"]),
                ("m4-booking-model", SOURCE["direct-truck"]),
                ("operator-spare-parts", COMMODITIES["nonfood"][1]),
                ("operator-packaged-food", COMMODITIES["food"][1]),
            )
        ):
            evidence.append(
                dict(
                    evidence_id=proof[i],
                    source_id=name,
                    source_version=version,
                    url=url,
                    retrieved_at=capture["observed_at"],
                    valid_period=valid,
                    content_hash=version,
                    license="OpenStreetMap ODbL"
                    if i == 3
                    else "Published facts and stated planning assumptions",
                    verified=i != 5,
                )
            )
        a, b = NODES[origin], NODES[destination]
        graph_version = f"valhalla:{capture['status']['version']}:tiles-{capture['status']['tileset_last_modified']}"
        snapshot = AcceptedData.model_validate(
            parent
            | dict(
                snapshot_id=sid,
                geographic_scope=sorted({a[0], a[1], b[0], b[1]}),
                valid_period=valid,
                provenance=dict(
                    graph_version=graph_version,
                    calendar_version=sid,
                    geometry_version=sid,
                    population_version=None,
                ),
                access_nodes=[
                    dict(
                        node_id=n,
                        territory_id=NODES[n][0],
                        kind="freight_terminal",
                        location=dict(zip(("lon", "lat"), NODES[n][2])),
                        source_refs=list(proof[:2]),
                    )
                    for n in (origin, destination)
                ],
                scheduled_services=[],
                evidence=parent["evidence"] + evidence,
                source_versions=parent["source_versions"]
                | {e["source_id"]: version for e in evidence},
            )
        )
        snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
        manifest["snapshots"].append(
            write(out, sid + "-snapshot.json", snapshot.model_dump(mode="json"))
            | dict(snapshot_id=sid, content_hash=snapshot_hash)
        )
        context = CargoContext(
            sid,
            graph_version,
            graph_version,
            sid,
            "m4-prebooked-v1",
            "truck-3.5t-static",
            "a2-methodology-v3",
        )

        for goods, urgency, ident in variants:
            cargo = CargoProfile(
                ident,
                version,
                goods,
                urgency,
                CargoTemperature(False, None, None, "ambient"),
                ("general",),
                ("appointment",),
                CargoVehicle("covered-van", 3500, 2.8, 2.1, 6, "freight"),
            )
            scope = CargoDeliveryScope(
                context,
                cargo.requirements_hash,
                a[1],
                b[1],
                (origin,),
                (destination,),
                start,
                end,
                ("road",),
                False,
                False,
                False,
                False,
                False,
                proof,
            )

            def compatibility(
                service: str, left: str, right: str, label: str
            ) -> CargoCompatibility:
                return CargoCompatibility(
                    f"{service}-{label}",
                    context,
                    ident,
                    version,
                    cargo.requirements_hash,
                    left,
                    right,
                    service,
                    "delivery",
                    start,
                    end,
                    "confirmed",
                    proof,
                )

            states = [
                CargoState("origin", origin, "unprepared", None, "ambient"),
                CargoState("delivered", destination, "completed", None, "ambient"),
            ]
            actions = []
            for day_index in range(9):
                local_day = day + timedelta(days=day_index)
                if local_day.weekday() >= 5:
                    continue
                for hour in assumptions["slots_local_hours"]:
                    loading = datetime.combine(local_day, time(hour), ZONE)
                    assert handoff_time(origin, loading) == loading
                    service = f"booked-{local_day.isoformat()}-{hour}"
                    prepared, arrival = f"{service}-loaded", f"{service}-arrived"
                    states.extend(
                        (
                            CargoState(prepared, origin, "prepared", service, "ambient"),
                            CargoState(arrival, destination, "in_transit", service, "ambient"),
                        )
                    )
                    drive_start = loading + HANDLING
                    driving = timedelta(seconds=road["time"]) + ROAD_BREAKS
                    unloading = handoff_time(destination, drive_start + driving)
                    actions.extend(
                        (
                            CargoAction(
                                f"{service}-load",
                                "origin",
                                prepared,
                                service,
                                "handling",
                                HANDLING.total_seconds(),
                                loading,
                                None,
                                "required",
                                compatibility(service, origin, origin, "load"),
                                CargoWaitPolicy(
                                    "initial_wait",
                                    compatibility(service, origin, origin, "wait-origin"),
                                ),
                                True,
                                proof,
                                (),
                                proof,
                            ),
                            CargoAction(
                                f"{service}-drive",
                                prepared,
                                arrival,
                                service,
                                "movement",
                                driving.total_seconds(),
                                drive_start,
                                "road",
                                "required",
                                compatibility(service, origin, destination, "drive"),
                                None,
                                True,
                                proof,
                                (),
                                proof,
                            ),
                            CargoAction(
                                f"{service}-unload",
                                arrival,
                                "delivered",
                                service,
                                "handling",
                                HANDLING.total_seconds(),
                                unloading,
                                None,
                                "required",
                                compatibility(service, destination, destination, "unload"),
                                CargoWaitPolicy(
                                    "transfer_wait",
                                    compatibility(
                                        service, destination, destination, "wait-destination"
                                    ),
                                ),
                                True,
                                proof,
                                (),
                                proof,
                            ),
                        )
                    )
            inputs = DeliveryInputs(
                kind="intercity_cargo",
                scope=scope,
                profile=cargo,
                states=tuple(states),
                actions=tuple(actions),
                readiness=(
                    CargoReadiness(
                        "origin", start, compatibility("readiness:origin", origin, origin, "origin")
                    ),
                ),
            )
            profile = Profile.model_validate(
                template
                | dict(
                    profile_id=f"{sid}-{goods}-{urgency}",
                    analysis_kind="intercity_cargo",
                    origin_id=a[1],
                    destination_id=b[1],
                    cargo_profile_id=ident,
                    cargo_example=f"Модель по записи · {COMMODITIES[goods][0]}",
                    snapshot_id=sid,
                    snapshot_hash=snapshot_hash,
                    valid_from=start,
                    valid_to=request_end,
                    passenger_modes=[],
                )
            )
            registry = ProductRegistry(
                snapshots=[snapshot],
                cargo_profiles=[
                    DeliveryCargoProfile.model_validate(
                        next(
                            row for row in manifest["cargo_profiles"] if row["profile_id"] == ident
                        )
                    )
                ],
            )
            graph_hash, costing_hash = input_fingerprints(profile, inputs, registry)
            profile = profile.model_copy(
                update=dict(graph_fingerprint=graph_hash, routing_profile_fingerprint=costing_hash)
            )
            doc = InputDocument(schema_version="waydex-engine-inputs/1", inputs=inputs)
            serialized = doc.model_dump(mode="json")
            validate_inputs(
                profile, InputDocument.model_validate(serialized).inputs, snapshot, registry
            )
            manifest["profiles"].append(
                profile.model_dump(mode="json")
                | dict(inputs=write(out, f"{profile.profile_id}-inputs.json", serialized))
            )
    write(out, "manifest.json", Manifest.model_validate(manifest).model_dump(mode="json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "captures", "out"):
        cli.add_argument("--" + name, type=Path, required=True)
    cli.add_argument("--day", type=date.fromisoformat, required=True)
    args = cli.parse_args()
    prepare(args.base, args.captures, args.out, args.day)
