"""Install a reviewed urban cargo selection into an existing compact runtime."""

import argparse
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile
from typing import Literal

from a2_api.adapters.product_inputs import InputDocument, UrbanCargoInputs
from a2_api.adapters.product_runtime import Manifest, Profile, input_fingerprints, validate_inputs
from a2_api.adapters.truck_runtime import route_scope_id
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_engine.freight.models import (
    CargoCompatibility,
    CargoContext,
    CargoTemperature,
    CargoVehicle,
)
from a2_engine.freight.models import CargoProfile as EngineCargoProfile
from a2_engine.freight.truck import TruckRoute, TruckSearchReceipt
from a2_engine.freight.urban_analysis import CargoNode, CargoSite, UrbanCargoCoverage
from assemble_compact_runtime import sha, write


@dataclass(frozen=True)
class ReviewedSelection:
    capture_hash: str
    site: str
    node: str
    pair: str
    version: str
    start: str
    end: str
    retrieved: str
    operator_documents: tuple[str, str]
    city: str = "moscow"
    node_kind: Literal["logistics_terminal", "port"] = "logistics_terminal"
    handling: Literal["container", "general", "bulk"] = "container"
    goods: Literal["food", "nonfood", "raw"] = "nonfood"
    vehicle_restrictions: tuple[str, ...] = ()
    truck_length: float = 12
    temperature_range: tuple[float, float] | None = None
    perishable: bool = False
    romex_gate_hours: bool = False
    major_ramp_hours: bool = False


