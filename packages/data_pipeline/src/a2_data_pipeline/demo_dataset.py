"""Versioned SYNTHETIC demo dataset: three cities and six directed pairs (``waydex-demo-rules-v1``).

Real inputs are read and pinned: the accepted city bundles in ``data/bundles`` (their
AcceptedData content hashes must equal the handoff runtime manifest), the intercity node
directory, and a small extract of the handoff ``runtime.tar.gz`` (road and truck
observations). Everything else is a model value from one seeded generator. Every model
row carries ``origin`` and DEMO evidence, and ``fields.json`` gives generator, seed, unit
and template source for every field. No real trip is drawn where a trip is a model.

    python -m a2_data_pipeline.demo_dataset extract --archive .../runtime.tar.gz
    python -m a2_data_pipeline.demo_dataset build
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import math
import random
import tarfile
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from a2_data_pipeline import contract_v2, store
from a2_data_pipeline.paths import bundles_dir, repo_root

DATASET_ID = "waydex-demo-2026-11-11.1"
RULES_VERSION = "waydex-demo-rules-v1"
SEED = 20261111
SERVICE_DATE = date(2026, 11, 11)
LOCAL_TIME = time(8, 30)
GENERATED_AT = "2026-11-11T00:00:00Z"
DEMO_SOURCE = "DEMO_SYNTHETIC_TRANSPORT_MODEL"
LIMITATIONS = ("DEMO_SYNTHETIC_TRANSPORT_MODEL", "NOT_A_REAL_CITY_RANKING")
CLOCK = "local_service_midnight_v1"
REPO_URL = f"https://github.com/matveyka-sudo/case-champion-hac/tree/dev/data/demo/{DATASET_ID}"
EXTRACT_FORMAT = "waydex-demo-runtime-extract/1"
MODULE = "a2_data_pipeline.demo_dataset"

# the handoff package of 2026-11-11 (START-HERE.md, release-pins.json, artifact-receipt.json)
HANDOFF = {
    "runtime_archive_sha256": "3e97ff74113bf40503d7f35082ac268a620175a0edf9349c52490d1d7737ef47",
    "graph_transfer_sha256": "20c4561f881016502101bfac89933d52337fcf113437e05445d5b83137fa0ecc",
    "release_pins_sha256": "c080cef135e35b899d041fb6db9b2d990b4f320306a0d9def4a4e8ffdeb19b05",
    "bundle_manifest_sha256": "518b6cf31b799286a8bff364ad58d32cccb17bd96bc825d68f934c28ba836ee3",
    "product_manifest_sha256": "86158bdbb60633c05ee81b5466ffce4497aa60575004a42dd82050078f090ca2",
    "source_code": "3d225a2c41776c045b8eefe53ef9e60796f41942",
}
INTERCITY_DIRECTORY = "russia-intercity-nodes-2026-11-09.2"

CATEGORIES = (
    "adult_polyclinic",
    "child_polyclinic",
    "kindergarten",
    "school",
    "college_university",
    "rail_terminal",
    "bus_terminal",
    "airport",
    "shopping_center",
)
PUBLIC = {"adult_polyclinic", "child_polyclinic", "kindergarten", "school", "college_university"}
FAR_CATEGORIES = {"airport", "rail_terminal", "bus_terminal"}
CITIES: dict[str, dict[str, Any]] = {
    "moscow": {
        "bundle": "moscow-urban-2026-11-10.4",
        "tz": "Europe/Moscow",
        "centre": "moscow--kaliningrad--freeflow:origin-network",
        "radius_m": 5000.0,
        "car_kmh": 24.0,
        "stops": "mos_ground_transport GTFS stops (transit_stops.json)",
        "freight": ("model:logistics_terminal", "model:rail_terminal"),
    },
    "kaliningrad": {
        "bundle": "kaliningrad-urban-2026-11-10.5",
        "tz": "Europe/Kaliningrad",
        "centre": "kaliningrad--moscow--freeflow:origin-network",
        "radius_m": 3500.0,
        "car_kmh": 30.0,
        "stops": "gortrans39 stop lists (stop_frequencies.json)",
        "freight": ("real:port", "model:rail_terminal"),
    },
    "novorossiysk": {
        "bundle": "novorossiysk-urban-2026-11-10.3",
        "tz": "Europe/Moscow",
        "centre": "novorossiysk--moscow--freeflow:origin-network",
        "radius_m": 3500.0,
        "car_kmh": 30.0,
        "stops": "real walk-network projections (no accepted stop table)",
        "freight": ("real:port", "model:rail_terminal"),
    },
}
PAIRS = (
    ("moscow", "kaliningrad"),
    ("kaliningrad", "moscow"),
    ("moscow", "novorossiysk"),
    ("novorossiysk", "moscow"),
    ("kaliningrad", "novorossiysk"),
    ("novorossiysk", "kaliningrad"),
)

# model parameters (each one is listed in fields.json with its unit)
WALK_KMH = 5.0  # the accepted Valhalla walking profile waydex-walk-static-5kmh-v1
WALK_DETOUR = 1.3
CAR_DETOUR = 1.35
CAR_ACCESS_S = 120
BUS_KMH = 18.0
BUS_DWELL_S = 20
BUS_CAPACITY = 80
HEADWAY_A_S = 600
HEADWAY_B_S = 900
MIN_CHANGE_S = 60
COORDINATE_SHIFT_S = 120
EXTRA_SHIFT_S = 450
FOOTPATH_LIMIT_M = 2500.0
TRUCK_KMH = 25.0
ROAD_ACCESS_KMH = 35.0
PROCESS_S = {"air": 5400, "rail": 1200, "road": 900}
AIR_KMH = 780.0
AIR_TAXI_S = 1800
MCT_AIR_S = 3600
CARGO_PROFILE = "truck-model-20t-container-nonfood-ordinary"


# ----------------------------------------------------------------------------- helpers


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rng(*key: object) -> random.Random:
    digest = _sha(":".join(str(k) for k in (SEED, *key)).encode())
    return random.Random(int(digest[:16], 16))


def _dist(a: dict[str, float], b: dict[str, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def _walk_s(meters: float) -> int:
    return max(10, _up(meters * WALK_DETOUR / (WALK_KMH / 3.6), 10))


def _road_s(meters: float, kmh: float, detour: float = CAR_DETOUR) -> int:
    return max(60, _up(meters * detour / (kmh / 3.6), 10))


def _at(tz: str, seconds: int, day: date = SERVICE_DATE) -> datetime:
    return datetime.combine(day, time(0), ZoneInfo(tz)) + timedelta(seconds=seconds)


def _clock(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    text = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}"
    return text if not days else f"{text}+{days}d"


def _hhmm(seconds: int) -> str:
    return _clock(seconds).replace(":", "").replace("+", "p")


def _sec(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 3600 + int(m) * 60


READY_S = LOCAL_TIME.hour * 3600 + LOCAL_TIME.minute * 60


def _canonical(value: object) -> str:
    common = importlib.import_module("a2_contracts.common")
    return str(common.canonical_hash(value))


def _accepted(payload: dict[str, Any]) -> dict[str, Any]:
    dataset = importlib.import_module("a2_contracts.v2.dataset")
    return dict(dataset.AcceptedData.model_validate(payload).model_dump(mode="json"))


def _evidence(scope: str, purpose: str) -> dict[str, Any]:
    return {
        "evidence_id": f"demo-model:{scope}:{purpose}",
        "source_id": DEMO_SOURCE,
        "source_version": DATASET_ID,
        "url": REPO_URL,
        "retrieved_at": GENERATED_AT,
        "valid_period": {"start": SERVICE_DATE.isoformat(), "end": SERVICE_DATE.isoformat()},
        "license": "CC0-1.0 synthetic model data",
        "content_hash": _sha(f"{RULES_VERSION}:{SEED}:{scope}:{purpose}".encode()),
        "verified": True,
    }


def _location(row: dict[str, Any]) -> dict[str, float]:
    return {"lat": float(row["lat"]), "lon": float(row["lon"])}


def _nearest(
    point: dict[str, float], rows: Iterable[dict[str, Any]], used: set[str]
) -> dict[str, Any]:
    best = min(
        (r for r in rows if r["key"] not in used),
        key=lambda r: (_dist(point, r["location"]), r["key"]),
    )
    used.add(best["key"])
    return best


# ----------------------------------------------------------------------------- extract


def extract(archive: Path, out: Path) -> dict[str, Any]:
    """Read the pinned handoff runtime archive (JSON members only, nothing executed)."""

    digest = _file_sha(archive)
    if digest != HANDOFF["runtime_archive_sha256"]:
        raise ValueError(f"runtime archive sha256 {digest} is not the pinned handoff archive")
    members: dict[str, bytes] = {}
    wanted = {
        "manifest.json",
        "kaliningrad/geometry/walking-profile.json",
        "intercity-road/collection.json",
        "intercity-road/accepted-data.json",
        "intercity-road/kaliningrad--novorossiysk--freeflow-response.json",
        "intercity-road/novorossiysk--kaliningrad--freeflow-response.json",
        "cargo-road/collection.json",
        "cargo-road/kaliningrad/accepted-data.json",
        "cargo-road/novorossiysk/accepted-data.json",
        "cargo-road/kaliningrad/source-receipt.json",
        "cargo-road/novorossiysk/source-receipt.json",
    }
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar:
            if info.isfile() and info.name in wanted:
                handle = tar.extractfile(info)
                assert handle is not None
                members[info.name] = handle.read()
    missing = wanted - set(members)
    if missing:
        raise ValueError(f"runtime archive lacks {sorted(missing)}")
    load = {name: json.loads(data) for name, data in members.items()}
    runtime = load["manifest.json"]
    road = load["intercity-road/collection.json"]
    road_data = load["intercity-road/accepted-data.json"]
    rows = []
    for row in road["rows"]:
        item = {
            "name": row["name"],
            "origin": row["origin"],
            "destination": row["destination"],
            "http_status": row["http_status"],
            "request_sha256": row["request_sha256"],
            "response_sha256": row["response_sha256"],
            "retrieved_at": row["retrieved_at"],
        }
        if row["http_status"] == 200:
            item |= {"time_s": row["summary"]["time"], "length_km": row["summary"]["length"]}
        else:
            error = load[f"intercity-road/{row['name']}-response.json"]
            item |= {"error_code": error["error_code"], "error": error["error"]}
        rows.append(item)
    cargo = load["cargo-road/collection.json"]
    cargo_rows = []
    for row in cargo["rows"]:
        summary = row["response"]["trip"]["summary"]
        cargo_rows.append(
            [
                row["city"],
                row["site"],
                row["node"],
                row["direction"],
                row["http_status"],
                row["response_sha256"],
                row["retrieved_at"],
                summary["time"],
                summary["length"],
            ]
        )
    cargo_templates = {}
    for city in ("kaliningrad", "novorossiysk"):
        data = load[f"cargo-road/{city}/accepted-data.json"]
        truck = [e for e in data["evidence"] if e["evidence_id"].startswith("truck-response:")]
        template = {
            k: v
            for k, v in truck[0].items()
            if k not in ("evidence_id", "content_hash", "retrieved_at")
        }
        for evidence in truck:  # every truck evidence row is template + response hash + time
            rebuilt = template | {
                "evidence_id": evidence["evidence_id"],
                "content_hash": evidence["content_hash"],
                "retrieved_at": evidence["retrieved_at"],
            }
            if rebuilt != evidence:
                raise ValueError(f"{city} truck evidence is not uniform: {evidence['evidence_id']}")
        cargo_templates[city] = {
            "snapshot_id": data["snapshot_id"],
            "evidence_template": template,
            "receipt": load[f"cargo-road/{city}/source-receipt.json"],
        }
    payload = {
        "format": EXTRACT_FORMAT,
        "archive_sha256": digest,
        "member_sha256": {name: _sha(members[name]) for name in sorted(members)},
        "runtime_snapshots": [
            {k: s[k] for k in ("snapshot_id", "content_hash", "sha256", "path")}
            for s in runtime["snapshots"]
        ],
        "runtime_profiles": [
            {
                k: p[k]
                for k in (
                    "profile_id",
                    "analysis_kind",
                    "city_id",
                    "destination_id",
                    "passenger_modes",
                    "categories",
                    "executor",
                    "snapshot_id",
                    "valid_from",
                    "valid_to",
                    "cargo_profile_id",
                    "direction",
                )
            }
            for p in runtime["profiles"]
        ],
        "cargo_profiles": runtime["cargo_profiles"],
        "walking_profile": load["kaliningrad/geometry/walking-profile.json"],
        "road": {
            "snapshot_id": road_data["snapshot_id"],
            "source_versions": road_data["source_versions"],
            "evidence": road_data["evidence"],
            "access_nodes": road_data["access_nodes"],
            "graph_version": road_data["provenance"]["graph_version"],
            "rows": rows,
        },
        "cargo": {
            "vehicle_model": cargo["vehicle_model"],
            "columns": [
                "city",
                "site",
                "node",
                "direction",
                "http_status",
                "response_sha256",
                "retrieved_at",
                "time_s",
                "length_km",
            ],
            "rows": cargo_rows,
            "cities": cargo_templates,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    return payload


# ----------------------------------------------------------------------------- real inputs


def _real_snapshot(city: str, ext: dict[str, Any], bundle_root: Path | None) -> dict[str, Any]:
    bundle = str(CITIES[city]["bundle"])
    data = store.load_bundle(bundle, root=bundle_root)
    payload = _accepted(contract_v2.to_accepted_data_v2(data))
    pinned = {s["snapshot_id"]: s["content_hash"] for s in ext["runtime_snapshots"]}
    if pinned.get(bundle) != _canonical(payload):
        raise ValueError(f"{bundle} content hash differs from the handoff runtime manifest")
    return payload


def _stop_candidates(
    city: str, real: dict[str, Any], bundle_root: Path | None
) -> list[dict[str, Any]]:
    base = (bundle_root or bundles_dir()) / str(CITIES[city]["bundle"])
    if city == "moscow":
        rows = json.loads((base / "transit_stops.json").read_text())
        return [
            {
                "key": r["stop_id"],
                "name": r["name"],
                "location": _location(r),
                "template": r["stop_id"],
            }
            for r in rows
            if r["status"] == "accepted"
        ]
    if city == "kaliningrad":
        rows = json.loads((base / "stop_frequencies.json").read_text())
        seen: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["lat"] is None or r["lon"] is None:  # stop names the source did not locate
                continue
            key = f"gortrans39:{r['stop_name']}:{r['lat']:.6f}:{r['lon']:.6f}"
            seen.setdefault(
                key,
                {
                    "key": key,
                    "name": r["stop_name"],
                    "location": _location(r),
                    "template": r["frequency_id"],
                },
            )
        return list(seen.values())
    return [
        {"key": n["node_id"], "name": None, "location": n["location"], "template": n["node_id"]}
        for n in real["access_nodes"]
        if n["kind"] == "walk_network_projection"
    ]


class _Copier:
    """Copy real rows with their evidence and source versions; nothing is altered."""

    def __init__(self, real: dict[str, Any]) -> None:
        self.real = real
        self.evidence = {e["evidence_id"]: e for e in real["evidence"]}
        self.nodes = {n["node_id"]: n for n in real["access_nodes"]}
        self.refs: set[str] = set()
        self.node_ids: set[str] = set()

    def take(self, row: dict[str, Any]) -> dict[str, Any]:
        self.refs.update(row.get("source_refs", ()))
        for key in ("geometry",):
            if isinstance(row.get(key), dict):
                self.refs.update(row[key].get("source_refs", ()))
        if "access_node_id" in row:
            self.node_ids.add(row["access_node_id"])
        return row

    def access_nodes(self) -> list[dict[str, Any]]:
        rows = [self.nodes[n] for n in sorted(self.node_ids) if n in self.nodes]
        for row in rows:
            self.refs.update(row["source_refs"])
        return rows

    def evidence_rows(self) -> list[dict[str, Any]]:
        return [self.evidence[e] for e in sorted(self.refs)]

    def source_versions(self) -> dict[str, str]:
        return {
            e["source_id"]: self.real["source_versions"][e["source_id"]]
            for e in self.evidence_rows()
        }


# ----------------------------------------------------------------------------- city part


def _origins(real: dict[str, Any], centre: dict[str, float], radius: float) -> list[dict[str, Any]]:
    cells = {c["cell_id"]: c for c in real["population_cells"]}
    candidates = [
        (p, cells[p["cell_id"]])
        for p in real["sample_points"]
        if cells[p["cell_id"]]["population"] and _dist(p["location"], centre) <= radius
    ]
    candidates.sort(key=lambda pc: (-pc[1]["population"], pc[0]["point_id"]))
    for spacing in (1000.0, 600.0, 0.0):
        chosen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for point, cell in candidates:
            if all(_dist(point["location"], q["location"]) >= spacing for q, _ in chosen):
                chosen.append((point, cell))
            if len(chosen) == 6:
                return [{"point": p, "cell": c} for p, c in chosen]
    raise ValueError("fewer than six populated sample points near the centre")


def _destinations(
    city: str, real: dict[str, Any], centre: dict[str, float]
) -> tuple[list[dict[str, Any]], dict[str, set[str]], list[dict[str, Any]]]:
    picked: dict[str, set[str]] = {}
    models: list[dict[str, Any]] = []
    walk_nodes = sorted(
        (n for n in real["access_nodes"] if n["kind"] == "walk_network_projection"),
        key=lambda n: n["node_id"],
    )
    by_id = {d["destination_id"]: d for d in real["destinations"]}
    for category in CATEGORIES:
        rows = sorted(
            (d for d in real["destinations"] if category in d["categories"]),
            key=lambda d: (_dist(d["location"], centre), d["destination_id"]),
        )
        objects: set[str] = set()
        count = 0
        for row in rows:
            if row["object_identity"] in objects:
                continue
            objects.add(row["object_identity"])
            picked.setdefault(row["destination_id"], set()).add(category)
            count += 1
            if count == 2:
                break
        for index in range(count, 2):
            rng = _rng(city, "model-destination", category, index)
            low, high = (3000, 6000) if category in FAR_CATEGORIES else (1000, 3000)
            bearing, reach = rng.uniform(0, 2 * math.pi), rng.uniform(low, high)
            target = {
                "lat": centre["lat"] + reach * math.cos(bearing) / 111320.0,
                "lon": centre["lon"]
                + reach * math.sin(bearing) / (111320.0 * math.cos(math.radians(centre["lat"]))),
            }
            node = min(walk_nodes, key=lambda n: (_dist(target, n["location"]), n["node_id"]))
            models.append(
                {
                    "destination_id": f"demo-{city}-{category}-model-{index + 1}",
                    "category": category,
                    "node": node,
                    "object_identity": f"demo-model:{city}:{category}:{index + 1}",
                }
            )
    chosen = [by_id[d] for d in sorted(picked)]
    return chosen, picked, models


def _first(departures: list[tuple[int, str]], ready: int) -> tuple[int, str] | None:
    return next((d for d in sorted(departures) if d[0] >= ready), None)


def _city_part(city: str, ext: dict[str, Any], bundle_root: Path | None) -> dict[str, Any]:
    cfg = CITIES[city]
    tz = str(cfg["tz"])
    real = _real_snapshot(city, ext, bundle_root)
    snapshot_id = f"demo-{city}-2026-11-11.1"
    road_nodes = {n["node_id"]: n for n in ext["road"]["access_nodes"]}
    centre = road_nodes[str(cfg["centre"])]["location"]
    copier = _Copier(real)
    territory = copier.take(real["territories"][0])
    proof = {
        p: _evidence(city, p)
        for p in (
            "schedule",
            "calendar",
            "footpaths",
            "transfers",
            "stops",
            "destinations",
            "edges",
            "demand",
            "resource",
            "turnaround",
            "freight",
            "handling",
        )
    }

    # origins: six real sample points of the most populated cells near the centre
    origins = _origins(real, centre, float(cfg["radius_m"]))
    cells = [copier.take(o["cell"]) for o in origins]
    points = [copier.take(o["point"]) for o in origins]

    # destinations: two per category, real where the accepted snapshot has them
    real_dest, picked, models = _destinations(city, real, centre)
    destinations = []
    for row in real_dest:
        keep = [c for c in row["categories"] if c in picked[row["destination_id"]]]
        destinations.append(copier.take(row | {"categories": keep}))
    selected = {(d["destination_id"], c) for d in destinations for c in d["categories"]}
    ownership = [
        copier.take(o)
        for o in real["category_ownership"]
        if (o["destination_id"], o["category"]) in selected
    ]
    registry_ids = {r for o in ownership for r in o["registry_evidence_ids"]}
    registry = [
        copier.take(r)
        for r in real["registry_evidence"]
        if r["registry_evidence_id"] in registry_ids
    ]
    for model in models:
        node = model["node"]
        copier.node_ids.add(node["node_id"])
        destinations.append(
            {
                "destination_id": model["destination_id"],
                "territory_id": city,
                "location": node["location"],
                "categories": [model["category"]],
                "access_node_id": node["node_id"],
                "object_identity": model["object_identity"],
                "source_refs": [proof["destinations"]["evidence_id"]],
            }
        )
        if model["category"] in PUBLIC:
            registry_id = f"demo-model-registry:{model['destination_id']}"
            registry.append(
                {
                    "registry_evidence_id": registry_id,
                    "registry_id": "demo_model_registry",
                    "destination_id": model["destination_id"],
                    "category": model["category"],
                    "ownership": "municipal",
                    "verified": True,
                    "source_refs": [proof["destinations"]["evidence_id"]],
                }
            )
            ownership.append(
                {
                    "destination_id": model["destination_id"],
                    "category": model["category"],
                    "ownership": "municipal",
                    "registry_evidence_ids": [registry_id],
                    "source_refs": [proof["destinations"]["evidence_id"]],
                }
            )
    dest_by_id = {d["destination_id"]: d for d in destinations}
    model_dest_ids = {m["destination_id"] for m in models}

    # public transport: two model routes over real stop positions
    candidates = _stop_candidates(city, real, bundle_root)
    used: set[str] = set()
    ordered = sorted(
        origins, key=lambda o: (-_dist(o["point"]["location"], centre), o["point"]["point_id"])
    )
    far = ordered[0]["point"]
    corridor = sorted(
        ordered[1:],
        key=lambda o: (_dist(o["point"]["location"], far["location"]), o["point"]["point_id"]),
    )[:2]
    a_origins = sorted(
        [far, *(o["point"] for o in corridor)],
        key=lambda p: (-_dist(p["location"], centre), p["point_id"]),
    )
    a_cands = [_nearest(p["location"], candidates, used) for p in a_origins]
    hub_a = _nearest(centre, candidates, used)
    hub_b = _nearest(hub_a["location"], candidates, used)
    reachable = [
        d
        for d in destinations
        if not set(d["categories"]) & FAR_CATEGORIES
        and _dist(d["location"], hub_b["location"]) <= 3000
    ]
    b_targets = sorted(
        reachable, key=lambda d: (-_dist(d["location"], hub_b["location"]), d["destination_id"])
    )[:3]
    b_cands, last = [], hub_b["location"]
    remaining = list(b_targets)
    while remaining:  # nearest-neighbour order from the hub
        nxt = min(remaining, key=lambda d: (_dist(last, d["location"]), d["destination_id"]))
        remaining.remove(nxt)
        b_cands.append(_nearest(nxt["location"], candidates, used))
        last = nxt["location"]
    routes = {"A": [*a_cands, hub_a], "B": [hub_b, *b_cands]}
    stops = []
    for route, rows in routes.items():
        for index, cand in enumerate(rows, start=1):
            stops.append(
                {
                    "node_id": f"demo-{city}-stop-{route}{index}",
                    "route": route,
                    "sequence": index,
                    "name": cand["name"],
                    "location": cand["location"],
                    "template_source": cand["template"],
                }
            )
    stop_loc = {s["node_id"]: s["location"] for s in stops}
    route_stops = {r: [s["node_id"] for s in stops if s["route"] == r] for r in routes}

    def segments(route: str) -> list[int]:
        ids = route_stops[route]
        return [
            max(60, _up(_dist(stop_loc[a], stop_loc[b]) * WALK_DETOUR / (BUS_KMH / 3.6), 30))
            for a, b in zip(ids, ids[1:], strict=False)
        ]

    def events(route: str, depart: int) -> list[tuple[str, int, int]]:
        out, clock = [], depart
        for index, node in enumerate(route_stops[route]):
            if index:
                clock += segments(route)[index - 1]
            arrival = clock
            departure = clock + (BUS_DWELL_S if 0 < index < len(route_stops[route]) - 1 else 0)
            out.append((node, arrival, departure))
            clock = departure
        return out

    origin_node = {p["point_id"]: p["access_node_id"] for p in points}
    hub_walk = _walk_s(_dist(stop_loc[route_stops["A"][-1]], stop_loc[route_stops["B"][0]]))
    access_paths: dict[str, list[tuple[str, int]]] = {}
    for p in points:
        near = sorted(stops, key=lambda s: (_dist(p["location"], s["location"]), s["node_id"]))[:2]
        access_paths[p["point_id"]] = [
            (s["node_id"], _walk_s(_dist(p["location"], s["location"])))
            for s in near
            if _dist(p["location"], s["location"]) <= FOOTPATH_LIMIT_M
        ]
    egress_paths: dict[str, list[tuple[str, int]]] = {}
    for d in destinations:
        near = sorted(stops, key=lambda s: (_dist(d["location"], s["location"]), s["node_id"]))[:2]
        paths = [
            (s["node_id"], _walk_s(_dist(d["location"], s["location"])))
            for s in near
            if _dist(d["location"], s["location"]) <= FOOTPATH_LIMIT_M
        ]
        if paths:
            egress_paths[d["destination_id"]] = paths

    a_first = 7 * 3600
    a_trips = {
        f"demo-{city}-trip-A-{_hhmm(a_first + k * HEADWAY_A_S)}": events(
            "A", a_first + k * HEADWAY_A_S
        )
        for k in range(24)
    }
    target_point = a_origins[0]
    walk_to_a1 = (
        next(w for s, w in access_paths[target_point["point_id"]] if s == route_stops["A"][0])
        if any(s == route_stops["A"][0] for s, _ in access_paths[target_point["point_id"]])
        else _walk_s(_dist(target_point["location"], stop_loc[route_stops["A"][0]]))
    )
    access_paths[target_point["point_id"]] = sorted(
        {*access_paths[target_point["point_id"]], (route_stops["A"][0], walk_to_a1)}
    )
    board = _first([(ev[0][2], trip) for trip, ev in a_trips.items()], READY_S + walk_to_a1)
    assert board is not None
    a_trip = board[1]
    hub_arrival = a_trips[a_trip][-1][1]
    planned = hub_arrival + hub_walk  # leaves one minute before the change rule is met
    b_offsets = [planned + j * HEADWAY_B_S for j in range(-6, 12)]
    b_departs = [d for d in b_offsets if 6 * 3600 + 1800 <= d <= 11 * 3600 + 1800]
    b_trips = {f"demo-{city}-trip-B-{_hhmm(d)}": events("B", d) for d in b_departs}
    planned_trip = f"demo-{city}-trip-B-{_hhmm(planned)}"
    next_trip = f"demo-{city}-trip-B-{_hhmm(planned + HEADWAY_B_S)}"
    extra_trip = f"demo-{city}-trip-B-{_hhmm(planned + EXTRA_SHIFT_S)}-extra"

    def shifted(
        trips: dict[str, list[tuple[str, int, int]]], trip: str, shift: int, new: str | None
    ) -> dict[str, list[tuple[str, int, int]]]:
        out = dict(trips)
        moved = [(n, a + shift, d + shift) for n, a, d in trips[trip]]
        out[trip if new is None else new] = moved
        return out

    coord_b = shifted(b_trips, planned_trip, COORDINATE_SHIFT_S, None)
    extra_b = shifted(b_trips, planned_trip, EXTRA_SHIFT_S, extra_trip)

    trips_all = a_trips | b_trips
    services = []
    for trip, evs in sorted(trips_all.items()):
        services.append(
            {
                "service_id": trip.replace("-trip-", "-svc-"),
                "trip_id": trip,
                "mode": "public_transport",
                "timezone": tz,
                "service_dates": [SERVICE_DATE.isoformat()],
                "trip_identity_confirmed": True,
                "stop_times": [
                    {
                        "node_id": n,
                        "sequence": i,
                        "arrival_seconds": a,
                        "departure_seconds": d,
                        "pickup": i < len(evs) - 1,
                        "dropoff": i > 0,
                    }
                    for i, (n, a, d) in enumerate(evs)
                ],
                "source_refs": [proof["schedule"]["evidence_id"]],
            }
        )
    stop_nodes = [
        {
            "node_id": s["node_id"],
            "kind": "demo_transit_stop",
            "location": s["location"],
            "territory_id": city,
            "source_refs": [proof["stops"]["evidence_id"]],
        }
        for s in stops
    ]
    transfers = [
        {
            "transfer_id": f"demo-{city}-transfer-A{len(route_stops['A'])}-B1",
            "from_node_id": route_stops["A"][-1],
            "to_node_id": route_stops["B"][0],
            "seconds": float(hub_walk),
            "verification_state": "verified",
            "source_refs": [proof["transfers"]["evidence_id"]],
        },
        {
            "transfer_id": f"demo-{city}-transfer-B1-A{len(route_stops['A'])}",
            "from_node_id": route_stops["B"][0],
            "to_node_id": route_stops["A"][-1],
            "seconds": float(hub_walk),
            "verification_state": "verified",
            "source_refs": [proof["transfers"]["evidence_id"]],
        },
    ] + [
        {  # a change on the same stop
            "transfer_id": f"demo-{city}-transfer-{s['node_id'].rsplit('-', 1)[1]}-same",
            "from_node_id": s["node_id"],
            "to_node_id": s["node_id"],
            "seconds": 0.0,
            "verification_state": "verified",
            "source_refs": [proof["transfers"]["evidence_id"]],
        }
        for s in stops
    ]
    minimums = [
        {
            "minimum_id": t["transfer_id"].replace("-transfer-", "-minimum-"),
            "from_node_id": t["from_node_id"],
            "to_node_id": t["to_node_id"],
            "seconds": float(t["seconds"] + MIN_CHANGE_S),
            "applicability": ["public_transport", "alight_to_departure"],
            "source_refs": [proof["transfers"]["evidence_id"]],
        }
        for t in transfers
    ]

    # walk and car: connectivity and edge times origin -> destination
    edges = []
    walk_rows: list[dict[str, Any]] = []
    car_rows: list[dict[str, Any]] = []
    for p in points:
        for d in destinations:
            meters = _dist(p["location"], d["location"])
            for mode, table in (("walk", walk_rows), ("private_car", car_rows)):
                if mode == "walk":
                    length, seconds = meters * WALK_DETOUR, _walk_s(meters)
                else:
                    length = meters * CAR_DETOUR
                    seconds = _road_s(meters, float(cfg["car_kmh"])) + CAR_ACCESS_S
                edge = {
                    "edge_id": f"demo-{city}-{mode}:{p['point_id']}->{d['destination_id']}",
                    "from_node_id": p["access_node_id"],
                    "to_node_id": d["access_node_id"],
                    "mode": mode,
                    "length_m": round(length, 1),
                    "running_time_seconds": float(seconds),
                    "calibration_method": f"{RULES_VERSION}:haversine-detour",
                    "geometry": None,
                    "source_refs": [proof["edges"]["evidence_id"]],
                }
                edges.append(edge)
                table.append(
                    {
                        "edge_id": edge["edge_id"],
                        "origin_point_id": p["point_id"],
                        "destination_id": d["destination_id"],
                        "categories": "|".join(d["categories"]),
                        "straight_m": round(meters, 1),
                        "length_m": edge["length_m"],
                        "time_s": seconds,
                        "origin": "synthetic",
                    }
                )

    # urban freight: three real sites, two compatible nodes
    cargo_cols = ext["cargo"]["columns"]
    observed = [
        dict(zip(cargo_cols, r, strict=True))
        for r in ext["cargo"]["rows"]
        if r[0] == city and r[4] == 200
    ]
    real_fnodes = {n["freight_node_id"]: n for n in real["freight_nodes"]}
    freight_nodes, freight_model = [], []
    road_access = sorted(
        (n for n in real["access_nodes"] if n["kind"] == "road_network_projection"),
        key=lambda n: n["node_id"],
    )
    for index, spec in enumerate(cfg["freight"]):
        origin, kind = str(spec).split(":")
        if origin == "real":
            counts: dict[str, int] = {}
            for row in observed:
                if real_fnodes.get(row["node"], {}).get("kind") == kind:
                    counts[row["node"]] = counts.get(row["node"], 0) + 1
            node_id = min(counts, key=lambda n: (-counts[n], n))
            freight_nodes.append(copier.take(real_fnodes[node_id]))
        else:
            rng = _rng(city, "model-freight-node", kind)
            band = [
                n for n in road_access if 6000 <= _dist(n["location"], centre) <= 14000
            ] or road_access
            node = band[rng.randrange(len(band))]
            copier.node_ids.add(node["node_id"])
            freight_nodes.append(
                {
                    "freight_node_id": f"demo-{city}-{kind.replace('_', '-')}-model",
                    "kind": kind,
                    "access_node_id": node["node_id"],
                    "location": node["location"],
                    "territory_id": city,
                    "source_refs": [proof["freight"]["evidence_id"]],
                }
            )
        rng = _rng(city, "handling", index)
        units = rng.randint(2, 4)
        freight_model.append(
            {
                "freight_node_id": freight_nodes[-1]["freight_node_id"],
                "kind": kind,
                "origin": origin if origin == "model" else "real_location_model_operations",
                "handling_types": ["container", "bulk"]
                if kind == "port"
                else ["container", "pallet"]
                if kind == "logistics_terminal"
                else ["container"],
                "max_mass_t": 40.0 if kind == "port" else 30.0,
                "max_height_m": 4.5,
                "approach_directions": ["inbound", "outbound"],
                "gate_window_local": ["06:00", "22:00"],
                "base_wait_s": _up(rng.uniform(2100, 3300), 60),
                "handling_s": _up(rng.uniform(1500, 2400), 60),
                "resource": {
                    "equipment": "reach_stacker",
                    "units": units,
                    "slots_per_hour": units * 3,
                },
            }
        )
    first_node = freight_nodes[0]
    obs_sites = {r["site"] for r in observed if r["node"] == first_node["freight_node_id"]}
    site_pool = [s for s in real["freight_sites"] if not obs_sites or s["site_id"] in obs_sites]
    sites = [
        copier.take(s)
        for s in sorted(site_pool, key=lambda s: (_dist(s["location"], centre), s["site_id"]))[:3]
    ]
    template = ext["cargo"]["cities"].get(city, {}).get("evidence_template")
    obs_index = {(r["site"], r["node"], r["direction"]): r for r in observed}
    compat, freight_edges, freight_edge_rows = [], [], []
    extra_evidence: list[dict[str, Any]] = []
    extra_versions: dict[str, str] = {}
    for site in sites:
        for node in freight_nodes:
            for direction in ("outbound", "inbound"):
                compat.append(
                    {
                        "compatibility_id": f"demo-{city}-compat:{site['site_id']}:{node['freight_node_id']}:{direction}",
                        "site_id": site["site_id"],
                        "freight_node_id": node["freight_node_id"],
                        "direction": direction,
                        "food_classes": ["nonfood"],
                        "temperature_requirements": ["ambient"],
                        "handling_requirements": ["container"],
                        "vehicle_constraints": ["max_weight_t:20", "max_height_m:4.0"],
                        "compatible": True,
                        "source_refs": [proof["freight"]["evidence_id"]],
                    }
                )
                obs = obs_index.get((site["site_id"], node["freight_node_id"], direction))
                ends = (site, node) if direction == "outbound" else (node, site)
                if obs is not None and template is not None:
                    evidence_id = f"truck-response:{obs['response_sha256']}"
                    extra_evidence.append(
                        template
                        | {
                            "evidence_id": evidence_id,
                            "content_hash": obs["response_sha256"],
                            "retrieved_at": obs["retrieved_at"],
                        }
                    )
                    extra_versions[template["source_id"]] = template["source_version"]
                    f_length, f_seconds, method, refs, origin = (
                        obs["length_km"] * 1000,
                        obs["time_s"],
                        "valhalla-truck-freeflow-observation",
                        [evidence_id],
                        "real_observation",
                    )
                else:
                    meters = _dist(site["location"], node["location"])
                    f_length, f_seconds, method, refs, origin = (
                        meters * CAR_DETOUR,
                        float(_road_s(meters, TRUCK_KMH)),
                        f"{RULES_VERSION}:haversine-detour",
                        [proof["freight"]["evidence_id"]],
                        "synthetic",
                    )
                edge_id = (
                    f"demo-{city}-truck:{site['site_id']}:{node['freight_node_id']}:{direction}"
                )
                freight_edges.append(
                    {
                        "edge_id": edge_id,
                        "from_node_id": ends[0]["access_node_id"],
                        "to_node_id": ends[1]["access_node_id"],
                        "mode": "road",
                        "length_m": round(f_length, 1),
                        "running_time_seconds": round(float(f_seconds), 3),
                        "calibration_method": method,
                        "geometry": None,
                        "source_refs": refs,
                    }
                )
                freight_edge_rows.append(
                    {
                        "edge_id": edge_id,
                        "site_id": site["site_id"],
                        "freight_node_id": node["freight_node_id"],
                        "direction": direction,
                        "length_m": round(f_length, 1),
                        "time_s": round(float(f_seconds), 3),
                        "origin": origin,
                    }
                )

    # assemble the contract snapshot
    access_nodes = copier.access_nodes() + stop_nodes
    evidence = (
        copier.evidence_rows()
        + sorted(
            {e["evidence_id"]: e for e in extra_evidence}.values(), key=lambda e: e["evidence_id"]
        )
        + list(proof.values())
    )
    versions = copier.source_versions() | extra_versions | {DEMO_SOURCE: DATASET_ID}
    qa_checks = [DEMO_SOURCE, "NOT_A_REAL_CITY_RANKING", f"{RULES_VERSION}:qa"]
    snapshot = _accepted(
        {
            "schema_version": "2",
            "snapshot_id": snapshot_id,
            "source_versions": dict(sorted(versions.items())),
            "geographic_scope": [city],
            "valid_period": {"start": SERVICE_DATE.isoformat(), "end": SERVICE_DATE.isoformat()},
            "timezone": tz,
            "provenance": {
                "graph_version": f"{snapshot_id}:graph",
                "geometry_version": real["provenance"]["geometry_version"],
                "calendar_version": f"{snapshot_id}:calendar",
                "population_version": real["provenance"]["population_version"],
            },
            "qa": {
                "acceptance_state": "accepted",
                "accepted_by": f"ML2 {RULES_VERSION} synthetic demo generator; model data, not a real acceptance",
                "accepted_at": GENERATED_AT,
                "checks": qa_checks,
            },
            "evidence": evidence,
            "registry_evidence": registry,
            "territories": [territory],
            "population_cells": cells,
            "sample_points": points,
            "destinations": destinations,
            "access_nodes": access_nodes,
            "scheduled_services": services,
            "transfers": transfers,
            "freight_sites": sites,
            "freight_nodes": freight_nodes,
            "freight_compatibility": compat,
            "pipeline_connections": [],
            "reference_edges": edges + freight_edges,
            "minimum_transfers": minimums,
            "traffic_profiles": [],
            "category_ownership": ownership,
            "coverage_by_mode_date_category": [
                {
                    "territory_ids": [city],
                    "modes": ["walk", "private_car", "public_transport"],
                    "categories": list(CATEGORIES),
                    "valid_period": {
                        "start": SERVICE_DATE.isoformat(),
                        "end": SERVICE_DATE.isoformat(),
                    },
                    "completeness": "partial",
                    "source_refs": [proof["destinations"]["evidence_id"]],
                }
            ],
            "confirmed_absence_scope": [],
        }
    )

    # closed synthetic timetable model for the DEMO transport receipt
    engine = importlib.import_module("a2_engine")
    pid = proof["schedule"]["evidence_id"]

    def run_of(trip: str, evs: list[tuple[str, int, int]]) -> Any:
        return engine.TimetableRun(
            snapshot_id,
            DEMO_SOURCE,
            trip,
            SERVICE_DATE,
            tz,
            "bus",
            tuple(
                engine.TimetableEvent(
                    i, n, _at(tz, arr), _at(tz, dep), i < len(evs) - 1, i > 0, (pid,)
                )
                for i, (n, arr, dep) in enumerate(evs)
            ),
            True,
            (pid, proof["calendar"]["evidence_id"]),
        )

    fp_ref = (proof["footpaths"]["evidence_id"],)
    footpaths = []
    for point_id, paths in access_paths.items():
        for stop, seconds in paths:
            footpaths.append(
                engine.TimetableFootpath(
                    f"demo-{city}-fp:{point_id}->{stop}",
                    origin_node[point_id],
                    stop,
                    float(seconds),
                    True,
                    fp_ref,
                )
            )
    for dest_id, paths in egress_paths.items():
        for stop, seconds in paths:
            footpaths.append(
                engine.TimetableFootpath(
                    f"demo-{city}-fp:{stop}->{dest_id}",
                    stop,
                    dest_by_id[dest_id]["access_node_id"],
                    float(seconds),
                    True,
                    fp_ref,
                )
            )
    for t in transfers:
        if t["from_node_id"] != t["to_node_id"]:
            footpaths.append(
                engine.TimetableFootpath(
                    f"demo-{city}-fp:{t['from_node_id']}->{t['to_node_id']}",
                    t["from_node_id"],
                    t["to_node_id"],
                    t["seconds"],
                    True,
                    fp_ref,
                )
            )
    rules = [
        engine.TimetableTransfer(
            m["minimum_id"],
            m["from_node_id"],
            m["to_node_id"],
            m["seconds"],
            True,
            (proof["transfers"]["evidence_id"],),
        )
        for m in minimums
    ]
    nodes = {n for evs in (a_trips | b_trips | extra_b).values() for n, _, _ in evs}
    nodes |= {f.from_node_id for f in footpaths} | {f.to_node_id for f in footpaths}
    scope = engine.TimetableScope(
        snapshot_id,
        snapshot["provenance"]["graph_version"],
        snapshot["provenance"]["calendar_version"],
        f"{snapshot_id}:timetable",
        f"{snapshot_id}:footpaths",
        f"{snapshot_id}:transfers",
        tz,
        _at(tz, 6 * 3600),
        _at(tz, 13 * 3600),
        frozenset(nodes),
        True,
        True,
        True,
        True,
        True,
        (pid,),
        (),
    )

    def table_of(trips: dict[str, list[tuple[str, int, int]]]) -> Any:
        return engine.Timetable(
            scope,
            tuple(run_of(t, e) for t, e in sorted(trips.items())),
            tuple(footpaths),
            tuple(rules),
        )

    table = table_of(a_trips | b_trips)

    # expected results come from the ML1 engine itself (a2_engine.solve_timetable)
    trip_by_run = {
        run_of(t, e).run_id: t for t, e in (a_trips | b_trips | coord_b | extra_b).items()
    }

    def solve(
        trips: dict[str, list[tuple[str, int, int]]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        variant = table_of(trips)
        out = {}
        for p in points:
            for d in destinations:
                if (
                    p["access_node_id"] == d["access_node_id"]
                    or not {p["access_node_id"], d["access_node_id"]} <= scope.node_ids
                ):
                    continue  # same node, or no footpath within the model limit
                res = engine.solve_timetable(
                    variant,
                    p["access_node_id"],
                    d["access_node_id"],
                    _at(tz, READY_S),
                    max_wait_seconds=3600,
                )
                path = res.best_known
                if path is None:
                    continue
                waits = {
                    k: sum(s.duration_seconds for s in path.steps if s.kind == k)
                    for k in ("wait_initial", "wait_transfer")
                }
                out[(p["point_id"], d["destination_id"])] = {
                    "state": res.state,
                    "total_s": path.total_seconds,
                    "initial_wait_s": waits["wait_initial"],
                    "transfer_wait_s": waits["wait_transfer"],
                    "transfers": path.transfers,
                    "trip_ids": list(
                        dict.fromkeys(
                            trip_by_run[s.run_id]
                            for s in path.steps
                            if s.kind == "ride" and s.run_id
                        )
                    ),
                    "reasons": list(res.reasons),
                }
        return out

    base_m, coord_m, extra_m = (
        solve(a_trips | b_trips),
        solve(a_trips | coord_b),
        solve(a_trips | extra_b),
    )
    gains = [
        (
            min(
                base_m[k]["total_s"] - coord_m[k]["total_s"],
                base_m[k]["total_s"] - extra_m[k]["total_s"],
            ),
            k,
        )
        for k in base_m
        if k in coord_m
        and k in extra_m
        and planned_trip in base_m[k]["trip_ids"] + coord_m[k]["trip_ids"]
        and base_m[k]["transfers"] >= 1
    ]
    gains = [g for g in gains if g[0] > 0]
    if not gains:
        raise ValueError(f"{city}: no origin-destination pair uses the problem transfer")
    target_key = max(gains, key=lambda g: (g[0], g[1]))[1]

    def diff(after: dict[tuple[str, str], dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        worse, better = [], []
        for key, row in sorted(base_m.items()):
            value = after.get(key)
            if value is None:
                continue
            item = {
                "origin_point_id": key[0],
                "destination_id": key[1],
                "before_s": row["total_s"],
                "after_s": value["total_s"],
            }
            if value["total_s"] > row["total_s"]:
                worse.append(item)
            elif value["total_s"] < row["total_s"]:
                better.append(item)
        return {"worse": worse, "better": better}

    pydantic = importlib.import_module("pydantic")
    model = pydantic.TypeAdapter(engine.Timetable).dump_python(table, mode="json")
    model["scope"]["node_ids"] = sorted(model["scope"]["node_ids"])
    receipt = {
        "schema_version": "waydex-demo-closed-transport/1",
        "snapshot_hash": _canonical(snapshot),
        "model": model,
        "limitations": sorted(LIMITATIONS),
    }

    # model scenario inputs (demand, resource, turnaround) and expected checks
    b_run = sum(segments("B")) + BUS_DWELL_S * (len(route_stops["B"]) - 2)
    load = round(_rng(city, "demand").uniform(0.93, 1.07), 3)
    capacity_hour = BUS_CAPACITY * 3600 // HEADWAY_B_S
    population = sum(float(c["population"]) for c in cells)
    pt_model = {
        "routes": [
            {
                "route_id": f"demo-{city}-route-{r}",
                "stops": route_stops[r],
                "headway_s": HEADWAY_A_S if r == "A" else HEADWAY_B_S,
                "segment_run_s": segments(r),
                "dwell_s": BUS_DWELL_S,
                "vehicle_capacity_persons": BUS_CAPACITY,
                "trip_ids": sorted(a_trips if r == "A" else b_trips),
            }
            for r in ("A", "B")
        ],
        "problem_transfer": {
            "transfer_id": transfers[0]["transfer_id"],
            "minimum_id": minimums[0]["minimum_id"],
            "from_stop": route_stops["A"][-1],
            "to_stop": route_stops["B"][0],
            "walk_s": hub_walk,
            "min_change_s": MIN_CHANGE_S,
            "arriving_trip_id": a_trip,
            "arrival_local": _clock(hub_arrival),
            "planned_trip_id": planned_trip,
            "planned_departure_local": _clock(planned),
            "slack_s": planned - (hub_arrival + hub_walk + MIN_CHANGE_S),
            "base_wait_s": planned + HEADWAY_B_S - (hub_arrival + hub_walk),
            "slack_after_coordinate_s": planned
            + COORDINATE_SHIFT_S
            - (hub_arrival + hub_walk + MIN_CHANGE_S),
            "next_trip_id": next_trip,
        },
        "target": {
            "origin_point_id": target_key[0],
            "destination_id": target_key[1],
            "ready_local": _clock(READY_S),
            "selection": "pair with the largest a2_engine gain under both operations that rides the planned trip after a change",
        },
        "extra_departure_slot": {
            "template_trip_id": planned_trip,
            "shift_s": EXTRA_SHIFT_S,
            "new_trip_id": extra_trip,
            "departure_local": _clock(planned + EXTRA_SHIFT_S),
        },
        "demand": {
            "route_id": f"demo-{city}-route-B",
            "window_local": ["08:00", "09:00"],
            "demand_persons_per_hour": round(load * capacity_hour),
            "capacity_persons_per_hour": capacity_hour,
            "load_factor": load,
            "catchment_population_persons": population,
            "evidence_id": proof["demand"]["evidence_id"],
        },
        "resource": {
            "vehicle_id": f"demo-{city}-vehicle-B-reserve-1",
            "capacity_persons": BUS_CAPACITY,
            "working_window_local": ["06:00", "14:00"],
            "assigned_trip_ids": [],
            "evidence_id": proof["resource"]["evidence_id"],
        },
        "turnaround": {
            "route_id": f"demo-{city}-route-B",
            "run_s": b_run,
            "layover_s": 300,
            "turnaround_s": 2 * b_run + 600,
            "evidence_id": proof["turnaround"]["evidence_id"],
        },
    }
    pt_checks = {
        "solver": "a2_engine.solve_timetable(max_wait_seconds=3600), departure 08:30 local",
        "base": base_m[target_key],
        "coordinate_transfer": coord_m[target_key] | diff(coord_m),
        "additional_departure": extra_m[target_key] | diff(extra_m),
        "od_reached_base": len(base_m),
        "od_total": len(points) * len(destinations),
    }

    cargo_profile = next(p for p in ext["cargo_profiles"] if p["profile_id"] == CARGO_PROFILE)
    node0 = freight_model[0]
    approach = next(
        r
        for r in freight_edge_rows
        if r["site_id"] == sites[0]["site_id"]
        and r["freight_node_id"] == node0["freight_node_id"]
        and r["direction"] == "outbound"
    )
    wait_after = _up(
        node0["base_wait_s"] * node0["resource"]["units"] / (node0["resource"]["units"] + 1), 60
    )
    freight_check = {
        "site_id": sites[0]["site_id"],
        "freight_node_id": node0["freight_node_id"],
        "direction": "outbound",
        "approach_edge_id": approach["edge_id"],
        "approach_s": approach["time_s"],
        "approach_origin": approach["origin"],
        "before": {
            "wait_s": node0["base_wait_s"],
            "handling_s": node0["handling_s"],
            "total_s": round(approach["time_s"] + node0["base_wait_s"] + node0["handling_s"], 3),
        },
        "after": {
            "wait_s": wait_after,
            "handling_s": node0["handling_s"],
            "total_s": round(approach["time_s"] + wait_after + node0["handling_s"], 3),
        },
        "resource_change": {
            "equipment": "reach_stacker",
            "units_before": node0["resource"]["units"],
            "units_after": node0["resource"]["units"] + 1,
            "window_local": ["08:00", "12:00"],
        },
    }

    tables = {
        "origins": [
            {
                "point_id": p["point_id"],
                "cell_id": c["cell_id"],
                "lat": p["location"]["lat"],
                "lon": p["location"]["lon"],
                "access_node_id": p["access_node_id"],
                "population_persons": c["population"],
                "population_year": c["population_year"],
                "origin": "real",
            }
            for p, c in zip(points, cells, strict=True)
        ],
        "destinations": [
            {
                "destination_id": d["destination_id"],
                "categories": "|".join(d["categories"]),
                "lat": d["location"]["lat"],
                "lon": d["location"]["lon"],
                "access_node_id": d["access_node_id"],
                "object_identity": d["object_identity"],
                "origin": "synthetic" if d["destination_id"] in model_dest_ids else "real",
                "limitation": "MODEL_POINT_NO_REAL_OBJECT"
                if d["destination_id"] in model_dest_ids
                else "",
            }
            for d in destinations
        ],
        "stops": [
            {
                "node_id": s["node_id"],
                "route": s["route"],
                "sequence": s["sequence"],
                "name": s["name"] or "",
                "lat": s["location"]["lat"],
                "lon": s["location"]["lon"],
                "location_origin": "real" if city != "novorossiysk" else "real_walk_node",
                "stop_origin": "synthetic",
                "template_source": s["template_source"],
            }
            for s in stops
        ],
        "stop_times": [
            {
                "trip_id": svc["trip_id"],
                "service_id": svc["service_id"],
                "event_id": f"{svc['trip_id']}:{st['sequence']}",
                "sequence": st["sequence"],
                "node_id": st["node_id"],
                "arrival_local": _clock(st["arrival_seconds"]),
                "departure_local": _clock(st["departure_seconds"]),
                "arrival_seconds": st["arrival_seconds"],
                "departure_seconds": st["departure_seconds"],
                "service_date": SERVICE_DATE.isoformat(),
                "origin": "synthetic",
            }
            for svc in snapshot["scheduled_services"]
            for st in svc["stop_times"]
        ],
        "footpaths": [
            {
                "path_id": f.path_id,
                "from_node_id": f.from_node_id,
                "to_node_id": f.to_node_id,
                "seconds": f.seconds,
                "origin": "synthetic",
            }
            for f in table.footpaths
        ],
        "transfers": [
            {
                "rule_id": r.rule_id,
                "from_node_id": r.from_node_id,
                "to_node_id": r.to_node_id,
                "minimum_seconds": r.minimum_seconds,
                "walk_s": hub_walk,
                "origin": "synthetic",
            }
            for r in table.transfers
        ],
        "walk_edges": walk_rows,
        "car_edges": car_rows,
        "freight_sites": [
            {
                "site_id": s["site_id"],
                "lat": s["location"]["lat"],
                "lon": s["location"]["lon"],
                "access_node_id": s["access_node_id"],
                "origin": "real",
            }
            for s in sites
        ],
        "freight_nodes": [
            {
                "freight_node_id": n["freight_node_id"],
                "kind": n["kind"],
                "lat": n["location"]["lat"],
                "lon": n["location"]["lon"],
                "access_node_id": n["access_node_id"],
                "origin": m["origin"],
                "handling_types": "|".join(m["handling_types"]),
                "max_mass_t": m["max_mass_t"],
                "max_height_m": m["max_height_m"],
                "base_wait_s": m["base_wait_s"],
                "handling_s": m["handling_s"],
                "equipment_units": m["resource"]["units"],
                "slots_per_hour": m["resource"]["slots_per_hour"],
            }
            for n, m in zip(freight_nodes, freight_model, strict=True)
        ],
        "freight_edges": freight_edge_rows,
    }
    return {
        "city": city,
        "tz": tz,
        "real": real,
        "real_snapshot_id": real["snapshot_id"],
        "snapshot": snapshot,
        "timetable": table,
        "receipt": receipt,
        "tables": tables,
        "model": {
            "public_transport": pt_model,
            "freight_nodes": freight_model,
            "cargo_profile": cargo_profile,
            "stops_template": cfg["stops"],
        },
        "checks": {"public_transport": pt_checks, "freight": freight_check},
        "centre": centre,
        "freight_nodes": freight_nodes,
        "sites": sites,
        "copier": copier,
    }


# ----------------------------------------------------------------------------- intercity part

IC_NODES = {
    "moscow": {
        "air": "node-wd-q192733",
        "rail_k": "node-wd-q1367269",
        "rail_n": "node-wd-q21844902",
        "road": "node-wd-q4304027",
    },
    "kaliningrad": {
        "air": "node-wd-q1431650",
        "rail_k": "node-wd-q800979",
        "road": "node-wd-q112735274",
    },
    "novorossiysk": {
        "air": "node-wd-q1997645",
        "rail_n": "node-wd-q3728238",
        "road": "node-osm-way-61808901",
    },
}
# model departures, local time of the departing node (service day 2026-11-11)
IC_SERVICES = (
    ("air", "moscow", "kaliningrad", ("13:20", "19:05")),
    ("air", "kaliningrad", "moscow", ("12:40", "17:10")),
    ("air", "moscow", "novorossiysk", ("14:50", "20:40")),
    ("air", "novorossiysk", "moscow", ("11:45", "17:45")),
    ("rail", "moscow", "kaliningrad", ("16:50",)),
    ("rail", "kaliningrad", "moscow", ("18:00",)),
    ("rail", "moscow", "novorossiysk", ("12:40", "18:30")),
    ("rail", "novorossiysk", "moscow", ("13:40", "21:10")),
    ("road", "moscow", "novorossiysk", ("07:30",)),
    ("road", "novorossiysk", "moscow", ("16:00",)),
)
# base route and the one change per directed pair
IC_PLAN = {
    ("moscow", "kaliningrad"): ("air", "additional_departure"),
    ("kaliningrad", "moscow"): ("air", "additional_departure"),
    ("moscow", "novorossiysk"): ("rail", "additional_departure"),
    ("novorossiysk", "moscow"): ("rail", "additional_departure"),
    ("kaliningrad", "novorossiysk"): ("air_via_moscow", "coordinate_transfer"),
    ("novorossiysk", "kaliningrad"): ("air_via_moscow", "coordinate_transfer"),
}
NOT_APPLICABLE = {
    "water": "NO_SCHEDULED_PASSENGER_WATER_LINK_BETWEEN_THESE_CITIES",
    "rail_kn": "NO_DIRECT_RAIL_LINK_AND_CROSS_TERMINAL_TRANSFER_IN_MOSCOW_NOT_MODELLED",
    "road_bus_k": "INTERNATIONAL_TRANSIT_BUS_NOT_MODELLED",
}


def _pair_key(a: str, b: str) -> str:
    return f"{a}--{b}"


def _intercity_part(
    ext: dict[str, Any], cities: dict[str, dict[str, Any]], directory_root: Path | None
) -> dict[str, Any]:
    snapshot_id = "demo-intercity-2026-11-11.1"
    root = directory_root or (repo_root() / "data" / "directories")
    directory = root / INTERCITY_DIRECTORY
    nodes_raw = (directory / "nodes.json").read_bytes()
    dmanifest = json.loads((directory / "manifest.json").read_text())
    nodes = {n["node_id"]: n for n in json.loads(nodes_raw)}
    road = ext["road"]
    proof = {
        p: _evidence("intercity", p)
        for p in (
            "schedule",
            "calendar",
            "transfers",
            "demand",
            "resource",
            "turnaround",
            "freight",
            "handling",
            "edges",
        )
    }
    directory_evidence = {
        "evidence_id": f"a2-directory:{INTERCITY_DIRECTORY}",
        "source_id": "a2_intercity_nodes",
        "source_version": INTERCITY_DIRECTORY,
        "url": f"https://github.com/matveyka-sudo/case-champion-hac/tree/dev/data/directories/{INTERCITY_DIRECTORY}",
        "retrieved_at": dmanifest["created_at"],
        "valid_period": {"start": None, "end": None},
        "license": "+".join(sorted({s["license"] for s in dmanifest["sources"]})),
        "content_hash": _sha(nodes_raw),
        "verified": True,
    }
    used_nodes = sorted({n for city in IC_NODES.values() for n in city.values()})
    access: list[dict[str, Any]] = []
    for node_id in used_nodes:
        node = nodes[node_id]
        city = next(c for c, m in IC_NODES.items() if node_id in m.values())
        access.append(
            {
                "node_id": node_id,
                "kind": f"intercity_{node['kind']}",
                "location": {"lat": node["lat"], "lon": node["lon"]},
                "territory_id": city,
                "source_refs": [directory_evidence["evidence_id"]],
            }
        )
    road_refs = {e["evidence_id"] for e in road["evidence"]}
    access += [n for n in road["access_nodes"]]
    loc = {n["node_id"]: n["location"] for n in access}
    centre = {c: cities[c]["centre"] for c in cities}
    tz = {c: cities[c]["tz"] for c in cities}

    def run_time(mode: str, a: str, b: str) -> int:
        if mode == "air":
            km = _dist(loc[IC_NODES[a]["air"]], loc[IC_NODES[b]["air"]]) / 1000
            return _up(km / AIR_KMH * 3600 + AIR_TAXI_S, 300)
        obs = next(
            r
            for r in road["rows"]
            if r["origin"] == a and r["destination"] == b and r["http_status"] == 200
        )
        if mode == "rail":
            return _up(
                obs["length_km"] * 1.0 / (62.0 if "kaliningrad" in (a, b) else 70.0) * 3600, 300
            )
        return _up(obs["length_km"] / 68.0 * 3600, 300)

    def node_for(mode: str, city: str, other: str) -> str:
        if mode == "rail":
            return IC_NODES[city]["rail_k" if "kaliningrad" in (city, other) else "rail_n"]
        return IC_NODES[city][mode]

    services, dep_rows = [], []
    by_leg: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for mode, a, b, clocks in IC_SERVICES:
        for clock in clocks:
            dep = _sec(clock)
            arr_utc = _at(tz[a], dep) + timedelta(seconds=run_time(mode, a, b))
            arr = int((arr_utc - _at(tz[a], 0)).total_seconds())  # seconds in the departing zone
            trip = f"demo-ic-{mode}-{_pair_key(a, b)}-{clock.replace(':', '')}"
            row = {
                "service_id": trip.replace("demo-ic-", "demo-ic-svc-"),
                "trip_id": trip,
                "mode": mode,
                "timezone": tz[a],
                "service_dates": [SERVICE_DATE.isoformat()],
                "trip_identity_confirmed": True,
                "stop_times": [
                    {
                        "node_id": node_for(mode, a, b),
                        "sequence": 0,
                        "arrival_seconds": dep,
                        "departure_seconds": dep,
                        "pickup": True,
                        "dropoff": False,
                    },
                    {
                        "node_id": node_for(mode, b, a),
                        "sequence": 1,
                        "arrival_seconds": arr,
                        "departure_seconds": arr,
                        "pickup": False,
                        "dropoff": True,
                    },
                ],
                "source_refs": [proof["schedule"]["evidence_id"]],
            }
            services.append(row)
            by_leg.setdefault((mode, a, b), []).append(row)
            local_arr = arr_utc.astimezone(ZoneInfo(tz[b]))
            dep_rows.append(
                {
                    "trip_id": trip,
                    "service_id": row["service_id"],
                    "mode": mode,
                    "from_city": a,
                    "to_city": b,
                    "from_node_id": node_for(mode, a, b),
                    "to_node_id": node_for(mode, b, a),
                    "departure_local": f"{SERVICE_DATE.isoformat()} {clock}",
                    "departure_tz": tz[a],
                    "arrival_local": local_arr.strftime("%Y-%m-%d %H:%M"),
                    "arrival_tz": tz[b],
                    "run_s": run_time(mode, a, b),
                    "crosses_midnight": local_arr.date() != SERVICE_DATE,
                    "calendar": SERVICE_DATE.isoformat(),
                    "origin": "synthetic",
                }
            )

    def access_s(city: str, node_id: str) -> int:
        return _road_s(_dist(centre[city], loc[node_id]), ROAD_ACCESS_KMH)

    def utc_s(city: str, seconds: int) -> int:
        return int((_at(tz[city], seconds) - _at("UTC", 0)).total_seconds())

    def leg_options(
        mode: str, a: str, b: str, extra: list[dict[str, Any]] | None = None
    ) -> list[tuple[int, int, str]]:
        rows = by_leg.get((mode, a, b), []) + (extra or [])
        return sorted(
            (
                utc_s(a, r["stop_times"][0]["departure_seconds"]),
                utc_s(a, r["stop_times"][1]["arrival_seconds"]),
                r["trip_id"],
            )
            for r in rows
        )

    def journey(
        a: str, b: str, plan: str, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        overrides = overrides or {}
        ready = utc_s(a, READY_S)
        if plan == "air_via_moscow":
            first_node = IC_NODES[a]["air"]
            earliest = ready + access_s(a, first_node) + PROCESS_S["air"]
            leg1 = next(o for o in leg_options("air", a, "moscow") if o[0] >= earliest)
            leg2_opts = leg_options("air", "moscow", b, overrides.get("extra"))
            if "shift" in overrides:
                trip, shift = overrides["shift"]
                leg2_opts = sorted(
                    (d + shift, ar + shift, t) if t == trip else (d, ar, t)
                    for d, ar, t in leg2_opts
                )
            leg2 = next(o for o in leg2_opts if o[0] >= leg1[1] + MCT_AIR_S)
            end = leg2[1] + access_s(b, IC_NODES[b]["air"])
            return {
                "trips": [leg1[2], leg2[2]],
                "initial_wait_s": leg1[0] - earliest,
                "transfer_wait_s": leg2[0] - leg1[1],
                "transfer_slack_s": leg2[0] - leg1[1] - MCT_AIR_S,
                "total_s": end - ready,
            }
        node = node_for(plan, a, b)
        earliest = ready + access_s(a, node) + PROCESS_S[plan]
        options = leg_options(plan, a, b, overrides.get("extra"))
        leg = next(o for o in options if o[0] >= earliest)
        end = leg[1] + access_s(b, node_for(plan, b, a))
        return {"trips": [leg[2]], "initial_wait_s": leg[0] - earliest, "total_s": end - ready}

    pairs_out, comparisons_ic = [], []
    for a, b in PAIRS:
        plan, operation = IC_PLAN[(a, b)]
        base = journey(a, b, plan)
        if operation == "additional_departure":
            mode = plan
            template = next(r for r in by_leg[(mode, a, b)] if r["trip_id"] == base["trips"][0])
            node = node_for(mode, a, b)
            earliest_local = READY_S + access_s(a, node) + PROCESS_S[mode]
            new_dep = _up(earliest_local + 900, 300)
            shift = new_dep - template["stop_times"][0]["departure_seconds"]
            new_trip = f"demo-ic-{mode}-{_pair_key(a, b)}-{_hhmm(new_dep)}-extra"
            extra = {
                "trip_id": new_trip,
                "stop_times": [
                    dict(
                        st,
                        arrival_seconds=st["arrival_seconds"] + shift,
                        departure_seconds=st["departure_seconds"] + shift,
                    )
                    for st in template["stop_times"]
                ],
            }
            after = journey(a, b, plan, {"extra": [extra]})
            rng = _rng("intercity", _pair_key(a, b), "demand")
            capacity = 180 if mode == "air" else 450
            inputs = {
                "service_ids": [template["service_id"]],
                "template_trip_id": template["trip_id"],
                "shift_seconds": shift,
                "new_trip_id": new_trip,
                "demand": {
                    "window_local": [
                        _clock(READY_S),
                        _clock(template["stop_times"][0]["departure_seconds"]),
                    ],
                    "demand_persons": round(capacity * rng.uniform(0.75, 0.95)),
                    "evidence_id": proof["demand"]["evidence_id"],
                },
                "resource": {
                    "vehicle_id": f"demo-ic-{'aircraft-a320' if mode == 'air' else 'trainset'}-reserve-{_pair_key(a, b)}",
                    "capacity_persons": capacity,
                    "working_window_local": ["06:00", "23:00"],
                    "evidence_id": proof["resource"]["evidence_id"],
                },
                "turnaround": {
                    "turnaround_s": 3000 if mode == "air" else 14400,
                    "evidence_id": proof["turnaround"]["evidence_id"],
                },
            }
            created = [new_trip]
        else:
            leg1_arrival = next(
                o for o in leg_options("air", a, "moscow") if o[2] == base["trips"][0]
            )[1]
            options = leg_options("air", "moscow", b)
            earlier = [o for o in options if o[0] < leg1_arrival + MCT_AIR_S]
            moved = earlier[-1] if earlier else None
            assert moved is not None, "a missed connection is required for coordinate_transfer"
            shift = _up(leg1_arrival + MCT_AIR_S + 900 - moved[0], 300)
            after = journey(a, b, plan, {"shift": (moved[2], shift)})
            moved_row = next(r for r in services if r["trip_id"] == moved[2])
            inputs = {
                "service_ids": [moved_row["service_id"]],
                "shift_seconds": shift,
                "transfer_node_id": IC_NODES["moscow"]["air"],
                "minimum_connection_s": MCT_AIR_S,
                "demand": {
                    "connecting_persons": round(
                        180 * _rng("intercity", _pair_key(a, b), "demand").uniform(0.08, 0.18)
                    ),
                    "evidence_id": proof["demand"]["evidence_id"],
                },
                "resource": {
                    "vehicle_id": f"demo-ic-aircraft-rotation-{moved[2]}",
                    "evidence_id": proof["resource"]["evidence_id"],
                },
                "turnaround": {
                    "turnaround_s": 3000,
                    "evidence_id": proof["turnaround"]["evidence_id"],
                },
                "neighbour_check": {
                    "shared_trip_id": moved[2],
                    "affected_pair": _pair_key("moscow", b),
                    "note": "the shifted flight also serves moscow -> "
                    + b
                    + "; its departure moves by shift_seconds",
                },
            }
            created = []
        pairs_out.append({"pair": _pair_key(a, b), "plan": plan, "base": base})
        comparisons_ic.append(
            {
                "pair": _pair_key(a, b),
                "operation_type": operation,
                "inputs": inputs,
                "before": base,
                "after": after,
                "created_ids": created,
            }
        )

    # road observations (real) and K<->N unknown with its recorded reason
    edges, road_rows = [], []
    for row in road["rows"]:
        a, b = row["origin"], row["destination"]
        item = {
            "pair": _pair_key(a, b),
            "http_status": row["http_status"],
            "response_sha256": row["response_sha256"],
        }
        if row["http_status"] == 200:
            evidence_id = f"road-observation:{row['response_sha256']}"
            assert evidence_id in road_refs
            edge_id = f"demo-ic-road-observation:{_pair_key(a, b)}"
            edges.append(
                {
                    "edge_id": edge_id,
                    "from_node_id": f"{row['name']}:origin-network",
                    "to_node_id": f"{row['name']}:destination-network",
                    "mode": "road",
                    "length_m": round(row["length_km"] * 1000, 1),
                    "running_time_seconds": round(float(row["time_s"]), 3),
                    "calibration_method": "valhalla-auto-freeflow-observation",
                    "geometry": None,
                    "source_refs": [evidence_id],
                }
            )
            item |= {
                "edge_id": edge_id,
                "length_km": row["length_km"],
                "time_s": row["time_s"],
                "status": "real_observation",
            }
        else:
            item |= {
                "status": "unknown",
                "reason": f"ROAD_OBSERVATION_UNAVAILABLE:{row['error_code']}:{row['error']}",
            }
        road_rows.append(item)

    # intercity freight chains
    fn = {c: {n["kind"]: n for n in cities[c]["freight_nodes"]} for c in cities}
    site = {c: cities[c]["sites"][0] for c in cities}
    chains = []
    chain_plan = {
        ("moscow", "kaliningrad"): ("rail", "reduce_transshipment_time"),
        ("kaliningrad", "moscow"): ("rail", "add_compatible_departure"),
        ("moscow", "novorossiysk"): ("rail", "add_compatible_departure"),
        ("novorossiysk", "moscow"): ("rail", "reduce_transshipment_time"),
        ("kaliningrad", "novorossiysk"): ("water", "reduce_transshipment_time"),
        ("novorossiysk", "kaliningrad"): ("water", "add_compatible_departure"),
    }
    departures = {  # model freight departures (local clock, day offset from 2026-11-11)
        ("rail", "moscow", "kaliningrad"): [(0, "14:00"), (1, "14:00")],
        ("rail", "kaliningrad", "moscow"): [(0, "22:00"), (1, "22:00")],
        ("rail", "moscow", "novorossiysk"): [(0, "11:00"), (0, "23:00"), (1, "11:00")],
        ("rail", "novorossiysk", "moscow"): [(0, "06:00"), (0, "18:00"), (1, "06:00")],
        ("water", "kaliningrad", "novorossiysk"): [(2, "10:00"), (9, "10:00")],
        ("water", "novorossiysk", "kaliningrad"): [(3, "16:00"), (10, "16:00")],
    }
    distance_km = {
        ("rail", "moscow", "kaliningrad"): 1300.0,
        ("rail", "moscow", "novorossiysk"): 1440.0,
        ("water", "kaliningrad", "novorossiysk"): 6900.0,
    }
    speed_kmh = {"rail": 40.0, "water": 26.0}
    freight_edges = []
    for a, b in PAIRS:
        mode, operation = chain_plan[(a, b)]
        key = (mode,) + tuple(
            sorted((a, b), key=lambda c: ("moscow", "kaliningrad", "novorossiysk").index(c))
        )
        dist = distance_km[key]  # type: ignore[index]
        start_node = fn[a]["port" if mode == "water" else "rail_terminal"]
        end_node = fn[b]["port" if mode == "water" else "rail_terminal"]
        first_mile = _road_s(_dist(site[a]["location"], start_node["location"]), TRUCK_KMH)
        last_mile = _road_s(_dist(end_node["location"], site[b]["location"]), TRUCK_KMH)
        rng = _rng("intercity-freight", _pair_key(a, b))
        handling_origin = _up(rng.uniform(7200, 10800), 600)
        transship = _up(rng.uniform(18000, 25200), 600)
        run = _up(dist / speed_kmh[mode] * 3600, 600)
        deps = [utc_s(a, d * 86400 + _sec(c)) for d, c in departures[(mode, a, b)]]

        def total(extra_dep: int | None = None, transship_s: int = transship) -> dict[str, Any]:
            ready = utc_s(a, READY_S) + first_mile + handling_origin
            options = sorted(deps + ([extra_dep] if extra_dep is not None else []))
            dep = next(d for d in options if d >= ready)
            end = dep + run + transship_s + last_mile
            return {
                "departure_utc": datetime.fromtimestamp(dep, UTC).isoformat(),
                "wait_s": dep - ready,
                "transshipment_s": transship_s,
                "total_s": end - utc_s(a, READY_S),
            }

        change: dict[str, Any]
        base = total()
        if operation == "add_compatible_departure":
            ready = utc_s(a, READY_S) + first_mile + handling_origin
            extra_dep = _up(ready + 1800, 1800)
            after = total(extra_dep=extra_dep)
            change = {
                "new_departure_id": f"demo-icf-{mode}-{_pair_key(a, b)}-extra",
                "departure_utc": datetime.fromtimestamp(extra_dep, UTC).isoformat(),
                "demand": {
                    "teu": rng.randint(36, 60),
                    "evidence_id": proof["demand"]["evidence_id"],
                },
                "resource": {
                    "unit": "locomotive+wagons" if mode == "rail" else "feeder_vessel",
                    "capacity_teu": 70 if mode == "rail" else 600,
                    "evidence_id": proof["resource"]["evidence_id"],
                },
            }
            created = [change["new_departure_id"]]
        else:
            after_s = _up(transship * 0.6, 600)
            after = total(transship_s=after_s)
            change = {
                "node_id": end_node["freight_node_id"],
                "transshipment_before_s": transship,
                "transshipment_after_s": after_s,
                "resource": {
                    "unit": "crane_shift",
                    "added": 1,
                    "window_local": ["08:00", "20:00"],
                    "evidence_id": proof["resource"]["evidence_id"],
                },
            }
            created = []
        legs = [
            {
                "leg_id": f"demo-icf-{_pair_key(a, b)}-1",
                "mode": "road",
                "from": site[a]["site_id"],
                "to": start_node["freight_node_id"],
                "run_s": first_mile,
                "handling_at_end_s": handling_origin,
                "origin": "synthetic",
            },
            {
                "leg_id": f"demo-icf-{_pair_key(a, b)}-2",
                "mode": mode,
                "from": start_node["freight_node_id"],
                "to": end_node["freight_node_id"],
                "distance_km": dist,
                "run_s": run,
                "departures_local": [f"+{d}d {c}" for d, c in departures[(mode, a, b)]],
                "handling_at_end_s": transship,
                "origin": "synthetic",
            },
            {
                "leg_id": f"demo-icf-{_pair_key(a, b)}-3",
                "mode": "road",
                "from": end_node["freight_node_id"],
                "to": site[b]["site_id"],
                "run_s": last_mile,
                "origin": "synthetic",
            },
        ]
        for leg in legs[1:2]:
            freight_edges.append(
                {
                    "edge_id": leg["leg_id"],
                    "from_node_id": start_node["access_node_id"],
                    "to_node_id": end_node["access_node_id"],
                    "mode": mode,
                    "length_m": dist * 1000,
                    "running_time_seconds": float(run),
                    "calibration_method": f"{RULES_VERSION}:distance-speed",
                    "geometry": None,
                    "source_refs": [proof["freight"]["evidence_id"]],
                }
            )
        chains.append(
            {
                "pair": _pair_key(a, b),
                "chain_id": f"demo-icf-{_pair_key(a, b)}",
                "cargo_profile_id": CARGO_PROFILE,
                "unit_load": "container_20ft",
                "legs": legs,
                "operation_type": operation,
                "change": change,
                "before": base,
                "after": after,
                "created_ids": created,
                "limitations": ["FOREIGN_TRANSIT_TERRITORY"]
                if "kaliningrad" in (a, b) and mode == "rail"
                else [],
            }
        )

    # assemble the intercity snapshot (territories, nodes and freight rows copied from the city parts)
    territories, extra_nodes, f_nodes, f_sites, copied_refs = [], [], [], [], set()
    evidence_rows: dict[str, dict[str, Any]] = {e["evidence_id"]: e for e in road["evidence"]}
    versions = dict(road["source_versions"])
    for c in cities:
        snap = cities[c]["snapshot"]
        territories.append(snap["territories"][0])
        nodes_by_id = {n["node_id"]: n for n in snap["access_nodes"]}
        wanted = [cities[c]["sites"][0], *cities[c]["freight_nodes"]]
        for row in wanted:
            extra_nodes.append(nodes_by_id[row["access_node_id"]])
        f_nodes += cities[c]["freight_nodes"]
        f_sites.append(cities[c]["sites"][0])
        rows = [
            snap["territories"][0],
            snap["territories"][0]["geometry"],
            *wanted,
            *(nodes_by_id[r["access_node_id"]] for r in wanted),
        ]
        refs = {r for row in rows for r in row["source_refs"]}
        for evidence in snap["evidence"]:
            if evidence["evidence_id"] in refs and evidence["source_id"] != DEMO_SOURCE:
                # one source id per city, as in threecity-road-observations (osm_geofabrik:<city>)
                source = (
                    evidence["source_id"]
                    if ":" in evidence["source_id"]
                    else f"{evidence['source_id']}:{c}"
                )
                row = evidence_rows.setdefault(
                    evidence["evidence_id"], evidence | {"source_id": source}
                )
                if (
                    versions.setdefault(row["source_id"], row["source_version"])
                    != row["source_version"]
                ):
                    raise ValueError(f"conflicting versions for {row['source_id']}")
        copied_refs |= refs
    # city DEMO evidence referenced by copied model freight nodes is re-pointed to the intercity proof
    for row in [*f_nodes, *extra_nodes]:
        row["source_refs"] = sorted(
            {
                r if not r.startswith("demo-model:") else proof["freight"]["evidence_id"]
                for r in row["source_refs"]
            }
        )
    evidence_rows[directory_evidence["evidence_id"]] = directory_evidence
    versions[directory_evidence["source_id"]] = directory_evidence["source_version"]
    versions[DEMO_SOURCE] = DATASET_ID
    all_nodes = {n["node_id"]: n for n in [*access, *extra_nodes]}
    minimums = [
        {
            "minimum_id": "demo-ic-minimum-svo-air",
            "from_node_id": IC_NODES["moscow"]["air"],
            "to_node_id": IC_NODES["moscow"]["air"],
            "seconds": float(MCT_AIR_S),
            "applicability": ["air", "alight_to_departure"],
            "source_refs": [proof["transfers"]["evidence_id"]],
        }
    ]
    transfers = [
        {
            "transfer_id": "demo-ic-transfer-svo-air",
            "from_node_id": IC_NODES["moscow"]["air"],
            "to_node_id": IC_NODES["moscow"]["air"],
            "seconds": 0.0,
            "verification_state": "verified",
            "source_refs": [proof["transfers"]["evidence_id"]],
        }
    ]
    snapshot = _accepted(
        {
            "schema_version": "2",
            "snapshot_id": snapshot_id,
            "source_versions": dict(sorted(versions.items())),
            "geographic_scope": list(cities),
            "valid_period": {
                "start": SERVICE_DATE.isoformat(),
                "end": (SERVICE_DATE + timedelta(days=14)).isoformat(),
            },
            "timezone": "Europe/Moscow",
            "provenance": {
                "graph_version": f"{snapshot_id}:graph",
                "geometry_version": f"{snapshot_id}:geometry",
                "calendar_version": f"{snapshot_id}:calendar",
                "population_version": None,
            },
            "qa": {
                "acceptance_state": "accepted",
                "accepted_by": f"ML2 {RULES_VERSION} synthetic demo generator; model data, not a real acceptance",
                "accepted_at": GENERATED_AT,
                "checks": [DEMO_SOURCE, "NOT_A_REAL_CITY_RANKING", f"{RULES_VERSION}:qa"],
            },
            "evidence": sorted(evidence_rows.values(), key=lambda e: e["evidence_id"])
            + list(proof.values()),
            "registry_evidence": [],
            "territories": territories,
            "population_cells": [],
            "sample_points": [],
            "destinations": [],
            "access_nodes": sorted(all_nodes.values(), key=lambda n: n["node_id"]),
            "scheduled_services": sorted(services, key=lambda s: s["trip_id"]),
            "transfers": transfers,
            "freight_sites": f_sites,
            "freight_nodes": f_nodes,
            "freight_compatibility": [],
            "pipeline_connections": [],
            "reference_edges": edges + freight_edges,
            "minimum_transfers": minimums,
            "traffic_profiles": [],
            "category_ownership": [],
            "coverage_by_mode_date_category": [],
            "confirmed_absence_scope": [],
        }
    )
    applicability = []
    for a, b in PAIRS:
        pair = _pair_key(a, b)
        k = "kaliningrad" in (a, b)
        kn = {a, b} == {"kaliningrad", "novorossiysk"}
        road_row = next(r for r in road_rows if r["pair"] == pair)
        passenger = {
            "air": "model_schedule" if not kn else "model_schedule_via_moscow",
            "rail": "not_applicable:" + NOT_APPLICABLE["rail_kn"] if kn else "model_schedule",
            "road_car": "real_observation"
            if road_row["status"] == "real_observation"
            else road_row["reason"],
            "road_bus": "not_applicable:" + NOT_APPLICABLE["road_bus_k"] if k else "model_schedule",
            "water": "not_applicable:" + NOT_APPLICABLE["water"],
        }
        mode = chain_plan[(a, b)][0]
        freight = {
            "road": "not_applicable:LONG_HAUL_ROAD_NOT_IN_DEMO_CHAIN"
            if not kn
            else "unknown:ROAD_OBSERVATION_UNAVAILABLE",
            "rail": "model_chain" if mode == "rail" else "not_applicable:NO_DIRECT_RAIL_LINK",
            "water": "model_chain" if mode == "water" else "not_applicable:NO_WATER_LINK_MODELLED",
            "air": "not_applicable:AIR_CARGO_NOT_MODELLED_FOR_CONTAINER_PROFILE",
            "pipeline": "separate_profile:demo-novorossiysk-pipeline"
            if "novorossiysk" in (a, b)
            else "not_applicable:NO_PIPELINE_LINK",
        }
        applicability.append({"pair": pair, "passenger": passenger, "freight": freight})
    return {
        "snapshot": snapshot,
        "departures": dep_rows,
        "pairs": pairs_out,
        "passenger_comparisons": comparisons_ic,
        "road": road_rows,
        "chains": chains,
        "applicability": applicability,
    }


# ----------------------------------------------------------------------------- pipeline part


def _pipeline_part(city_part: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = "demo-pipeline-2026-11-11.1"
    real = city_part["real"]
    proof = _evidence("pipeline", "network")
    terminals = sorted(
        (n for n in real["freight_nodes"] if n["kind"] == "pipeline_terminal"),
        key=lambda n: n["freight_node_id"],
    )
    by_id = {n["freight_node_id"]: n for n in terminals}
    chain = [
        "novorossiysk-pipeline_terminal-relation-3569335",
        "novorossiysk-pipeline_terminal-way-183488914",
        "novorossiysk-pipeline_terminal-way-126416704",
    ]
    nodes = [by_id[n] for n in chain]
    copier = _Copier(real)
    territory = copier.take(real["territories"][0])
    for node in nodes:
        copier.take(node)
    access_nodes = copier.access_nodes()
    evidence = [*copier.evidence_rows(), proof]
    period = {"start": SERVICE_DATE.isoformat(), "end": SERVICE_DATE.isoformat()}
    rng = _rng("pipeline")
    connections, metrics = [], []
    capacity = float(_up(rng.uniform(250000, 350000), 1000))
    for index, (a, b) in enumerate(zip(chain, chain[1:], strict=False), start=1):
        volume = float(_up(capacity * rng.uniform(0.7, 0.9), 1000))
        connections.append(
            {
                "connection_id": f"demo-pipeline-novorossiysk-{index}",
                "product": "oil",
                "from_node_id": a,
                "to_node_id": b,
                "connection_confirmed": True,
                "direction_confirmed": True,
                "volume": {"value": volume, "unit": "t", "period": period, "product": "oil"},
                "capacity": {"value": capacity, "unit": "t", "period": period, "product": "oil"},
                "quantity_status": "available",
                "source_refs": [proof["evidence_id"]],
            }
        )
        metrics.append(
            {
                "connection_id": connections[-1]["connection_id"],
                "from": a,
                "to": b,
                "direction": "intake_to_delivery",
                "product": "oil",
                "capacity_t_per_day": capacity,
                "volume_t_per_day": volume,
                "utilisation": round(volume / capacity, 3),
                "origin": "synthetic",
            }
        )
    snapshot = _accepted(
        {
            "schema_version": "2",
            "snapshot_id": snapshot_id,
            "source_versions": dict(
                sorted((copier.source_versions() | {DEMO_SOURCE: DATASET_ID}).items())
            ),
            "geographic_scope": ["novorossiysk"],
            "valid_period": period,
            "timezone": "Europe/Moscow",
            "provenance": {
                "graph_version": f"{snapshot_id}:graph",
                "geometry_version": real["provenance"]["geometry_version"],
                "calendar_version": f"{snapshot_id}:calendar",
                "population_version": None,
            },
            "qa": {
                "acceptance_state": "accepted",
                "accepted_by": f"ML2 {RULES_VERSION} synthetic demo generator; model data, not a real acceptance",
                "accepted_at": GENERATED_AT,
                "checks": [
                    DEMO_SOURCE,
                    "NOT_A_REAL_CITY_RANKING",
                    "PIPELINE_NOT_IN_DELIVERY_INDEX",
                ],
            },
            "evidence": evidence,
            "registry_evidence": [],
            "territories": [territory],
            "population_cells": [],
            "sample_points": [],
            "destinations": [],
            "access_nodes": access_nodes,
            "scheduled_services": [],
            "transfers": [],
            "freight_sites": [],
            "freight_nodes": nodes,
            "freight_compatibility": [],
            "pipeline_connections": connections,
            "reference_edges": [],
            "minimum_transfers": [],
            "traffic_profiles": [],
            "category_ownership": [],
            "coverage_by_mode_date_category": [],
            "confirmed_absence_scope": [],
        }
    )
    return {
        "snapshot": snapshot,
        "metrics": metrics,
        "nodes": [
            {
                "freight_node_id": n["freight_node_id"],
                "role": role,
                "origin": "real_location_model_role",
            }
            for n, role in zip(nodes, ("intake", "tank_farm", "delivery"), strict=True)
        ],
    }


# ----------------------------------------------------------------------------- matrix


def _utc(city: str) -> str:
    return _at(str(CITIES[city]["tz"]), READY_S).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _matrix(
    cities: dict[str, dict[str, Any]], ic: dict[str, Any], pipe: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    presets: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    slice_limits = ["DATE_SLICE_ONLY:2026-11-11T08:30_LOCAL", *LIMITATIONS]
    labels = {
        "walk": "пешком",
        "private_car": "на автомобиле",
        "public_transport": "общественный транспорт",
    }
    names = {"moscow": "Москва", "kaliningrad": "Калининград", "novorossiysk": "Новороссийск"}
    for city, part in cities.items():
        snap = part["snapshot"]
        tz = part["tz"]
        pt = part["model"]["public_transport"]
        checks = part["checks"]["public_transport"]
        model_dest = [
            d["destination_id"]
            for d in part["tables"]["destinations"]
            if d["origin"] == "synthetic"
        ]
        for mode, short in (("walk", "walk"), ("private_car", "car"), ("public_transport", "pt")):
            preset_id = f"demo-{city}-urban-{short}"
            ops = (
                ["coordinate_transfer", "additional_departure"]
                if mode == "public_transport"
                else []
            )
            presets.append(
                {
                    "preset_id": preset_id,
                    "tab": "in_city",
                    "analysis_kind": "urban_passenger",
                    "city_id": city,
                    "passenger_mode": mode,
                    "categories": list(CATEGORIES),
                    "service_date": SERVICE_DATE.isoformat(),
                    "local_time": "08:30",
                    "timezone": tz,
                    "departure_utc": _utc(city),
                    "snapshot_id": snap["snapshot_id"],
                    "transport_receipt": f"receipts/{snap['snapshot_id']}-transport.json"
                    if mode == "public_transport"
                    else None,
                    "inputs": {
                        "origin_point_ids": [p["point_id"] for p in snap["sample_points"]],
                        "destination_ids": [d["destination_id"] for d in snap["destinations"]],
                        "edge_count": sum(1 for e in snap["reference_edges"] if e["mode"] == mode)
                        if mode != "public_transport"
                        else None,
                        "trip_count": len(snap["scheduled_services"])
                        if mode == "public_transport"
                        else None,
                    },
                    "schedule_operations": ops,
                    "comparison_ids": [f"cmp-{preset_id}-{op}" for op in ops],
                    "origin": {
                        "origins": "real",
                        "destinations": "real"
                        if not model_dest
                        else f"real+model:{len(model_dest)}",
                        "times": "synthetic",
                        "schedule": "synthetic" if mode == "public_transport" else None,
                    },
                    "model_destination_ids": model_dest,
                    "limitations": slice_limits
                    + (["NO_SCHEDULE_OPERATIONS_FOR_MODE"] if not ops else []),
                    "label_ru": f"{names[city]} · {labels[mode]} · 9 категорий · 11.11.2026 08:30",
                }
            )
            for op in ops:
                key = (
                    "coordinate_transfer" if op == "coordinate_transfer" else "additional_departure"
                )
                after = checks[key]
                svc = pt["problem_transfer"]["planned_trip_id"].replace("-trip-", "-svc-")
                ids = [
                    pt["target"]["origin_point_id"],
                    pt["target"]["destination_id"],
                    pt["problem_transfer"]["transfer_id"],
                    svc,
                ]
                created = (
                    [pt["extra_departure_slot"]["new_trip_id"]]
                    if op == "additional_departure"
                    else []
                )
                comparisons.append(
                    {
                        "comparison_id": f"cmp-{preset_id}-{op}",
                        "preset_id": preset_id,
                        "operation_type": op,
                        "contract_status": "scenario_contract_v2",
                        "fixed_parameters": {
                            "service_ids": [svc],
                            "shift_seconds": COORDINATE_SHIFT_S
                            if op == "coordinate_transfer"
                            else EXTRA_SHIFT_S,
                            "demand_evidence": [pt["demand"]["evidence_id"]],
                            "resource_evidence": [pt["resource"]["evidence_id"]],
                            "turnaround_evidence": [pt["turnaround"]["evidence_id"]],
                            "resource_constraints": [pt["resource"]["vehicle_id"]]
                            if op == "additional_departure"
                            else [],
                        },
                        "model_inputs": {
                            "problem_transfer": pt["problem_transfer"],
                            "demand": pt["demand"],
                            "resource": pt["resource"],
                            "turnaround": pt["turnaround"],
                            "extra_departure_slot": pt["extra_departure_slot"] if created else None,
                        },
                        "ids": {"before": ids, "after": ids + created},
                        "created_ids": created,
                        "expected_by": checks["solver"],
                        "before": checks["base"],
                        "after": {k: after[k] for k in checks["base"]},
                        "delta": {
                            "waiting_s": checks["base"]["initial_wait_s"]
                            + checks["base"]["transfer_wait_s"]
                            - after["initial_wait_s"]
                            - after["transfer_wait_s"],
                            "total_s": checks["base"]["total_s"] - after["total_s"],
                            "sign": "before_minus_after",
                        },
                        "neighbour_worse": after["worse"],
                        "other_better": after["better"],
                    }
                )
    for row in ic["pairs"]:
        a, b = row["pair"].split("--")
        cmp = next(c for c in ic["passenger_comparisons"] if c["pair"] == row["pair"])
        preset_id = f"demo-ic-{row['pair']}"
        app = next(x for x in ic["applicability"] if x["pair"] == row["pair"])
        presets.append(
            {
                "preset_id": preset_id,
                "tab": "passenger_links",
                "analysis_kind": "intercity_passenger",
                "origin_city_id": a,
                "destination_city_id": b,
                "passenger_mode": "air" if row["plan"].startswith("air") else row["plan"],
                "route_plan": row["plan"],
                "service_date": SERVICE_DATE.isoformat(),
                "local_time": "08:30",
                "timezone": str(CITIES[a]["tz"]),
                "departure_utc": _utc(a),
                "snapshot_id": ic["snapshot"]["snapshot_id"],
                "inputs": {"trip_ids": row["base"]["trips"]},
                "schedule_operations": [cmp["operation_type"]],
                "comparison_ids": [f"cmp-{preset_id}-{cmp['operation_type']}"],
                "applicability": app["passenger"],
                "origin": {
                    "nodes": "real",
                    "schedule": "synthetic",
                    "road_observation": app["passenger"]["road_car"],
                },
                "limitations": slice_limits + ["MODEL_SCHEDULE_NOT_A_REAL_TIMETABLE"],
                "label_ru": f"{names[a]} → {names[b]} · {'самолёт' if row['plan'].startswith('air') else 'поезд'}"
                + (" через Москву" if row["plan"] == "air_via_moscow" else ""),
            }
        )
        inputs = cmp["inputs"]
        scope = [row["pair"], *inputs["service_ids"]]
        comparisons.append(
            {
                "comparison_id": f"cmp-{preset_id}-{cmp['operation_type']}",
                "preset_id": preset_id,
                "operation_type": cmp["operation_type"],
                "contract_status": "scenario_contract_v2_operation; intercity context not accepted by the current API",
                "fixed_parameters": {
                    "service_ids": inputs["service_ids"],
                    "shift_seconds": inputs["shift_seconds"],
                    "demand_evidence": [inputs["demand"]["evidence_id"]],
                    "resource_evidence": [inputs["resource"]["evidence_id"]],
                    "turnaround_evidence": [inputs["turnaround"]["evidence_id"]],
                    "resource_constraints": [inputs["resource"]["vehicle_id"]],
                },
                "model_inputs": inputs,
                "ids": {"before": scope, "after": scope + cmp["created_ids"]},
                "created_ids": cmp["created_ids"],
                "before": cmp["before"],
                "after": cmp["after"],
                "delta": {
                    "initial_wait_s": cmp["before"]["initial_wait_s"]
                    - cmp["after"]["initial_wait_s"],
                    "transfer_wait_s": cmp["before"].get("transfer_wait_s", 0)
                    - cmp["after"].get("transfer_wait_s", 0),
                    "total_s": cmp["before"]["total_s"] - cmp["after"]["total_s"],
                    "sign": "before_minus_after",
                },
            }
        )
    for city, part in cities.items():
        check = part["checks"]["freight"]
        preset_id = f"demo-{city}-cargo-urban"
        presets.append(
            {
                "preset_id": preset_id,
                "tab": "cargo_in_city",
                "analysis_kind": "urban_cargo",
                "city_id": city,
                "cargo_profile_id": CARGO_PROFILE,
                "direction": "outbound",
                "site_id": check["site_id"],
                "freight_node_id": check["freight_node_id"],
                "service_date": SERVICE_DATE.isoformat(),
                "local_time": "08:30",
                "timezone": part["tz"],
                "departure_utc": _utc(city),
                "snapshot_id": part["snapshot"]["snapshot_id"],
                "inputs": {"approach_edge_id": check["approach_edge_id"]},
                "schedule_operations": ["reduce_handling_wait"],
                "comparison_ids": [f"cmp-{preset_id}-reduce_handling_wait"],
                "origin": {
                    "site": "real",
                    "node": next(
                        r["origin"]
                        for r in part["tables"]["freight_nodes"]
                        if r["freight_node_id"] == check["freight_node_id"]
                    ),
                    "approach": check["approach_origin"],
                    "handling": "synthetic",
                },
                "limitations": slice_limits,
                "label_ru": f"{names[city]} · площадка → грузовой узел · контейнер 20 т",
            }
        )
        scope = [check["site_id"], check["freight_node_id"], "outbound", check["approach_edge_id"]]
        comparisons.append(
            {
                "comparison_id": f"cmp-{preset_id}-reduce_handling_wait",
                "preset_id": preset_id,
                "operation_type": "reduce_handling_wait",
                "contract_status": "requires_contract_change: cargo operations are not in scenarios v2",
                "model_inputs": {
                    "resource_change": check["resource_change"],
                    "evidence_id": f"demo-model:{city}:handling",
                },
                "ids": {"before": scope, "after": scope},
                "created_ids": [],
                "before": check["before"],
                "after": check["after"],
                "delta": {
                    "wait_s": check["before"]["wait_s"] - check["after"]["wait_s"],
                    "total_s": round(check["before"]["total_s"] - check["after"]["total_s"], 3),
                    "sign": "before_minus_after",
                },
            }
        )
    for chain in ic["chains"]:
        a, b = chain["pair"].split("--")
        preset_id = f"demo-icc-{chain['pair']}"
        presets.append(
            {
                "preset_id": preset_id,
                "tab": "cargo_between",
                "analysis_kind": "intercity_cargo",
                "origin_city_id": a,
                "destination_city_id": b,
                "cargo_profile_id": CARGO_PROFILE,
                "chain_id": chain["chain_id"],
                "main_mode": chain["legs"][1]["mode"],
                "service_date": SERVICE_DATE.isoformat(),
                "local_time": "08:30",
                "timezone": str(CITIES[a]["tz"]),
                "departure_utc": _utc(a),
                "snapshot_id": ic["snapshot"]["snapshot_id"],
                "inputs": {"leg_ids": [leg["leg_id"] for leg in chain["legs"]]},
                "schedule_operations": [chain["operation_type"]],
                "comparison_ids": [f"cmp-{preset_id}-{chain['operation_type']}"],
                "applicability": next(
                    x["freight"] for x in ic["applicability"] if x["pair"] == chain["pair"]
                ),
                "origin": {"sites_nodes": "real+model", "chain": "synthetic"},
                "limitations": slice_limits
                + chain["limitations"]
                + ["PIPELINE_EXCLUDED_FROM_DELIVERY_INDEX"],
                "label_ru": f"{names[a]} → {names[b]} · груз · {'ж/д' if chain['legs'][1]['mode'] == 'rail' else 'море'}",
            }
        )
        scope = [chain["chain_id"], *(leg["leg_id"] for leg in chain["legs"])]
        comparisons.append(
            {
                "comparison_id": f"cmp-{preset_id}-{chain['operation_type']}",
                "preset_id": preset_id,
                "operation_type": chain["operation_type"],
                "contract_status": "requires_contract_change: cargo operations are not in scenarios v2",
                "model_inputs": chain["change"],
                "ids": {"before": scope, "after": scope + chain["created_ids"]},
                "created_ids": chain["created_ids"],
                "before": chain["before"],
                "after": chain["after"],
                "delta": {
                    "wait_s": chain["before"]["wait_s"] - chain["after"]["wait_s"],
                    "total_s": chain["before"]["total_s"] - chain["after"]["total_s"],
                    "sign": "before_minus_after",
                },
            }
        )
    presets.append(
        {
            "preset_id": "demo-novorossiysk-pipeline",
            "tab": "cargo_between",
            "analysis_kind": "pipeline_profile",
            "city_id": "novorossiysk",
            "in_acceptance_matrix": False,
            "snapshot_id": pipe["snapshot"]["snapshot_id"],
            "service_date": SERVICE_DATE.isoformat(),
            "inputs": {"connection_ids": [m["connection_id"] for m in pipe["metrics"]]},
            "schedule_operations": [],
            "comparison_ids": [],
            "origin": {"nodes": "real", "network": "synthetic"},
            "limitations": [*LIMITATIONS, "PIPELINE_NOT_IN_DELIVERY_INDEX"],
            "label_ru": "Новороссийск · трубопровод (отдельный профиль, модель)",
        }
    )
    return presets, comparisons


# ----------------------------------------------------------------------------- QA and coverage


def _qa(
    cities: dict[str, dict[str, Any]],
    ic: dict[str, Any],
    pipe: dict[str, Any],
    presets: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def check(check_id: str, ok: bool, detail: object) -> None:
        out.append({"check_id": check_id, "passed": bool(ok), "detail": detail})

    snapshots = [p["snapshot"] for p in cities.values()] + [ic["snapshot"], pipe["snapshot"]]
    check(
        "accepted_data_v2_valid",
        all(_accepted(s) == s for s in snapshots),
        [s["snapshot_id"] for s in snapshots],
    )
    check(
        "demo_label_on_every_snapshot",
        all(
            DEMO_SOURCE in s["qa"]["checks"] and DEMO_SOURCE in s["source_versions"]
            for s in snapshots
        ),
        "qa.checks and source_versions",
    )
    engine = importlib.import_module("a2_engine")
    check(
        "timetables_valid_engine_model",
        all(isinstance(p["timetable"], engine.Timetable) for p in cities.values()),
        "a2_engine.Timetable",
    )
    proof_ok = True
    for part in cities.values():
        snap, table = part["snapshot"], part["timetable"]
        proof = {
            e["evidence_id"]
            for e in snap["evidence"]
            if e["source_id"] == DEMO_SOURCE and e["verified"]
        }
        rows = [
            table.scope,
            *table.footpaths,
            *table.transfers,
            *(e for r in table.runs for e in r.events),
        ]
        proof_ok &= all(proof & set(r.evidence_refs) for r in rows) and all(
            proof & set(r.calendar_evidence_refs) for r in table.runs
        )
    check(
        "synthetic_evidence_on_events_footpaths_transfers",
        proof_ok,
        "every run, event, footpath and transfer cites DEMO evidence",
    )
    refs_ok, missing = True, []
    for snap in snapshots:
        nodes = {n["node_id"] for n in snap["access_nodes"]}
        for svc in snap["scheduled_services"]:
            for st in svc["stop_times"]:
                if st["node_id"] not in nodes:
                    refs_ok = False
                    missing.append(st["node_id"])
    for part in cities.values():
        nodes = set(part["timetable"].scope.node_ids)
        services = {s["service_id"] for s in part["snapshot"]["scheduled_services"]}
        for c in comparisons:
            if c["preset_id"].startswith(f"demo-{part['city']}-urban-pt"):
                refs_ok &= set(c["fixed_parameters"]["service_ids"]) <= services
                refs_ok &= c["model_inputs"]["problem_transfer"]["from_stop"] in nodes
    ic_services = {s["service_id"] for s in ic["snapshot"]["scheduled_services"]}
    for c in comparisons:
        if c["preset_id"].startswith("demo-ic-"):
            refs_ok &= set(c["fixed_parameters"]["service_ids"]) <= ic_services
    check("node_and_event_references_resolve", refs_ok and not missing, missing[:5])
    pairs = [
        (p["origin_city_id"], p["destination_city_id"])
        for p in presets
        if p["tab"] == "passenger_links"
    ]
    cargo_pairs = [
        (p["origin_city_id"], p["destination_city_id"])
        for p in presets
        if p["tab"] == "cargo_between" and p["analysis_kind"] == "intercity_cargo"
    ]
    check(
        "six_directed_pairs_each_tab",
        sorted(pairs) == sorted(PAIRS) == sorted(cargo_pairs) and all(a != b for a, b in PAIRS),
        [_pair_key(a, b) for a, b in PAIRS],
    )
    directions_ok = True
    for part in cities.values():
        fnodes = {
            n["freight_node_id"]: n["access_node_id"] for n in part["snapshot"]["freight_nodes"]
        }
        sites = {s["site_id"]: s["access_node_id"] for s in part["snapshot"]["freight_sites"]}
        edges = {e["edge_id"]: e for e in part["snapshot"]["reference_edges"]}
        for row in part["tables"]["freight_edges"]:
            edge = edges[row["edge_id"]]
            expected = (sites[row["site_id"]], fnodes[row["freight_node_id"]])
            if row["direction"] == "inbound":
                expected = expected[::-1]
            directions_ok &= (edge["from_node_id"], edge["to_node_id"]) == expected
    check(
        "freight_directions_match_edges", directions_ok, "outbound site->node, inbound node->site"
    )
    registered = {(f["table"], f["field"]) for f in fields}
    numeric = {
        ("walk_edges", "time_s"),
        ("car_edges", "time_s"),
        ("stop_times", "arrival_seconds"),
        ("freight_nodes", "base_wait_s"),
        ("freight_nodes", "handling_s"),
        ("origins", "population_persons"),
        ("departures", "run_s"),
        ("pipeline", "capacity_t_per_day"),
    }
    check(
        "units_registered_for_numeric_fields",
        numeric <= registered and all(f["unit"] for f in fields),
        sorted(numeric - registered),
    )
    positive = True
    for snap in snapshots:
        positive &= all((e["running_time_seconds"] or 0) > 0 for e in snap["reference_edges"])
        for svc in snap["scheduled_services"]:
            st = svc["stop_times"]
            positive &= all(
                b["arrival_seconds"] > a["departure_seconds"]
                for a, b in zip(st, st[1:], strict=False)
            )
    for part in cities.values():
        positive &= all(f.seconds and f.seconds > 0 for f in part["timetable"].footpaths)
        positive &= all(
            m["base_wait_s"] > 0 and m["handling_s"] > 0 for m in part["model"]["freight_nodes"]
        )
    for chain in ic["chains"]:
        positive &= all(leg["run_s"] > 0 for leg in chain["legs"]) and chain["after"]["total_s"] > 0
    check("positive_durations", positive, "edges, segments, footpaths, handling, chain legs")
    ids = [p["preset_id"] for p in presets] + [c["comparison_id"] for c in comparisons]
    trips = [s["trip_id"] for snap in snapshots for s in snap["scheduled_services"]]
    created = [i for c in comparisons for i in c["created_ids"]]
    check(
        "no_duplicate_ids",
        len(ids) == len(set(ids))
        and len(trips) == len(set(trips))
        and not set(created) & set(trips)
        and len(created) == len(set(created)),
        {"presets_comparisons": len(ids), "trips": len(trips), "created": len(created)},
    )
    midnight = [d["trip_id"] for d in ic["departures"] if d["crosses_midnight"]]
    utc_ok = True
    for snap in snapshots:
        for svc in snap["scheduled_services"]:
            times = [_at(svc["timezone"], st["departure_seconds"]) for st in svc["stop_times"]]
            utc_ok &= all(b > a for a, b in zip(times, times[1:], strict=False))
    check(
        "midnight_crossing_monotonic_utc", utc_ok and bool(midnight), {"crossing_trips": midnight}
    )
    tz_ok = all(
        s["timezone"] == part["tz"]
        for part in cities.values()
        for s in part["snapshot"]["scheduled_services"]
    )
    tz_ok &= all(
        s["timezone"] == CITIES[s["trip_id"].split("-")[3]]["tz"]
        for s in ic["snapshot"]["scheduled_services"]
    )
    check(
        "timezones_consistent",
        tz_ok
        and _utc("kaliningrad") == "2026-11-11T06:30:00Z"
        and _utc("moscow") == "2026-11-11T05:30:00Z",
        {c: _utc(c) for c in CITIES},
    )
    compat_ok = True
    for part in cities.values():
        profile = part["model"]["cargo_profile"]
        for node in part["model"]["freight_nodes"]:
            compat_ok &= set(profile["handling_requirements"]) <= set(node["handling_types"])
            compat_ok &= profile["vehicle_constraints"]["max_weight_tonnes"] <= node["max_mass_t"]
            compat_ok &= profile["vehicle_constraints"]["max_height_m"] <= node["max_height_m"]
        pt = part["model"]["public_transport"]
        start, end = (_sec(t) for t in pt["resource"]["working_window_local"])
        extra_dep = _sec(pt["extra_departure_slot"]["departure_local"].split("+")[0])
        compat_ok &= start <= extra_dep and extra_dep + pt["turnaround"]["turnaround_s"] <= end
        compat_ok &= (
            pt["demand"]["capacity_persons_per_hour"] > 0 and pt["resource"]["capacity_persons"] > 0
        )
    check(
        "cargo_and_resource_compatibility",
        compat_ok,
        "profile vs node handling/mass/height; reserve vehicle window covers extra trip and turnaround",
    )
    in_matrix = [p for p in presets if p.get("in_acceptance_matrix", True)]
    check("matrix_24_base_presets", len(in_matrix) == 24, len(in_matrix))
    check("matrix_21_comparisons", len(comparisons) == 21, len(comparisons))
    check(
        "comparison_ids_stable",
        all(c["ids"]["after"] == c["ids"]["before"] + c["created_ids"] for c in comparisons),
        "after = before + created",
    )
    check(
        "walk_car_presets_without_schedule_operations",
        all(
            not p["schedule_operations"]
            for p in presets
            if p.get("passenger_mode") in ("walk", "private_car")
        ),
        "in_city walk/car",
    )
    pt_ok = True
    for part in cities.values():
        pt, ck = part["model"]["public_transport"], part["checks"]["public_transport"]
        pt_ok &= (
            pt["problem_transfer"]["slack_s"]
            < 0
            <= pt["problem_transfer"]["slack_after_coordinate_s"]
        )
        pt_ok &= ck["base"]["transfers"] >= 1 and ck["base"]["state"] in ("reachable", "unknown")
        pt_ok &= all(
            ck[op]["total_s"] < ck["base"]["total_s"]
            for op in ("coordinate_transfer", "additional_departure")
        )
        pt_ok &= pt["extra_departure_slot"]["new_trip_id"] in ck["additional_departure"]["trip_ids"]
    check(
        "pt_problem_transfer_missed_then_fixed",
        pt_ok,
        {
            c: {
                k: p["checks"]["public_transport"][k]["total_s"]
                for k in ("base", "coordinate_transfer", "additional_departure")
            }
            for c, p in cities.items()
        },
    )
    reasons = sorted(
        {
            r
            for p in cities.values()
            for k in ("base", "coordinate_transfer", "additional_departure")
            for r in p["checks"]["public_transport"][k]["reasons"]
        }
    )
    check("engine_reports_no_missing_rules", not any("UNAVAILABLE" in r for r in reasons), reasons)
    check(
        "every_comparison_changes_its_metric",
        all(c["delta"]["total_s"] > 0 for c in comparisons),
        [c["comparison_id"] for c in comparisons if c["delta"]["total_s"] <= 0],
    )
    check(
        "pipeline_separate_from_delivery",
        all(
            "pipeline" not in str(p.get("analysis_kind")) or p.get("in_acceptance_matrix") is False
            for p in presets
        )
        and not any(s["pipeline_connections"] for s in snapshots if s is not pipe["snapshot"]),
        "pipeline only in demo-pipeline snapshot",
    )
    return out


def _coverage(cities: dict[str, dict[str, Any]], ic: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for city, part in cities.items():
        t = part["tables"]
        model_dest = sum(1 for d in t["destinations"] if d["origin"] == "synthetic")
        reach = part["checks"]["public_transport"]
        base = {"scope": city, "direction": "", "coverage": "model"}
        rows.append(
            base
            | {
                "tab": "in_city",
                "mode": "walk",
                "status": "model",
                "real": "6 origins (GHSL cells), " + f"{18 - model_dest} destinations",
                "synthetic": f"{model_dest} destinations, {len(t['walk_edges'])} edge times",
                "note": "speed 5 km/h from the accepted walking profile",
            }
        )
        rows.append(
            base
            | {
                "tab": "in_city",
                "mode": "private_car",
                "status": "model",
                "real": "same origins/destinations",
                "synthetic": f"{len(t['car_edges'])} edge times, {CITIES[city]['car_kmh']} km/h",
                "note": "no traffic data; static",
            }
        )
        rows.append(
            base
            | {
                "tab": "in_city",
                "mode": "public_transport",
                "status": "model",
                "real": f"stop positions: {CITIES[city]['stops']}",
                "synthetic": f"2 routes, {len({r['trip_id'] for r in t['stop_times']})} trips, {len(t['footpaths'])} footpaths, 2 transfer rules",
                "note": f"OD reached {reach['od_reached_base']}/{reach['od_total']}",
            }
        )
        nodes = t["freight_nodes"]
        rows.append(
            base
            | {
                "tab": "cargo_in_city",
                "mode": "road",
                "status": "real+model" if any(n["origin"] != "model" for n in nodes) else "model",
                "real": "3 sites"
                + "".join(f", {n['kind']} location" for n in nodes if n["origin"] != "model")
                + (
                    ", truck observations"
                    if any(r["origin"] == "real_observation" for r in t["freight_edges"])
                    else ""
                ),
                "synthetic": ", ".join(
                    f"{n['kind']} " + ("node" if n["origin"] == "model" else "operations")
                    for n in nodes
                )
                + ", handling, resource",
                "note": CARGO_PROFILE,
            }
        )
        rows.append(
            base
            | {
                "tab": "cargo_between",
                "mode": "pipeline",
                "status": "model" if city == "novorossiysk" else "not_applicable",
                "real": "pipeline terminal locations (OSM)" if city == "novorossiysk" else "",
                "synthetic": "connections, direction, capacity/volume per day"
                if city == "novorossiysk"
                else "",
                "note": "separate profile, not in the delivery index"
                if city == "novorossiysk"
                else "NO_PIPELINE_PROFILE",
            }
        )
    for app in ic["applicability"]:
        for tab, modes in (
            ("passenger_links", app["passenger"]),
            ("cargo_between", app["freight"]),
        ):
            for mode, status in modes.items():
                kind, _, reason = status.partition(":")
                rows.append(
                    {
                        "scope": app["pair"],
                        "direction": app["pair"].replace("--", "→"),
                        "tab": tab,
                        "mode": mode,
                        "status": kind,
                        "real": (
                            "sites and ports (accepted bundles)"
                            if tab == "cargo_between"
                            else "nodes (intercity directory)"
                        )
                        if kind.startswith("model")
                        else (
                            "Valhalla freeflow observation" if kind == "real_observation" else ""
                        ),
                        "synthetic": "schedule/chain" if kind.startswith("model") else "",
                        "note": reason,
                        "coverage": "model" if kind.startswith("model") else kind,
                    }
                )
    return rows


def _fields() -> list[dict[str, Any]]:
    g = f"{MODULE}:{RULES_VERSION}"

    def f(
        table: str, field: str, unit: str, origin: str, generator: str, template: str
    ) -> dict[str, Any]:
        return {
            "table": table,
            "field": field,
            "unit": unit,
            "origin": origin,
            "generator": f"{g}:{generator}",
            "seed": SEED,
            "template_source": template,
        }

    return [
        f(
            "origins",
            "location",
            "EPSG:4326",
            "real",
            "_origins",
            "sample_points of the accepted city bundle",
        ),
        f(
            "origins",
            "population_persons",
            "persons",
            "real",
            "_origins",
            "GHSL R2023A E2025 100 m via population_cells",
        ),
        f(
            "destinations",
            "location",
            "EPSG:4326",
            "real|synthetic",
            "_destinations",
            "accepted destinations; model point snapped to a real walk node (rng key city:model-destination:category:index)",
        ),
        f(
            "stops",
            "location",
            "EPSG:4326",
            "real|real_walk_node",
            "_stop_candidates",
            "GTFS stops / gortrans39 stop lists / walk nodes",
        ),
        f(
            "stops",
            "route,sequence",
            "ordinal",
            "synthetic",
            "_city_part",
            "nearest stop to three corridor origins, hub and three destinations",
        ),
        f(
            "stop_times",
            "arrival_seconds",
            "s since local service midnight",
            "synthetic",
            "_city_part",
            f"segment = straight*{WALK_DETOUR}/{BUS_KMH} km/h, step 30 s, dwell {BUS_DWELL_S} s",
        ),
        f(
            "stop_times",
            "departure_seconds",
            "s since local service midnight",
            "synthetic",
            "_city_part",
            f"route A headway {HEADWAY_A_S} s from 07:00, route B headway {HEADWAY_B_S} s",
        ),
        f(
            "footpaths",
            "seconds",
            "s",
            "synthetic",
            "_walk_s",
            f"straight*{WALK_DETOUR} at {WALK_KMH} km/h (accepted walking profile), <= {FOOTPATH_LIMIT_M} m",
        ),
        f(
            "transfers",
            "minimum_seconds",
            "s",
            "synthetic",
            "_city_part",
            f"walk + {MIN_CHANGE_S} s change",
        ),
        f(
            "walk_edges",
            "time_s",
            "s",
            "synthetic",
            "_walk_s",
            f"straight*{WALK_DETOUR} at {WALK_KMH} km/h",
        ),
        f("walk_edges", "length_m", "m", "synthetic", "_city_part", f"straight*{WALK_DETOUR}"),
        f(
            "car_edges",
            "time_s",
            "s",
            "synthetic",
            "_road_s",
            f"straight*{CAR_DETOUR} at city car_kmh + {CAR_ACCESS_S} s access",
        ),
        f("car_edges", "length_m", "m", "synthetic", "_city_part", f"straight*{CAR_DETOUR}"),
        f(
            "pt_demand",
            "demand_persons_per_hour",
            "persons/h",
            "synthetic",
            "_city_part",
            "route B capacity x load factor U(0.93, 1.07), rng key city:demand",
        ),
        f(
            "pt_resource",
            "capacity_persons",
            "persons",
            "synthetic",
            "_city_part",
            f"reserve bus {BUS_CAPACITY} persons, window 06:00-14:00",
        ),
        f(
            "pt_turnaround",
            "turnaround_s",
            "s",
            "synthetic",
            "_city_part",
            "2 x route B run + 2 x 300 s layover",
        ),
        f(
            "freight_nodes",
            "location",
            "EPSG:4326",
            "real|synthetic",
            "_city_part",
            "accepted port; model node on a real road projection 6-14 km from centre (rng key city:model-freight-node:kind)",
        ),
        f(
            "freight_nodes",
            "base_wait_s",
            "s",
            "synthetic",
            "_city_part",
            "U(2100, 3300) step 60, rng key city:handling:index",
        ),
        f("freight_nodes", "handling_s", "s", "synthetic", "_city_part", "U(1500, 2400) step 60"),
        f(
            "freight_nodes",
            "equipment_units",
            "units",
            "synthetic",
            "_city_part",
            "randint(2, 4) reach stackers",
        ),
        f("freight_nodes", "max_mass_t", "t", "synthetic", "_city_part", "port 40, others 30"),
        f("freight_nodes", "max_height_m", "m", "synthetic", "_city_part", "4.5"),
        f(
            "freight_edges",
            "time_s",
            "s",
            "real_observation|synthetic",
            "_city_part",
            f"Valhalla truck freeflow observation (handoff runtime) or straight*{CAR_DETOUR} at {TRUCK_KMH} km/h",
        ),
        f(
            "freight_edges",
            "length_m",
            "m",
            "real_observation|synthetic",
            "_city_part",
            "as time_s",
        ),
        f(
            "departures",
            "departure_local",
            "local clock",
            "synthetic",
            "IC_SERVICES",
            "fixed model clocks per mode and directed pair",
        ),
        f(
            "departures",
            "run_s",
            "s",
            "synthetic",
            "_intercity_part",
            f"air: great-circle/{AIR_KMH} km/h + {AIR_TAXI_S} s; rail/bus: real road length / 62-70 km/h or 68 km/h",
        ),
        f(
            "intercity_access",
            "access_s",
            "s",
            "synthetic",
            "_intercity_part",
            f"centre -> node straight*{CAR_DETOUR} at {ROAD_ACCESS_KMH} km/h + process {PROCESS_S}",
        ),
        f(
            "intercity_demand",
            "demand_persons",
            "persons",
            "synthetic",
            "_intercity_part",
            "capacity x U(0.75, 0.95), rng key intercity:pair:demand",
        ),
        f(
            "road_observations",
            "time_s",
            "s",
            "real_observation",
            "extract",
            "Valhalla public freeflow auto (handoff threecity-road-observations-2026-11-11.1)",
        ),
        f(
            "chains",
            "run_s",
            "s",
            "synthetic",
            "_intercity_part",
            "distance / rail 40 km/h or water 26 km/h, step 600 s",
        ),
        f(
            "chains",
            "handling_at_end_s",
            "s",
            "synthetic",
            "_intercity_part",
            "origin handling U(7200, 10800), transshipment U(18000, 25200), rng key intercity-freight:pair",
        ),
        f(
            "chains",
            "distance_km",
            "km",
            "synthetic",
            "_intercity_part",
            "rail 1300/1440 km, sea 6900 km (model)",
        ),
        f("chains", "demand_teu", "TEU", "synthetic", "_intercity_part", "randint(36, 60)"),
        f(
            "pipeline",
            "capacity_t_per_day",
            "t/day",
            "synthetic",
            "_pipeline_part",
            "U(250000, 350000) step 1000, rng key pipeline",
        ),
        f(
            "pipeline",
            "volume_t_per_day",
            "t/day",
            "synthetic",
            "_pipeline_part",
            "capacity x U(0.7, 0.9)",
        ),
    ]


# ----------------------------------------------------------------------------- output


def _dump(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode()


def _csv(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    names: dict[str, None] = {}
    for row in rows:
        names.update(dict.fromkeys(row))
    writer = csv.DictWriter(buf, fieldnames=list(names), lineterminator="\n", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else v
                for k, v in row.items()
            }
        )
    return buf.getvalue().encode()


def _coverage_md(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Территория / направление | Вкладка | Режим | Статус | Реальное | Модельное | Примечание |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['direction'] or r['scope']} | {r['tab']} | {r['mode']} | {r['status']} | {r['real']} | {r['synthetic']} | {r['note']} |"
        )
    return "\n".join(lines) + "\n"


def build(
    out_dir: Path | None = None,
    *,
    extract_path: Path | None = None,
    bundle_root: Path | None = None,
    directory_root: Path | None = None,
) -> dict[str, Any]:
    base = repo_root() / "data" / "demo"
    out_dir = out_dir or base / DATASET_ID
    extract_path = extract_path or base / "inputs" / "waydex-handoff-2026-11-11-extract.json"
    ext_bytes = extract_path.read_bytes()
    ext = json.loads(ext_bytes)
    if (
        ext.get("format") != EXTRACT_FORMAT
        or ext.get("archive_sha256") != HANDOFF["runtime_archive_sha256"]
    ):
        raise ValueError("runtime extract is not from the pinned handoff archive")
    cities = {c: _city_part(c, ext, bundle_root) for c in CITIES}
    ic = _intercity_part(ext, cities, directory_root)
    pipe = _pipeline_part(cities["novorossiysk"])
    presets, comparisons = _matrix(cities, ic, pipe)
    fields = _fields()
    qa = _qa(cities, ic, pipe, presets, comparisons, fields)
    failed = [q["check_id"] for q in qa if not q["passed"]]
    if failed:
        raise ValueError(f"demo QA failed: {failed}")
    coverage = _coverage(cities, ic)
    files: dict[str, bytes] = {}
    snapshots = []
    for part in cities.values():
        snap = part["snapshot"]
        files[f"snapshots/{snap['snapshot_id']}.json"] = _dump(snap)
        files[f"receipts/{snap['snapshot_id']}-transport.json"] = _dump(part["receipt"])
        for name, rows in part["tables"].items():
            files[f"tables/{part['city']}/{name}.csv"] = _csv(rows)
        files[f"tables/{part['city']}/model.json"] = _dump(part["model"])
    for snap in (ic["snapshot"], pipe["snapshot"]):
        files[f"snapshots/{snap['snapshot_id']}.json"] = _dump(snap)
    files["tables/intercity/departures.csv"] = _csv(ic["departures"])
    files["tables/intercity/road_observations.csv"] = _csv(ic["road"])
    files["tables/intercity/freight_chain_legs.csv"] = _csv(
        [
            leg | {"chain_id": c["chain_id"], "pair": c["pair"]}
            for c in ic["chains"]
            for leg in c["legs"]
        ]
    )
    files["tables/intercity/applicability.json"] = _dump(ic["applicability"])
    files["tables/intercity/freight_chains.json"] = _dump(ic["chains"])
    files["tables/pipeline/connections.csv"] = _csv(pipe["metrics"])
    files["tables/pipeline/nodes.csv"] = _csv(pipe["nodes"])
    for c in comparisons:
        files[f"control/{c['comparison_id']}.json"] = _dump(c)
    files["presets.json"] = _dump(presets)
    files["fields.json"] = _dump(fields)
    files["qa.json"] = _dump(qa)
    files["coverage.csv"] = _csv(coverage)
    files["coverage.md"] = _coverage_md(coverage).encode()
    for snap in [*(p["snapshot"] for p in cities.values()), ic["snapshot"], pipe["snapshot"]]:
        path = f"snapshots/{snap['snapshot_id']}.json"
        snapshots.append(
            {
                "snapshot_id": snap["snapshot_id"],
                "path": path,
                "sha256": _sha(files[path]),
                "content_hash": _canonical(snap),
                "receipt": f"receipts/{snap['snapshot_id']}-transport.json"
                if snap["snapshot_id"] in {p["snapshot"]["snapshot_id"] for p in cities.values()}
                else None,
            }
        )
    real_inputs = (
        [
            {
                "kind": "accepted_bundle",
                "snapshot_id": p["real_snapshot_id"],
                "path": f"data/bundles/{p['real_snapshot_id']}",
                "content_hash": next(
                    s["content_hash"]
                    for s in ext["runtime_snapshots"]
                    if s["snapshot_id"] == p["real_snapshot_id"]
                ),
                "runtime_sha256": next(
                    s["sha256"]
                    for s in ext["runtime_snapshots"]
                    if s["snapshot_id"] == p["real_snapshot_id"]
                ),
            }
            for p in cities.values()
        ]
        + [
            {
                "kind": "runtime_snapshot",
                "snapshot_id": s["snapshot_id"],
                "content_hash": s["content_hash"],
                "runtime_sha256": s["sha256"],
            }
            for s in ext["runtime_snapshots"]
            if s["snapshot_id"].startswith(
                ("threecity-road", "kaliningrad-cargo", "novorossiysk-cargo")
            )
        ]
        + [
            {
                "kind": "directory",
                "directory_id": INTERCITY_DIRECTORY,
                "path": f"data/directories/{INTERCITY_DIRECTORY}/nodes.json",
                "sha256": _file_sha(
                    (directory_root or repo_root() / "data" / "directories")
                    / INTERCITY_DIRECTORY
                    / "nodes.json"
                ),
            },
            {
                "kind": "runtime_extract",
                "path": "data/demo/inputs/waydex-handoff-2026-11-11-extract.json",
                "sha256": _sha(ext_bytes),
                "archive_sha256": ext["archive_sha256"],
                "member_sha256": ext["member_sha256"],
            },
        ]
    )
    runtime_profiles = [
        {
            "city_id": p["city_id"],
            "profile_id": p["profile_id"],
            "analysis_kind": p["analysis_kind"],
            "passenger_modes": p["passenger_modes"],
            "categories": p["categories"],
            "executor": p["executor"],
            "snapshot_id": p["snapshot_id"],
        }
        for p in ext["runtime_profiles"]
        if p["analysis_kind"] != "urban_cargo"
    ]
    cargo_runtime = sorted(
        {p["snapshot_id"] for p in ext["runtime_profiles"] if p["analysis_kind"] == "urban_cargo"}
    )
    manifest = {
        "format": "waydex-demo-dataset/1",
        "dataset_id": DATASET_ID,
        "rules": RULES_VERSION,
        "synthetic": True,
        "labels": list(LIMITATIONS),
        "generator": {
            "module": MODULE,
            "seed": SEED,
            "command": "python -m a2_data_pipeline.demo_dataset build",
            "generated_at": GENERATED_AT,
        },
        "slice": {
            "service_date": SERVICE_DATE.isoformat(),
            "local_time": "08:30",
            "utc": {c: _utc(c) for c in CITIES},
            "timezones": {c: CITIES[c]["tz"] for c in CITIES},
            "service_clock": CLOCK,
            "outside_slice": "DATE_OR_TIME_OUTSIDE_DEMO_SLICE: other dates, times and modes are not in this set",
        },
        "cities": list(CITIES),
        "pairs": [_pair_key(a, b) for a, b in PAIRS],
        "handoff": HANDOFF,
        "real_inputs": real_inputs,
        "real_runtime_profiles": runtime_profiles,
        "real_runtime_cargo_snapshots": cargo_runtime,
        "snapshots": snapshots,
        "presets": presets,
        "comparisons": [
            {
                k: c[k]
                for k in (
                    "comparison_id",
                    "preset_id",
                    "operation_type",
                    "contract_status",
                    "ids",
                    "created_ids",
                    "delta",
                )
            }
            | {
                "inputs": c.get("fixed_parameters") or c["model_inputs"],
                "control": f"control/{c['comparison_id']}.json",
            }
            for c in comparisons
        ],
        "counts": {
            "base_presets": sum(1 for p in presets if p.get("in_acceptance_matrix", True)),
            "comparisons": len(comparisons),
            "extra_profiles": [
                p["preset_id"] for p in presets if not p.get("in_acceptance_matrix", True)
            ],
        },
        "qa": {"passed": all(q["passed"] for q in qa), "checks": [q["check_id"] for q in qa]},
        "api_notes": [
            "DemoTransportReceipt and demo map/scenario guards accept only snapshot ids starting with demo-moscow-; demo-kaliningrad- and demo-novorossiysk- need the same opt-in (BACKEND)",
            "scenarios v2 operation_type is coordinate_transfer | additional_departure for urban public transport; intercity passenger and cargo operations need a contract and engine change",
            "A2_DEMO_TRANSPORT_ENABLED=true on a separately labelled stand only",
        ],
    }
    manifest["files"] = {path: _sha(data) for path, data in sorted(files.items())}
    files["manifest.json"] = _dump(manifest)
    files["README.md"] = _readme(manifest, cities, ic, coverage).encode()
    files["SHA256SUMS"] = "".join(
        f"{_sha(data)}  {path}\n" for path, data in sorted(files.items())
    ).encode()
    staging = out_dir.parent / f".staging-{out_dir.name}"
    if staging.exists():
        import shutil

        shutil.rmtree(staging)
    for path, data in files.items():
        target = staging / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    staging.rename(out_dir)
    return manifest


def _readme(
    manifest: dict[str, Any],
    cities: dict[str, dict[str, Any]],
    ic: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {DATASET_ID} — синтетический демо-набор (МОДЕЛЬ)",
        "",
        "Отдельный versioned набор для демо-стенда. Это **модельные данные**: реальные только узлы, координаты, население и",
        "наблюдения, перечисленные ниже. Рейтингом городов это не является (`NOT_A_REAL_CITY_RANKING`).",
        "",
        f"- Срез: {SERVICE_DATE.isoformat()} 08:30 по местному времени — Калининград {manifest['slice']['utc']['kaliningrad']}, Москва и Новороссийск {manifest['slice']['utc']['moscow']}.",
        "  Другие даты, время и режимы в набор не входят: `DATE_OR_TIME_OUTSIDE_DEMO_SLICE`.",
        f"- Генератор: `python -m a2_data_pipeline.demo_dataset build`, seed {SEED}, правила `{RULES_VERSION}`. Повторный запуск даёт те же байты.",
        "- Метка синтетики: источник `DEMO_SYNTHETIC_TRANSPORT_MODEL` в `qa.checks`, `source_versions` и evidence каждого модельного ряда.",
        f"- Матрица: {manifest['counts']['base_presets']} базовых пресетов и {manifest['counts']['comparisons']} сравнений до/после; трубопровод — отдельный профиль `demo-novorossiysk-pipeline`, не входит в индекс доставки.",
        f"- QA: {'все проверки пройдены' if manifest['qa']['passed'] else 'есть ошибки'} ({len(manifest['qa']['checks'])} проверок, `qa.json`).",
        "",
        "## Что реальное, что модельное",
        "",
        "| Город | Реальные входы | Модель | Что запускается на проверенной поставке |",
        "| --- | --- | --- | --- |",
    ]
    for city, part in cities.items():
        t = part["tables"]
        model_dest = [d["destination_id"] for d in t["destinations"] if d["origin"] == "synthetic"]
        runs = [p for p in manifest["real_runtime_profiles"] if p["city_id"] == city]
        run_text = "; ".join(
            f"{p['profile_id']} ({'/'.join(p['passenger_modes'])}, {len(p['categories'])} кат.)"
            for p in runs
        )
        lines.append(
            f"| {city} | снимок `{part['real_snapshot_id']}`: 6 точек старта с населением GHSL, {18 - len(model_dest)} объектов категорий, "
            f"остановки ({CITIES[city]['stops']}), 3 грузовые площадки"
            + (
                ", порт и наблюдения грузовика"
                if any(r["origin"] == "real_observation" for r in t["freight_edges"])
                else ""
            )
            + f" | {len(model_dest)} модельных объектов, времена пешком/авто, 2 маршрута ОТ с рейсами, спрос/ресурс/оборот, грузовые узлы-модели и обработка | {run_text}; грузовик: {'да' if city != 'moscow' else 'площадки без узлов назначения'} |"
        )
    lines += [
        "",
        "Межгород: узлы из каталога `russia-intercity-nodes-2026-11-09.2` (реальные), дорожные наблюдения Valhalla freeflow для 4 направлений (реальные),",
        "Калининград↔Новороссийск — наблюдения нет (лимит провайдера 1500 км), показывается причина. Все расписания самолётов, поездов, автобусов,",
        "грузовые цепочки и трубопровод — модель. Таблица применимости: `tables/intercity/applicability.json`, сводка: `coverage.md`.",
        "",
        "## Файлы",
        "",
        "- `manifest.json` — для Искандера: 3 города, 6 пар, снимки (id, sha256, content hash), receipts, 24 пресета, входы 21 сравнения, реальные версии и суммы.",
        "- `snapshots/*.json` — AcceptedData v2 (`demo-<город>-2026-11-11.1`, `demo-intercity-…`, `demo-pipeline-…`).",
        "- `receipts/*-transport.json` — `waydex-demo-closed-transport/1`: закрытая модель расписания (рейсы, события, подходы, пересадки) с evidence на каждый ряд.",
        "- `tables/<город>/*.csv`, `tables/intercity/*`, `control/*.json` — для Матвея: исходные таблицы и контрольные входы/ожидаемые значения обеих операций.",
        "- `presets.json` — для Марии: пресеты, которые будет отдавать API (подписи, дата/время, операции, ограничения).",
        "- `coverage.csv` / `coverage.md` — наличие по каждому городу и направлению; `fields.json` — генератор, seed, единица и шаблон каждого поля.",
        "",
        "## Ограничения для API",
        "",
        *[f"- {note}" for note in manifest["api_notes"]],
        "",
        "## Покрытие (модельное)",
        "",
        _coverage_md(coverage),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="read the pinned handoff runtime archive")
    ex.add_argument("--archive", type=Path, required=True)
    ex.add_argument("--out", type=Path, default=None)
    bd = sub.add_parser("build", help="generate the dataset into data/demo")
    bd.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "extract":
        out = (
            args.out
            or repo_root() / "data" / "demo" / "inputs" / "waydex-handoff-2026-11-11-extract.json"
        )
        payload = extract(args.archive, out)
        print(
            json.dumps(
                {
                    "out": str(out),
                    "road_rows": len(payload["road"]["rows"]),
                    "cargo_rows": len(payload["cargo"]["rows"]),
                }
            )
        )
        return 0
    manifest = build(args.out)
    print(
        json.dumps(
            {
                "dataset_id": manifest["dataset_id"],
                "counts": manifest["counts"],
                "qa_passed": manifest["qa"]["passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
