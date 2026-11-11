"""Bindings of intercity nodes and territories to Yandex Rasp codes (rule set ``rasp-bindings-v1``).

BACKEND's ``RaspPlaceBinding`` needs one unambiguous provider code per territory with
evidence, never guessed from a name or a frontend alias. The source is the Rasp
``stations_list`` answer (one API call, stored as raw).

Rules:

- a railway node takes the Rasp station with one of its ESR codes; a code shared by
  several Rasp stations binds nothing;
- an airport node takes the only Rasp airport within 5 km, a bus node the only Rasp
  bus station (``station_type=bus_station``) within 300 m; several candidates bind
  nothing (Rasp airports carry no IATA code, so position is the only key);
- a city takes the Rasp settlement (``c…``) of the Rasp stations bound to the nodes
  located in it (airports bound only as «served» do not count); when they lie in
  several settlements (Moscow and Zelenograd, Saint Petersburg and Pushkin), the
  settlement with more than half of them, or more than twice the nodes of any
  other, wins and the others are reported; otherwise nothing is bound;
- a settlement claimed by several cities (Девяткино of Murino lies in Rasp's Saint
  Petersburg) stays with the city that has more than twice the nodes of each other
  claimant; the others are left unbound and reported;
- a city with no such node but one bound airport that serves only it (Kyzyl,
  Anadyr) takes that airport's station code (``s…``): BACKEND accepts ``s…`` and
  ``c…`` codes, and a code never names two territories;
- a region is bound through its capital (PRODUCT_SPEC §1), with the capital's code;
- names are never compared.

Licence: the API terms (https://yandex.ru/legal/rasp_api/, 3.2.4) allow storing the
answers only as a cache for the service's own functionality. The raw answer and the
bindings stay under ``data/raw`` and ``data/processed`` (not in git); only the code
and a QA summary with counts are versioned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from . import territories
from .intercity_nodes import IntercityNode, load_nodes
from .manifest import Identifier, load_dataset_manifest
from .paths import data_dir, processed_dir, raw_dir
from .store import sha256_file

SOURCE_ID = "yandex_rasp"
RULES_VERSION = "rasp-bindings-v1"
RUSSIA_CODE = "l225"
AIRPORT_M = 5000.0
BUS_M = 300.0
NodeBasis = Literal["esr_code", "airport_within_5km", "bus_station_within_300m"]
# Rasp ``transport_type`` values (API docs, «Системы кодирования») → ML1 ``PublicMode``
TRANSPORT_MODES = {
    "plane": "air",
    "helicopter": "air",
    "train": "rail",
    "suburban": "rail",
    "bus": "road",
    "water": "water",
    "sea": "water",
}
TRANSPORT_DOC = "https://yandex.ru/dev/rasp/doc/ru/"


def transport_mode_bindings() -> list[dict[str, Any]]:
    """ML1 ``TransportModeBinding`` rows for the Rasp transport types."""

    return [
        {"transport_type": t, "mode": m, "evidence_refs": [f"doc:{TRANSPORT_DOC}"]}
        for t, m in sorted(TRANSPORT_MODES.items())
    ]


class NodeCode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Identifier
    provider_code: str
    settlement_code: str | None
    basis: NodeBasis
    distance_m: float | None
    counts_for_city: bool


class PlaceBinding(BaseModel):
    """A ``RaspPlaceBinding`` row without ``snapshot_id`` (BACKEND sets it)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    territory_id: Identifier
    level: Literal["city", "region"]
    provider_code: str
    timezone: str
    basis: Literal["node_codes", "node_codes_majority", "served_airport", "capital"]
    node_ids: list[Identifier]
    evidence_id: str


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _stations(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """Russian Rasp stations with their settlement code."""

    russia = next(
        c for c in answer["countries"] if (c.get("codes") or {}).get("yandex_code") == RUSSIA_CODE
    )
    rows = []
    for region in russia["regions"]:
        for settlement in region["settlements"]:
            code = (settlement.get("codes") or {}).get("yandex_code") or None
            for station in settlement["stations"]:
                rows.append({**station, "settlement_code": code})
    return rows


def _nearest(
    node: IntercityNode, candidates: list[dict[str, Any]], limit: float
) -> tuple[dict[str, Any], float] | None | Literal["several"]:
    near = []
    for station in candidates:
        lon, lat = station.get("longitude"), station.get("latitude")
        if isinstance(lon, int | float) and isinstance(lat, int | float):
            distance = _metres((node.lon, node.lat), (float(lon), float(lat)))
            if distance < limit:
                near.append((distance, station))
    if not near:
        return None
    if len(near) > 1:
        return "several"
    return near[0][1], round(near[0][0], 1)


def build(  # noqa: C901
    answer: dict[str, Any],
    nodes: list[IntercityNode],
    directory: list[territories.TerritoryEntry],
    evidence_id: str,
) -> tuple[list[NodeCode], list[PlaceBinding], dict[str, Any]]:
    stations = _stations(answer)
    by_esr: dict[str, list[dict[str, Any]]] = {}
    for station in stations:
        code = (station.get("codes") or {}).get("esr_code")
        if code:
            by_esr.setdefault(code, []).append(station)
    airports = [s for s in stations if s.get("station_type") == "airport"]
    buses = [s for s in stations if s.get("station_type") == "bus_station"]
    codes: list[NodeCode] = []
    unbound: Counter[str] = Counter()
    for node in sorted(nodes, key=lambda n: n.node_id):
        found: tuple[dict[str, Any], float | None, NodeBasis] | None = None
        if node.kind == "rail_station":
            matches = {
                s["codes"]["yandex_code"]: s for e in node.esr_codes for s in by_esr.get(e, [])
            }
            if len(matches) == 1:
                found = (next(iter(matches.values())), None, "esr_code")
            else:
                unbound["rail_" + ("several" if matches else "no_esr_match")] += 1
        else:
            pool, limit, basis = (
                (airports, AIRPORT_M, "airport_within_5km")
                if node.kind == "airport"
                else (buses, BUS_M, "bus_station_within_300m")
            )
            near = _nearest(node, pool, limit)
            if near == "several":
                unbound[f"{node.kind}_several"] += 1
            elif near is None:
                unbound[f"{node.kind}_none"] += 1
            else:
                found = (near[0], near[1], basis)  # type: ignore[assignment]
        if found is None:
            continue
        station, distance, basis_found = found
        codes.append(
            NodeCode(
                node_id=node.node_id,
                provider_code=station["codes"]["yandex_code"],
                settlement_code=station["settlement_code"],
                basis=basis_found,
                distance_m=distance,
                counts_for_city=node.assignment_basis != "served_place",
            )
        )
    node_by_id = {n.node_id: n for n in nodes}
    settlements: dict[str, dict[str, list[str]]] = {}
    for code in codes:
        if not code.counts_for_city or not code.settlement_code:
            continue
        for territory_id in node_by_id[code.node_id].territory_ids:
            settlements.setdefault(territory_id, {}).setdefault(code.settlement_code, []).append(
                code.node_id
            )
    entries = {e.territory_id: e for e in directory}
    bindings: list[PlaceBinding] = []
    several: list[str] = []
    no_majority: list[str] = []
    for territory_id in sorted(settlements):
        options = settlements[territory_id]
        ranked = sorted(options.items(), key=lambda item: (-len(item[1]), item[0]))
        provider_code, node_ids = ranked[0]
        if len(options) > 1:
            label = ", ".join(f"{c} ({len(v)})" for c, v in sorted(options.items()))
            total = sum(len(v) for v in options.values())
            if 2 * len(node_ids) <= total and len(node_ids) <= 2 * len(ranked[1][1]):
                no_majority.append(f"{entries[territory_id].name}: {label}")
                continue
            several.append(f"{entries[territory_id].name}: {label} → {provider_code}")
        bindings.append(
            PlaceBinding(
                territory_id=territory_id,
                level="city",
                provider_code=provider_code,
                timezone=entries[territory_id].timezone,
                basis="node_codes" if len(options) == 1 else "node_codes_majority",
                node_ids=sorted(node_ids),
                evidence_id=evidence_id,
            )
        )
    claims: dict[str, list[PlaceBinding]] = {}
    for binding in bindings:
        claims.setdefault(binding.provider_code, []).append(binding)
    yielded: list[str] = []
    for provider_code, claimants in sorted(claims.items()):
        if len(claimants) == 1:
            continue
        claimants.sort(key=lambda b: (-len(b.node_ids), b.territory_id))
        keeper_ok = len(claimants[0].node_ids) > 2 * len(claimants[1].node_ids)
        losers = claimants[1:] if keeper_ok else claimants
        for loser in losers:
            bindings.remove(loser)
            yielded.append(f"{entries[loser.territory_id].name}: {provider_code}")
    used = {b.provider_code for b in bindings}
    bound_cities = {b.territory_id for b in bindings}
    airport_codes: dict[str, list[NodeCode]] = {}
    for code in codes:
        node = node_by_id[code.node_id]
        if node.kind == "airport" and not code.counts_for_city and len(node.territory_ids) == 1:
            airport_codes.setdefault(node.territory_ids[0], []).append(code)
    for territory_id in sorted(set(airport_codes) - bound_cities - set(settlements)):
        options_air = airport_codes[territory_id]
        if len(options_air) != 1 or options_air[0].provider_code in used:
            continue
        bindings.append(
            PlaceBinding(
                territory_id=territory_id,
                level="city",
                provider_code=options_air[0].provider_code,
                timezone=entries[territory_id].timezone,
                basis="served_airport",
                node_ids=[options_air[0].node_id],
                evidence_id=evidence_id,
            )
        )
    by_city = {b.territory_id: b for b in bindings}
    regions_unbound = []
    for region in sorted(
        (e for e in directory if e.level == "region"), key=lambda e: e.territory_id
    ):
        capital = by_city.get(region.capital_city_id or "")
        if capital is None:
            regions_unbound.append(region.name)
            continue
        bindings.append(
            PlaceBinding(
                territory_id=region.territory_id,
                level="region",
                provider_code=capital.provider_code,
                timezone=region.timezone,
                basis="capital",
                node_ids=capital.node_ids,
                evidence_id=evidence_id,
            )
        )
    codes_used = Counter(b.provider_code for b in bindings if b.level == "city")
    report = {
        "rules": RULES_VERSION,
        "rasp_russian_stations": len(stations),
        "nodes": dict(Counter(n.kind for n in nodes)),
        "nodes_bound": dict(Counter(c.basis for c in codes)),
        "nodes_unbound": dict(sorted(unbound.items())),
        "cities_bound": sum(1 for b in bindings if b.level == "city"),
        "cities_with_nodes": len({t for n in nodes for t in n.territory_ids}),
        "cities_several_settlements": several,
        "cities_without_majority": no_majority,
        "cities_sharing_a_settlement": yielded,
        "regions_bound": sum(1 for b in bindings if b.level == "region"),
        "regions_unbound": regions_unbound,
        "provider_codes_shared_by_cities": sorted(c for c, k in codes_used.items() if k > 1),
    }
    return codes, bindings, report


def _checks(bindings: list[PlaceBinding], report: dict[str, Any]) -> list[dict[str, Any]]:
    ids = Counter(b.territory_id for b in bindings)
    pilots = {b.territory_id for b in bindings} & {"moscow", "kaliningrad", "novorossiysk"}
    return [
        {
            "check_id": "territory_bound_once",
            "status": "pass" if all(c == 1 for c in ids.values()) else "fail",
            "severity": "fail",
            "details": {"duplicates": sorted(i for i, c in ids.items() if c > 1)},
        },
        {
            # one provider code must not name two territories (BACKEND handoff)
            "check_id": "provider_code_names_one_city",
            "status": "pass" if not report["provider_codes_shared_by_cities"] else "fail",
            "severity": "fail",
            "details": {"shared": report["provider_codes_shared_by_cities"]},
        },
        {
            "check_id": "pilot_cities_bound",
            "status": "pass" if len(pilots) == 3 else "warn",
            "severity": "warn",
            "details": {"bound": sorted(pilots)},
        },
        {
            "check_id": "cities_with_several_settlements",
            "status": "pass" if not report["cities_several_settlements"] else "warn",
            "severity": "warn",
            "details": {"cities": report["cities_several_settlements"]},
        },
        {
            "check_id": "cities_without_majority",
            "status": "pass" if not report["cities_without_majority"] else "warn",
            "severity": "warn",
            "details": {"cities": report["cities_without_majority"]},
        },
        {
            "check_id": "regions_bound_through_capitals",
            "status": "pass" if not report["regions_unbound"] else "warn",
            "severity": "warn",
            "details": {"unbound": report["regions_unbound"]},
        },
    ]


def publish(
    dataset_id: str, nodes_directory_id: str, territory_directory_id: str, output_id: str
) -> Path:
    manifest = load_dataset_manifest(dataset_id)
    raw = raw_dir() / manifest.raw_path
    if sha256_file(raw).value != manifest.checksum.value:
        raise ValueError(f"{dataset_id}: raw sha256 mismatch")
    evidence_id = f"{SOURCE_ID}:stations_list:{manifest.checksum.value[:16]}"
    answer = json.loads(raw.read_text(encoding="utf-8"))
    codes, bindings, report = build(
        answer,
        load_nodes(nodes_directory_id),
        territories.load_directory(territory_directory_id),
        evidence_id,
    )
    checks = _checks(bindings, report)
    failed = [c["check_id"] for c in checks if c["status"] == "fail"]
    if failed:
        raise ValueError(f"rasp bindings QA failed: {failed}")
    base = processed_dir() / SOURCE_ID
    target = base / output_id
    if target.exists():
        raise FileExistsError(f"{output_id} exists; bindings are immutable")
    staging = base / f".staging-{output_id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    summary = {
        "output_id": output_id,
        "created_at": datetime.now(UTC).isoformat(),
        "rules": RULES_VERSION,
        "source": {
            "dataset_id": dataset_id,
            "raw_sha256": manifest.checksum.value,
            "retrieved_at": manifest.retrieved_at.isoformat(),
            "evidence_id": evidence_id,
        },
        "inputs": {"nodes": nodes_directory_id, "territories": territory_directory_id},
        "checks": checks,
        "summary": report,
    }
    try:
        files = {
            "node_codes.json": [c.model_dump(mode="json") for c in codes],
            "bindings.json": [b.model_dump(mode="json") for b in bindings],
        }
        hashes = {}
        for name, rows in files.items():
            payload = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            (staging / name).write_bytes(payload)
            hashes[name] = {"sha256": hashlib.sha256(payload).hexdigest(), "rows": len(rows)}
        summary["files"] = hashes
        (staging / "manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # the versioned QA summary holds counts and our own names, no Rasp codes
    qa = data_dir() / "manifests" / "qa" / f"{output_id}.json"
    qa.write_text(
        json.dumps(_public(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


CODE_LISTS = (
    "cities_several_settlements",
    "cities_without_majority",
    "cities_sharing_a_settlement",
    "cities",
)


def _public(value: Any) -> Any:
    """The summary with Rasp codes cut from city lists («Москва: c213 (91), …» → «Москва»)."""

    if isinstance(value, dict):
        return {
            key: [item.split(":", 1)[0] for item in item_list]
            if key in CODE_LISTS and isinstance(item_list := value[key], list)
            else _public(value[key])
            for key in value
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def load_bindings(output_id: str) -> tuple[list[NodeCode], list[PlaceBinding]]:
    """Node codes and place bindings; hashes and rows are verified on every load."""

    target = processed_dir() / SOURCE_ID / output_id
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    for name, ref in manifest["files"].items():
        if sha256_file(target / name).value != ref["sha256"]:
            raise ValueError(f"{output_id}/{name}: sha256 mismatch")
    codes = json.loads((target / "node_codes.json").read_text(encoding="utf-8"))
    bindings = json.loads((target / "bindings.json").read_text(encoding="utf-8"))
    return (
        [NodeCode.model_validate(row) for row in codes],
        [PlaceBinding.model_validate(row) for row in bindings],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_id")
    parser.add_argument("nodes_directory_id")
    parser.add_argument("territory_directory_id")
    parser.add_argument("output_id")
    args = parser.parse_args()
    print(
        publish(
            args.dataset_id, args.nodes_directory_id, args.territory_directory_id, args.output_id
        )
    )


__all__ = ["NodeCode", "PlaceBinding", "build", "load_bindings", "publish"]

if __name__ == "__main__":
    main()