SELECTIONS = {
    "major-manp": ReviewedSelection(
        "2f459ec560591890909612e7c12363c558840a68af8b8fce3c342dd4a6541108",
        "moscow-major-shcherbinka-warehouse",
        "moscow-manp-kresty-terminal",
        "major-manp",
        "2026-11-12.1",
        "2026-11-12T09:00:00+03:00",
        "2026-11-12T16:30:00+03:00",
        "2026-11-12T10:26:00+03:00",
        ("major.pdf", "manp.html"),
    ),
    "izvarino-bekasovo": ReviewedSelection(
        "d97c72e9bf2d1252274dbf6e0bfc3ea412cdf6b0d530ceb57e4a2676e28b4cb3",
        "moscow-russia-container-izvarino",
        "moscow-technopark-bekasovo",
        "izvarino-bekasovo",
        "2026-11-12.2",
        "2026-11-12T00:00:00+03:00",
        "2026-11-14T02:00:00+03:00",
        "2026-11-12T10:39:00+03:00",
        ("russia.html", "ktt.html"),
    ),
    "romex-golf": ReviewedSelection(
        "0a174da04b11dee1e06cd4bbd04ab9c67fdc8fd5ab1971011780400809d3ceb1",
        "novorossiysk-romex-storage",
        "novorossiysk-gulfstream-terminal",
        "romex-golf",
        "2026-11-12.1",
        "2026-11-12T00:00:00+03:00",
        "2026-11-14T02:00:00+03:00",
        "2026-11-12T10:55:00+03:00",
        ("romex.pdf", "golf.html"),
        city="novorossiysk",
    ),
    "kmrp-kmtp": ReviewedSelection(
        "df6a2fb18d920e1a9fe12049b2c3fdcbaf52731cda1913aa9f7da7737a62b0be",
        "kaliningrad-kmrp-warehouse",
        "kaliningrad-kmtp-port",
        "kmrp-kmtp",
        "2026-11-12.1",
        "2026-11-12T00:00:00+02:00",
        "2026-11-14T02:00:00+02:00",
        "2026-11-12T09:55:00+02:00",
        ("kmrp-complex.html", "kmtproute.html"),
        city="kaliningrad",
        node_kind="port",
        handling="general",
    ),
    "kmrp-kmtp-coal": ReviewedSelection(
        "16e57fc6ada3b0de7ff6ff81391448498296aa5df913485a33ea3032d3fc68e5",
        "kaliningrad-kmrp-warehouse",
        "kaliningrad-kmtp-port",
        "kmrp-kmtp-coal",
        "2026-11-12.1",
        "2026-11-12T00:00:00+02:00",
        "2026-11-14T02:00:00+02:00",
        "2026-11-12T10:20:00+02:00",
        ("kmrp-complex.html", "kmtproute.html"),
        city="kaliningrad",
        node_kind="port",
        handling="bulk",
        goods="raw",
        vehicle_restrictions=("covered-tipper",),
    ),
    "romex-golf-food": ReviewedSelection(
        "493ef63ca1eba27f9d5c767192c899e5ae20d18fac3e5174e07b0ae3dbaa0f49",
        "novorossiysk-romex-storage",
        "novorossiysk-gulfstream-terminal",
        "romex-golf-food",
        "2026-11-12.1",
        "2026-11-12T08:00:00+03:00",
        "2026-11-14T00:00:00+03:00",
        "2026-11-12T11:20:00+03:00",
        ("romex.pdf", "golf.html"),
        city="novorossiysk",
        goods="food",
    ),
    "kmrp-kmtp-food": ReviewedSelection(
        "63dc8ae48fabd550c68897745ee640a5f5c5f42f6f7cbd8e8864c43ad2896c81",
        "kaliningrad-kmrp-warehouse",
        "kaliningrad-kmtp-port",
        "kmrp-kmtp-food",
        "2026-11-12.1",
        "2026-11-12T00:00:00+02:00",
        "2026-11-14T02:00:00+02:00",
        "2026-11-12T10:35:00+02:00",
        ("kmrp-complex.html", "kmtproute.html"),
        city="kaliningrad",
        node_kind="port",
        handling="general",
        goods="food",
        vehicle_restrictions=("clean-dry-enclosed-body",),
    ),
    "romex-nutep-reefer": ReviewedSelection(
        "551a87742c0a09085a3e404e8740d3fa43b9dd8d02747d8adecf198a3c99d927",
        "novorossiysk-romex-storage",
        "novorossiysk-nutep-port",
        "romex-nutep-reefer",
        "2026-11-12.1",
        "2026-11-12T08:00:00+03:00",
        "2026-11-14T00:00:00+03:00",
        "2026-11-12T11:50:00+03:00",
        ("romex.pdf", "nutep-parking.pdf"),
        city="novorossiysk",
        node_kind="port",
        goods="food",
        vehicle_restrictions=("powered-reefer", "temperature-monitoring"),
        truck_length=16.5,
        temperature_range=(4, 6),
        perishable=True,
    ),
}
SELECTIONS["romex-golf-general-food"] = replace(
    SELECTIONS["romex-golf-food"],
    capture_hash="0042e3efd395348db1c12ede20b9ab32df3fc65ae5479681edd7cd6b26ee3c50",
    pair="romex-golf-general-food",
    retrieved="2026-11-12T12:00:00+03:00",
    operator_documents=("romex.pdf", "golf-general.html"),
    handling="general",
    vehicle_restrictions=("clean-dry-enclosed-body",),
    romex_gate_hours=True,
)
SELECTIONS["romex-golf-general-nonfood"] = replace(
    SELECTIONS["romex-golf-general-food"],
    capture_hash="b14ebcef027bb28568003f13cf3e398a673aed06d5e72050f38125aece60e7a9",
    pair="romex-golf-general-nonfood",
    goods="nonfood",
    vehicle_restrictions=(),
)
SELECTIONS["romex-nutep-chocolate"] = replace(
    SELECTIONS["romex-nutep-reefer"],
    capture_hash="6a7994c1a39f4800fb94abef67e536bab2b951540a93485bd7726052e5e089c2",
    pair="romex-nutep-chocolate",
    retrieved="2026-11-12T12:00:00+03:00",
    temperature_range=(15, 18),
    perishable=False,
    romex_gate_hours=True,
)

SELECTIONS["major-manp-general"] = replace(
    SELECTIONS["major-manp"],
    capture_hash="c8cc600fa1197057bdbf6e1a20013dc9d9750ddebc9ffe1900592327414be023",
    pair="major-manp-general",
    version="2026-11-12.2",
    end="2026-11-13T16:00:00+03:00",
    retrieved="2026-11-12T12:18:00+03:00",
    handling="general",
    major_ramp_hours=True,
)
SELECTIONS["major-manp-raw"] = replace(
    SELECTIONS["major-manp-general"],
    capture_hash="250173a879c530357a70cd6f48eaae9b58845df1bdacd1009920752fcf84e79c",
    pair="major-manp-raw",
    retrieved="2026-11-12T13:07:00+03:00",
    goods="raw",
)
SELECTIONS["major-foodcity-general"] = replace(
    SELECTIONS["major-manp-general"],
    capture_hash="aab5d19fcfaf0ca6511bbcead5d5ba60b3f6c4294e1a6421f7f570c17f4c1719",
    node="moscow-foodcity-crossdock",
    pair="major-foodcity-general",
    retrieved="2026-11-12T12:48:00+03:00",
    operator_documents=("major.pdf", "foodcity-contacts.html"),
    goods="food",
    vehicle_restrictions=("clean-dry-enclosed-body",),
)
SELECTIONS["kmrp-kmtp-timber"] = replace(
    SELECTIONS["kmrp-kmtp"],
    capture_hash="e86a88e2ac660f0d8aa2e1ef7a09b49f5307ac698acce660ddf0851627f2e03c",
    pair="kmrp-kmtp-timber",
    retrieved="2026-11-12T12:18:00+03:00",
    goods="raw",
)
SELECTIONS["kmrp-kmtp-frozen-fish"] = replace(
    SELECTIONS["kmrp-kmtp-food"],
    capture_hash="fa05a7bf6888c8c220210e803deb0bfb74d65b142e54a1aeea9303a18534b0bc",
    pair="kmrp-kmtp-frozen-fish",
    retrieved="2026-11-12T11:37:00+02:00",
    temperature_range=(-22, -18),
    perishable=True,
    vehicle_restrictions=("powered-reefer", "temperature-monitoring"),
)
SELECTIONS["romex-golf-timber"] = replace(
    SELECTIONS["romex-golf-general-nonfood"],
    capture_hash="79c862be6d4a70d987526d11ef97179774544ee6f3f3509987fca80680eed2f8",
    pair="romex-golf-timber",
    retrieved="2026-11-12T12:37:00+03:00",
    goods="raw",
)
SELECTIONS["romex-golf-timber-container"] = replace(
    SELECTIONS["romex-golf-timber"],
    capture_hash="5d05b3c36049cc73891b82385eb8e3300540a8a4b311b5cb6318544383e030ba",
    pair="romex-golf-timber-container",
    handling="container",
)


CARGO_EXAMPLES = {
    "izvarino-bekasovo": "Сухой контейнер с непродовольственным грузом",
    "major-manp-general": "Металлические комплектующие в коробках на паллетах",
    "major-manp-raw": "Сухие пиломатериалы на паллетах для деревообработки",
    "major-foodcity-general": "Фасованные консервы в коробках на паллетах",
    "kmrp-kmtp": "Тарно-штучный непродовольственный груз",
    "kmrp-kmtp-coal": "Уголь навалом",
    "kmrp-kmtp-food": "Сахар в мешках на паллетах",
    "kmrp-kmtp-timber": "Пакетированные пиломатериалы для деревообработки",
    "kmrp-kmtp-frozen-fish": "Замороженная рыба в коробках при −22…−18 °C",
    "romex-golf": "Сухой контейнер с непродовольственным грузом",
    "romex-golf-food": "Фасованный сахар в сухом контейнере",
    "romex-golf-general-food": "Бутилированное масло в коробках на паллетах",
    "romex-golf-general-nonfood": "Металлические комплектующие в коробках на паллетах",
    "romex-golf-timber": "Пакетированные пиломатериалы на паллетах для деревообработки",
    "romex-golf-timber-container": "Пиломатериалы в закрытом 20-футовом контейнере",
    "romex-nutep-reefer": "Охлаждённая молочная продукция при +4…+6 °C",
    "romex-nutep-chocolate": "Шоколад без начинки при +15…+18 °C",
}
CARGO_EXAMPLES_BY_SNAPSHOT = {
    f"{SELECTIONS[key].city}-{SELECTIONS[key].pair}-{SELECTIONS[key].version}": label
    for key, label in CARGO_EXAMPLES.items()
}


DIRS: tuple[Literal["outbound", "inbound"], ...] = ("outbound", "inbound")


def departure_windows(
    start: datetime, end: datetime, reviewed: ReviewedSelection, arrival_seconds: float
):
    if not (reviewed.romex_gate_hours or reviewed.major_ramp_hours):
        yield start, end
        return
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    trip = timedelta(seconds=arrival_seconds if reviewed.romex_gate_hours else 0)
    # For an inbound trip, translate Romex's receiving windows to departure time.
    while midnight - trip < end:
        if reviewed.major_ramp_hours:
            # Both operators require a prior appointment. End departures at 16:00,
            # so the inbound truck reaches Major before the published 16:30 cutoff.
            windows = ((540, 960),) if midnight.weekday() < 5 else ()
        else:
            # Retain receiving windows needed by the four published atlas slots.
            windows = ((480, 720), (780, 1170), (1200, 1440))
        for opening, closing in windows:
            lower = max(start, midnight + timedelta(minutes=opening) - trip)
            upper = min(end, midnight + timedelta(minutes=closing) - trip)
            if lower < upper:
                yield lower, upper
        midnight += timedelta(days=1)


def pinned(root, ref):
    path = (root / ref["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or sha(path) != ref["sha256"]:
        raise ValueError(f"Input identity mismatch: {ref['path']}")
    return path


def build(base: Path, captures: Path, output: Path, reviewed: ReviewedSelection) -> None:
    start, end = datetime.fromisoformat(reviewed.start), datetime.fromisoformat(reviewed.end)
    period = {"start": start.date().isoformat(), "end": end.date().isoformat()}
    city = reviewed.city
    analysis_type = "port" if reviewed.node_kind == "port" else "terminal"
    snapshot_id = f"{city}-{reviewed.pair}-{reviewed.version}"

    def evidence(evidence_id, url, digest, license_id):
        return {
            "evidence_id": evidence_id,
            "source_id": evidence_id,
            "source_version": reviewed.version,
            "url": url,
            "retrieved_at": reviewed.retrieved,
            "valid_period": period,
            "license": license_id,
            "content_hash": digest,
            "verified": True,
        }

    capture = json.loads(
        pinned(captures, {"path": "capture.json", "sha256": reviewed.capture_hash}).read_bytes()
    )
    for ref in (capture["graph"], capture["gates"], *capture["docs"].values()):
        pinned(captures, ref)
    gates = json.loads(pinned(captures, capture["gates"]).read_bytes())
    manifest = Manifest.model_validate_json((base / "manifest.json").read_bytes())
    cargo_prefix = f"truck-model-20t-{reviewed.handling}-{reviewed.goods}"
    if reviewed.temperature_range:
        low, high = reviewed.temperature_range
        range_id = f"{low:g}-to-{high:g}c" if low < 0 else f"{low:g}-{high:g}c"
        temperature = CargoTemperature(True, low, high, f"chilled-{range_id}")
        cargo_prefix += f"-reefer-{range_id}"
    else:
        temperature = CargoTemperature(False, None, None, "uncontrolled")
    if reviewed.perishable:
        cargo_prefix += "-perishable"
    if len(gates) not in (2, 3):
        raise ValueError("Expected two road ends or an additional directional exit")
    gate_order = {"outbound": (0, 1), "inbound": (len(gates) - 1, 0)}
    cargo_ids = {f"{cargo_prefix}-{urgency}" for urgency in ("ordinary", "urgent")}
    public_profiles = list(manifest.cargo_profiles)
    existing_cargo_ids = {profile.profile_id for profile in public_profiles}
    for template in manifest.cargo_profiles:
        if template.profile_id not in {
            "truck-model-20t-container-nonfood-ordinary",
            "truck-model-20t-container-nonfood-urgent",
        }:
            continue
        cargo_id = f"{cargo_prefix}-{template.urgency}"
        if cargo_id not in existing_cargo_ids:
            public_profiles.append(
                template.model_copy(
                    update={
                        "profile_id": cargo_id,
                        "handling_requirements": [reviewed.handling],
                        "food_class": reviewed.goods,
                        "perishable": reviewed.perishable,
                        "temperature_requirements": template.temperature_requirements.model_copy(
                            update={
                                "controlled": temperature.controlled,
                                "min_c": temperature.min_c,
                                "max_c": temperature.max_c,
                            }
                        ),
                        "vehicle_constraints": template.vehicle_constraints.model_copy(
                            update={"restrictions": list(reviewed.vehicle_restrictions)}
                        ),
                    }
                )
            )
    replaced = [
        p
        for p in manifest.profiles
        if p.analysis_kind == "urban_cargo"
        and p.city_id == city
        and p.cargo_profile_id in cargo_ids
    ]
    if not manifest.compact or any(p.truck_routing is None for p in replaced):
        raise ValueError("Expected compact runtime without a prepared selection for this city")
    parent = next(
        p for p in manifest.profiles if p.city_id == city and p.passenger_modes == ["walk"]
    )
    parent_ref = next(ref for ref in manifest.snapshots if ref.snapshot_id == parent.snapshot_id)
    parent_data = AcceptedData.model_validate_json(
        pinned(base, parent_ref.model_dump(mode="json")).read_bytes()
    ).model_dump(mode="json")
    if canonical_hash(parent_data) != parent_ref.content_hash:
        raise ValueError("Administrative snapshot identity mismatch")

    graph_digest = capture["graph"]["sha256"]
    context = CargoContext(
        snapshot_id,
        f"local-valhalla-truck:{graph_digest}",
        graph_digest,
        "not_collected",
        "freeflow-truck-model-20t-v1",
        f"truck-freeflow-20t-4m-2.5m-{reviewed.truck_length:g}m",
        "a2-methodology-v3",
    )
    evidence_docs = [
        evidence(
            f"{city}-cargo-{name}",
            row["url"],
            row["sha256"],
            "provider-imagery"
            if name.endswith(("-sat.png", "-graph.png"))
            else "public-operator-document",
        )
        for name, row in capture["docs"].items()
    ]
    evidence_docs += [
        evidence(
            f"osm-node-{gate['osm_node']}",
            f"https://www.openstreetmap.org/node/{gate['osm_node']}",
            capture["gates"]["sha256"],
            "ODbL-1.0",
        )
        for gate in gates
    ]
    times = {}
    for direction, indexes in gate_order.items():
        endpoints = [gates[index] for index in indexes]
        refs = capture["routes"][direction]
        request = json.loads(pinned(captures, refs["request"]).read_bytes())
        truck = request["costing_options"]["truck"]
        if request["costing"] != "truck" or (
            truck["weight"],
            truck["height"],
            truck["width"],
            truck["length"],
            truck["speed_types"],
        ) != (20, 4, 2.5, reviewed.truck_length, ["freeflow"]):
            raise ValueError("Captured route vehicle does not match the reviewed truck")
        locate = json.loads(pinned(captures, refs["locate"]).read_bytes())
        trip = json.loads(pinned(captures, refs["response"]).read_bytes())["trip"]
        for gate, location, match in zip(endpoints, request["locations"], locate, strict=True):
            if (location["lat"], location["lon"]) != (gate["lat"], gate["lon"]) or not any(
                e.get("way_id", e.get("edge_info", {}).get("way_id")) == gate["osm_way"]
                and abs(e["correlated_lat"] - gate["lat"]) <= 0.000001
                and abs(e["correlated_lon"] - gate["lon"]) <= 0.000001
                for e in match["edges"]
            ):
                raise ValueError("Truck route does not use the reviewed road connection")
        if trip["status"] or trip["summary"]["has_time_restrictions"]:
            raise ValueError("Truck route is unavailable or has unresolved time restrictions")
        times[direction] = trip["summary"]["time"]
        evidence_docs.append(
            evidence(
                f"{city}-gate-truck-{direction}",
                "https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/",
                refs["response"]["sha256"],
                "ODbL-1.0",
            )
        )

    source_ids = tuple(row["evidence_id"] for row in evidence_docs)
    access = [
        dict(
            node_id=f"osm-node-{gate['osm_node']}",
            kind="truck_road_connection" if gate.get("connector_m") else "truck_gate",
            territory_id=city,
            location={key: gate[key] for key in ("lat", "lon")},
            source_refs=[
                f"osm-node-{gate['osm_node']}",
                f"{city}-cargo-{reviewed.operator_documents[min(index, 1)]}",
            ],
        )
        for index, gate in enumerate(gates)
    ]
    physical = [
        dict(
            territory_id=city,
            location=gate.get("site_location", row["location"]),
            access_node_id=row["node_id"],
            source_refs=row["source_refs"],
        )
        for row, gate in zip(access[:2], gates[:2], strict=True)
    ]
    access_seconds = [
        gate["connector_m"] / (gate["connector_kmh"] / 3.6) if "connector_m" in gate else 0.0
        for gate in gates
    ]
    territory = next(row for row in parent_data["territories"] if row["territory_id"] == city)
    territory_refs = set(territory["source_refs"] + territory["geometry"]["source_refs"])
    evidence_docs.extend(e for e in parent_data["evidence"] if e["evidence_id"] in territory_refs)
    snapshot = AcceptedData.model_validate(
        {
            **{key: [] for key, value in parent_data.items() if isinstance(value, list)},
            "schema_version": "2",
            "snapshot_id": snapshot_id,
            "timezone": parent_data["timezone"],
            "geographic_scope": [city],
            "valid_period": period,
            "provenance": {**parent_data["provenance"], "graph_version": context.graph_version},
            "qa": {
                "acceptance_state": "accepted",
                "accepted_at": reviewed.retrieved,
                "accepted_by": "Waydex integration: source and truck graph review",
                "checks": [
                    "operator-loading-and-unloading",
                    "gate-to-graph-match",
                    "both-directions",
                ],
            },
            "source_versions": {e["source_id"]: e["source_version"] for e in evidence_docs},
            "evidence": evidence_docs,
            "territories": [territory],
            "access_nodes": access,
            "freight_sites": [{"site_id": reviewed.site, **physical[0]}],
            "freight_nodes": [
                {"freight_node_id": reviewed.node, "kind": reviewed.node_kind, **physical[1]}
            ],
            "freight_compatibility": [
                {
                    "compatibility_id": f"{reviewed.pair}-{reviewed.goods}-{direction}",
                    "site_id": reviewed.site,
                    "freight_node_id": reviewed.node,
                    "direction": direction,
                    "food_classes": [reviewed.goods],
                    "temperature_requirements": [temperature.condition_id],
                    "perishable": reviewed.perishable,
                    "handling_requirements": [reviewed.handling],
                    "vehicle_constraints": [context.routing_profile],
                    "compatible": True,
                    "source_refs": list(source_ids),
                }
                for direction in DIRS
            ],
        }
    )
    registry = ProductRegistry(cargo_profiles=public_profiles)
    prepared = []
    for urgency in ("ordinary", "urgent"):
        cargo_id = f"{cargo_prefix}-{urgency}"
        cargo = EngineCargoProfile(
            cargo_id,
            reviewed.version,
            reviewed.goods,
            urgency,
            temperature,
            (reviewed.handling,),
            ("agreed-appointment",),
            CargoVehicle(
                "truck",
                20000,
                4,
                2.5,
                reviewed.truck_length,
                "truck-model",
                reviewed.vehicle_restrictions,
            ),
            perishable=reviewed.perishable,
        )
        for direction in DIRS:
            endpoints = (reviewed.site, reviewed.node)
            connectors = [access_seconds[index] for index in gate_order[direction]]
            if direction == "inbound":
                endpoints = endpoints[::-1]
            route_id = f"{reviewed.pair}-{urgency}-{direction}"
            requirements = cargo.requirements_hash
            refs = tuple(
                ref
                for ref in source_ids
                if ref != f"{city}-gate-truck-{DIRS[1 - DIRS.index(direction)]}"
            )
            compatibility = CargoCompatibility(
                route_id,
                context,
                cargo_id,
                reviewed.version,
                requirements,
                *endpoints,
                "static-truck",
                direction,
                start,
                end,
                "confirmed",
                refs,
            )
            route = TruckRoute(
                route_id,
                context,
                requirements,
                cargo.vehicle,
                *endpoints,
                "static-truck",
                direction,
                start,
                times[direction],
                *connectors,
                "reachable",
                compatibility,
                refs,
            )
            receipt = TruckSearchReceipt(
                route_id, route.fingerprint, route_scope_id(route), True, True, refs
            )
            inputs = UrbanCargoInputs(
                kind="urban_cargo",
                profile=cargo,
                sites=(CargoSite(reviewed.site, context, tuple(access[0]["source_refs"])),),
                nodes=(
                    CargoNode(
                        reviewed.node,
                        reviewed.node_kind,
                        analysis_type,
                        context,
                        tuple(access[1]["source_refs"]),
                    ),
                ),
                routes=(route,),
                receipts=(receipt,),
                coverage=UrbanCargoCoverage(
                    context, requirements, direction, (reviewed.site,), (), False, refs
                ),
                area_km2=territory["area_km2"],
            )
            prepared.append((route_id, inputs))
    output.mkdir(parents=True)
    base_refs = [*manifest.snapshots, *(p.inputs for p in manifest.profiles if p.inputs)]
    if manifest.selection:
        base_refs.append(manifest.selection)
    for ref in base_refs:
        src = pinned(base, ref.model_dump(mode="json"))
        dst = output / ref.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        copyfile(src, dst)
    snapshot_data = snapshot.model_dump(mode="json")
    snapshot_hash = canonical_hash(snapshot_data)
    manifest_data = manifest.model_dump(mode="json")
    manifest_data["cargo_profiles"] = [
        profile.model_dump(mode="json") for profile in public_profiles
    ]
    removed_ids = {p.profile_id for p in replaced}
    manifest_data["profiles"] = [
        p for p in manifest_data["profiles"] if p["profile_id"] not in removed_ids
    ]
    snapshot_file = f"{city}-cargo-snapshot.json"
    if any(ref.path == snapshot_file for ref in base_refs):
        snapshot_file = f"{snapshot_id}.json"
    manifest_data["snapshots"].append(
        {
            **write(output, snapshot_file, snapshot_data),
            "snapshot_id": snapshot_id,
            "content_hash": snapshot_hash,
        }
    )
    scope_ids = []
    for route_id, inputs in prepared:
        input_doc = InputDocument(schema_version="waydex-engine-inputs/1", inputs=inputs)
        input_ref = write(output, f"{route_id}.json", input_doc.model_dump(mode="json"))
        route = inputs.routes[0]
        arrival_seconds = (
            times["inbound"] + sum(access_seconds[index] for index in gate_order["inbound"])
            if route.direction == "inbound"
            else 0
        )
        if reviewed.major_ramp_hours and arrival_seconds > 30 * 60:
            raise ValueError("Inbound truck would arrive after Major's ramp cutoff")
        for index, (lower, upper) in enumerate(
            departure_windows(start, end, reviewed, arrival_seconds)
        ):
            profile = Profile.model_validate(
                {
                    "profile_id": route_id
                    if not (reviewed.romex_gate_hours or reviewed.major_ramp_hours)
                    else f"{route_id}-{index}",
                    "analysis_kind": "urban_cargo",
                    "city_id": city,
                    "cargo_profile_id": inputs.profile.profile_id,
                    "direction": inputs.coverage.direction,
                    "valid_from": lower,
                    "valid_to": upper,
                    "snapshot_id": snapshot_id,
                    "snapshot_hash": snapshot_hash,
                    "graph_fingerprint": "0" * 64,
                    "routing_profile_fingerprint": "0" * 64,
                    "inputs": input_ref,
                    "static_observations": True,
                    "compact": True,
                    "max_route_pairs": 1,
                }
            )
            graph, routing = input_fingerprints(profile, inputs, registry)
            profile = profile.model_copy(
                update={"graph_fingerprint": graph, "routing_profile_fingerprint": routing}
            )
            validate_inputs(profile, inputs, snapshot, registry)
            manifest_data["profiles"].append(profile.model_dump(mode="json"))
            scope_ids.append(profile.profile_id)
    for profile in manifest_data["profiles"]:
        example = CARGO_EXAMPLES_BY_SNAPSHOT.get(profile["snapshot_id"])
        if profile["analysis_kind"] == "urban_cargo" and example:
            profile["cargo_example"] = example
    selection = (
        json.loads(pinned(base, manifest.selection.model_dump(mode="json")).read_bytes())
        if manifest.selection
        else {}
    )
    urban_selection = selection.setdefault("urban_cargo", {})
    selections = (
        urban_selection
        if city not in urban_selection
        else selection.setdefault("urban_cargo_additions", {})
    )
    selection_key = city if selections is urban_selection else snapshot_id
    selections[selection_key] = {
        "sites": [reviewed.site],
        "nodes": [reviewed.node],
        "capture_sha256": reviewed.capture_hash,
        "scopes": scope_ids,
        "evidence": list(source_ids),
        **({"applicability": capture["applicability"]} if "applicability" in capture else {}),
    }
    manifest_data["selection"] = write(output, "selection.json", selection)
    document = Manifest.model_validate(manifest_data)
    print(write(output, "manifest.json", document.model_dump(mode="json"))["sha256"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--selection", choices=SELECTIONS, default="major-manp")
    args = parser.parse_args()
    build(args.base, args.captures, args.output, SELECTIONS[args.selection])
