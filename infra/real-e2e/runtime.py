#!/usr/bin/env python3
"""Reproduce the bounded real Kaliningrad pedestrian pilot, without paid APIs."""

import argparse
import dataclasses
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

IMAGE = "ghcr.io/valhalla/valhalla@sha256:4ac3d13d1019f9a130e9e6b6924c16ea55da0fa51e3653039152e33ad4095661"
BUNDLE = "kaliningrad-urban-2026-11-10.2"
SUPPORTED_BUNDLES = (BUNDLE, "kaliningrad-urban-2026-11-10.3")
PBF = "kaliningrad-261107.osm.pbf"
PBF_URL = "https://download.geofabrik.de/russia/" + PBF
PBF_SHA = "769a6a6ac552b534057f2480b88f1c2d0709636761ab6d08483efe69c1e97b69"
SERIALIZER = "valhalla-3.5.1-locate_serializer.cc"
SERIALIZER_URL = (
    "https://raw.githubusercontent.com/valhalla/valhalla/3.5.1/src/tyr/locate_serializer.cc"
)
SERIALIZER_SHA = "0f1182a370d6cdefb463574ac9cd1c0a1bdf4ec92a28d898e2295d55c8bfe192"
CONFIG_URL = (
    "https://raw.githubusercontent.com/valhalla/valhalla/3.5.1/scripts/valhalla_build_config"
)
CONFIG_SHA = "7d01244c5b45ced3011a78d6fd688fb237eee9e13325f8abac4d3276d964d06e"
PROFILE = "waydex-walk-static-5kmh-v1"
OPTIONS = {"type": "foot", "walking_speed": 5, "shortest": False, "use_ferry": 0}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def write(path, value):
    """Exclusive creation: published artifacts are never overwritten."""
    payload = value if isinstance(value, bytes) else encoded(value) + b"\n"
    with path.open("xb") as target:
        target.write(payload)
    path.chmod(0o444)


def read(path):
    return json.loads(path.read_bytes())


def post(args, action, payload, save=False):
    request = encoded(payload)
    req = urllib.request.Request(
        args.endpoint + action,
        data=request,
        headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read(4 * 1024 * 1024 + 1)
        if response.status != 200 or len(body) > 4 * 1024 * 1024:
            raise RuntimeError("HTTP status or response size outside pilot limits")
    digest = hashlib.sha256(body).hexdigest()
    if save:
        for suffix, content in (("request", request), ("response", body)):
            path = args.runtime / f"{action.strip('/')}-{digest}-{suffix}.json"
            if path.exists():
                if path.read_bytes() != content:
                    raise RuntimeError("Immutable observation changed")
            else:
                write(path, content)
    return json.loads(body), digest


def source_models(args):
    # Keep CLI help/build usable before workspace Python packages are installed.
    from a2_data_pipeline.contract_v2 import load_accepted_data
    from a2_data_pipeline.store import load_bundle

    return load_bundle(args.bundle_id), load_accepted_data(args.bundle_id).model_dump(mode="json")


def location(node):
    return dict(
        lon=node.lon,
        lat=node.lat,
        type="break",
        radius=0,
        search_cutoff=1,
        node_snap_tolerance=0,
        search_filter={"exclude_ferry": True},
    )


def locate(args, locations, save=True):
    return post(
        args,
        "/locate",
        {
            "locations": locations,
            "costing": "pedestrian",
            "costing_options": {"pedestrian": OPTIONS},
            "verbose": True,
        },
        save,
    )


def fetch(args):
    args.runtime.mkdir(parents=True, exist_ok=True)
    for name, url, expected in (
        (PBF, PBF_URL, PBF_SHA),
        (SERIALIZER, SERIALIZER_URL, SERIALIZER_SHA),
        ("valhalla-build-config-3.5.1.py", CONFIG_URL, CONFIG_SHA),
    ):
        target = args.runtime / name
        if not target.exists():
            temporary = args.runtime / (name + ".download")
            with (
                urllib.request.urlopen(url, timeout=60) as response,
                temporary.open("xb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if expected and sha(temporary) != expected:
                raise RuntimeError("Downloaded source hash mismatch: " + name)
            # link() fails rather than overwriting a concurrently published file.
            os.link(temporary, target)
            temporary.unlink()
            target.chmod(0o444)
        if expected and sha(target) != expected:
            raise RuntimeError("Existing source hash mismatch: " + name)
        print(name, sha(target))


def build(args):
    root = args.runtime
    if sha(root / "valhalla-build-config-3.5.1.py") != CONFIG_SHA:
        raise RuntimeError("Pinned configuration generator mismatch")
    if sha(root / PBF) != PBF_SHA or sha(root / SERIALIZER) != SERIALIZER_SHA:
        raise RuntimeError("Pinned source mismatch")
    for name in (
        "tiles",
        "valhalla.json",
        "walking-profile.json",
        "valhalla-build-manifest.json",
        "valhalla-build.log",
    ):
        if (root / name).exists():
            raise RuntimeError("Build refuses existing output; use a fresh runtime: " + name)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", IMAGE]))[0]
    version = subprocess.check_output(
        ["docker", "run", "--rm", "--pull=never", IMAGE, "valhalla_build_tiles", "--version"],
        text=True,
    )
    if version.strip() != "valhalla_build_tiles 3.5.1":
        raise RuntimeError("Unexpected binary version")
    command = [
        sys.executable,
        str(root / "valhalla-build-config-3.5.1.py"),
        "--mjolnir-tile-dir",
        "/data/tiles",
        "--mjolnir-tile-extract",
        "/data/tiles.tar",
        "--mjolnir-admin",
        "/data/admins.sqlite",
        "--mjolnir-timezone",
        "/data/timezones.sqlite",
        "--mjolnir-concurrency",
        "2",
        "--mjolnir-data-processing-use-admin-db",
        "false",
        "--httpd-service-listen",
        "tcp://*:8002",
    ]
    write(root / "valhalla.json", json.loads(subprocess.check_output(command)))
    write(
        root / "walking-profile.json",
        {
            "profile_version": PROFILE,
            "engine_version": "3.5.1",
            "pedestrian_options": OPTIONS,
            "assumptions": [
                "5 km/h is an explicit model assumption, not empirical calibration.",
                "Static pedestrian routing only; no minimum-time proof.",
                "Administrative/timezone databases not collected; source PBF administrative tags used.",
            ],
        },
    )
    write(root / "valhalla-image-inspect.json", image)
    write(root / "valhalla-version.txt", version.encode())
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        args.name + "-build",
        "--mount",
        f"type=bind,source={root},target=/data",
        IMAGE,
        "valhalla_build_tiles",
        "-c",
        "/data/valhalla.json",
        "/data/" + PBF,
    ]
    with (root / "valhalla-build.log").open("x") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    files = [
        {"path": str(path.relative_to(root)), "sha256": sha(path), "bytes": path.stat().st_size}
        for path in sorted((root / "tiles").rglob("*"))
        if path.is_file()
    ]
    write(
        root / "valhalla-build-manifest.json",
        {
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - timer,
            "command": command,
            "image_id": image["Id"],
            "image_repo_digests": image["RepoDigests"],
            "source_sha256": PBF_SHA,
            "config_sha256": sha(root / "valhalla.json"),
            "profile_sha256": sha(root / "walking-profile.json"),
            "files": files,
        },
    )
    print("Built", len(files), "tile files; manifest", sha(root / "valhalla-build-manifest.json"))


def serve(args):
    verify_files(args)
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--pull=never",
            "--name",
            args.name,
            "--publish",
            f"127.0.0.1:{args.port}:8002",
            "--mount",
            f"type=bind,source={args.runtime},target=/data,readonly",
            IMAGE,
            "valhalla_service",
            "/data/valhalla.json",
            "1",
        ],
        check=True,
    )


def select(args):
    output = args.runtime / "pair-selection-report.json"
    if output.exists():
        raise RuntimeError("Selection report exists; use a fresh output directory")
    bundle, _ = source_models(args)
    nodes = {n.access_node_id: n for n in bundle.access_nodes}
    connectors = {c.physical_node_id: c for c in bundle.walk_connectors}
    groups = [
        (
            [
                p
                for p in bundle.sample_points
                if connectors["physical:point:" + p.point_id].traversable
            ],
            "point_id",
        ),
        (
            [
                d
                for d in bundle.destinations
                if d.status == "accepted"
                and d.category == "school"
                and connectors["physical:destination:" + d.destination_id].traversable
            ],
            "destination_id",
        ),
    ]
    if args.point_id:
        groups = [
            ([row for row in rows if getattr(row, key) == identity], key)
            for (rows, key), identity in zip(
                groups, (args.point_id, args.destination_id), strict=True
            )
        ]
        if any(len(rows) != 1 for rows, _ in groups):
            raise RuntimeError("Explicit pair must be accepted and have traversable ML2 connectors")
    compatible, rejected = [], []
    for rows, key in groups:
        located, digest = locate(args, [location(nodes[row.access_node_id]) for row in rows])
        usable = []
        for row, observed in zip(rows, located, strict=True):
            node = nodes[row.access_node_id]
            projections = {
                (e["correlated_lon"], e["correlated_lat"]) for e in observed.get("edges") or []
            }
            reasons = []
            if len(projections) != 1:
                reasons.append("NONUNIQUE_OR_NO_PROJECTION")
            else:
                lon, lat = next(iter(projections))
                for original, actual in ((node.lon, lon), (node.lat, lat)):
                    source, rendered = Decimal(str(original)), Decimal(str(actual))
                    if abs(source - rendered) > Decimal("0.0000005"):
                        reasons.append("OUTSIDE_LOCATE_SERIALIZATION_BIN")
                    # v3.5.1 locate rounds; its route serializer truncates input echoes.
                    if source.quantize(Decimal("0.000001"), rounding=ROUND_DOWN) != rendered:
                        reasons.append("ROUTE_ECHO_SERIALIZER_DIFFERS")
            if reasons:
                rejected.append(
                    {
                        "id": getattr(row, key),
                        "reasons": sorted(set(reasons)),
                        "locate_response_sha256": digest,
                    }
                )
            else:
                usable.append({"id": getattr(row, key), "lon": row.lon, "lat": row.lat})
        compatible.append(usable)
    pairs = sorted(
        (
            {
                "point_id": p["id"],
                "destination_id": d["id"],
                "squared_angular_distance": (p["lon"] - d["lon"]) ** 2 + (p["lat"] - d["lat"]) ** 2,
            }
            for p in compatible[0]
            for d in compatible[1]
        ),
        key=lambda pair: (
            pair["squared_angular_distance"],
            pair["point_id"],
            pair["destination_id"],
        ),
    )
    if not pairs:
        raise RuntimeError("No compatible accepted pair; do not relax coordinate checks")
    write(
        output,
        {
            "selection_only": "Angular ordering selects a smoke-test candidate; it does not prove nearest route or minimum time.",
            "compatible_points": len(compatible[0]),
            "compatible_schools": len(compatible[1]),
            "rejected": rejected,
            "candidate_pairs": pairs[:10],
        },
    )
    print(json.dumps(pairs[0]))


def artifact(args):
    from a2_data_pipeline.supplements import walk_connectors
    from a2_engine import ValhallaBuild, ValhallaCorrelation, WalkCoordinate

    root = args.runtime
    target = root / args.artifact
    if target.exists():
        raise RuntimeError("Artifact exists; never overwrite it")
    verify_files(args)
    _, raw = source_models(args)
    selected = read(root / "pair-selection-report.json")["candidate_pairs"][0]
    point_id, destination_id = selected["point_id"], selected["destination_id"]
    point = next(p for p in raw["sample_points"] if p["point_id"] == point_id)
    destination = next(d for d in raw["destinations"] if d["destination_id"] == destination_id)
    nodes = {n["node_id"]: n for n in raw["access_nodes"]}
    locations = [
        dict(
            nodes[ep["access_node_id"]]["location"],
            type="break",
            radius=0,
            search_cutoff=1,
            node_snap_tolerance=0,
            search_filter={"exclude_ferry": True},
        )
        for ep in (point, destination)
    ]
    located, locate_hash = locate(args, locations)
    now = datetime.now(timezone.utc).isoformat()
    refs = []

    def evidence(prefix, url, section, digest):
        identity = prefix + digest
        refs.append(
            dict(
                evidence_id=identity,
                source_id="waydex-real-e2e-runtime",
                url=url,
                retrieved_at=now,
                observed_at=None,
                section=section + " SHA256 " + digest,
            )
        )
        return identity

    build_hash, profile_hash = (
        sha(root / "valhalla-build-manifest.json"),
        sha(root / "walking-profile.json"),
    )
    build_ref = evidence(
        "valhalla-build-", args.endpoint + "/status", "valhalla-build-manifest.json", build_hash
    )
    profile_ref = evidence(
        "walking-profile-",
        args.endpoint + "/status",
        "Explicit 5 km/h model; walking-profile.json",
        profile_hash,
    )
    serializer_ref = evidence(
        "valhalla-351-locate-serializer-", SERIALIZER_URL, SERIALIZER, SERIALIZER_SHA
    )
    locate_ref = evidence(
        "valhalla-locate-",
        args.endpoint + "/locate",
        "Actual immutable locate response",
        locate_hash,
    )
    source_ref = evidence("osm-geofabrik-pbf-", PBF_URL, "Accepted source PBF", PBF_SHA)
    build_model = ValhallaBuild(
        args.bundle_id,
        raw["provenance"]["graph_version"],
        "waydex-klgd-351-" + build_hash[:16],
        "valhalla-351-tiles-" + build_hash,
        "3.5.1",
        PROFILE,
        encoded(OPTIONS).decode(),
        (build_ref, profile_ref, source_ref),
    )
    correlations = []
    for ep, observed in zip((point, destination), located, strict=True):
        projections = {
            (e["correlated_lon"], e["correlated_lat"]) for e in observed.get("edges") or []
        }
        if len(projections) != 1:
            raise RuntimeError("Unique actual projection required")
        correlated = WalkCoordinate(*next(iter(projections)))
        model = ValhallaCorrelation(
            build_model,
            ep["access_node_id"],
            WalkCoordinate(**nodes[ep["access_node_id"]]["location"]),
            correlated,
            "locate-" + locate_hash + ":" + ep["access_node_id"],
            (locate_ref,),
            6,
            (serializer_ref,),
        )
        correlations.append(
            {key: value for key, value in dataclasses.asdict(model).items() if key != "build"}
        )
    physical = {"physical:point:" + point_id, "physical:destination:" + destination_id}
    connectors = [
        c
        for c in walk_connectors(
            args.bundle_id,
            walking_speed_kmh=5,
            speed_evidence_refs=[profile_ref],
            profile_version=PROFILE,
        )
        if c["physical_node_id"] in physical
    ]
    if len(connectors) != 2 or not all(
        c["traversable"] and c["seconds"] is not None for c in connectors
    ):
        raise RuntimeError("Confirmed physical connectors required")
    status, _ = post(args, "/status", {}, True)
    payload = dict(
        bundle_id=args.bundle_id,
        endpoint_url=args.endpoint,
        point_ids=[point_id],
        destination_ids=[destination_id],
        build=dataclasses.asdict(build_model),
        connectors=connectors,
        correlations=correlations,
        evidence_refs=refs,
        status_expectation={key: status[key] for key in ("version", "tileset_last_modified")},
    )
    # Publish only after the real locate + route response normalizes successfully.
    verify_route(args, payload, raw, save=True)
    write(target, payload)
    (root / "observations").mkdir(exist_ok=True)
    print(str(target), sha(target))


def verify_files(args):
    root = args.runtime
    manifest = read(root / "valhalla-build-manifest.json")
    if sha(root / PBF) != PBF_SHA or sha(root / SERIALIZER) != SERIALIZER_SHA:
        raise RuntimeError("Source checksum differs")
    if (
        sha(root / "valhalla.json") != manifest["config_sha256"]
        or sha(root / "walking-profile.json") != manifest["profile_sha256"]
    ):
        raise RuntimeError("Config/profile checksum differs")
    for row in manifest["files"]:
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root) or sha(path) != row["sha256"]:
            raise RuntimeError("Tile integrity mismatch")
    return manifest


def verify_route(args, artifact_data, accepted, save=False):
    from a2_engine import (
        ValhallaBuild,
        ValhallaCorrelation,
        WalkConnector,
        WalkCoordinate,
        WalkResponseEnvelope,
        analyze_walk_responses,
        load_urban_config,
        load_urban_journey_rules,
        plan_walk_routes,
        prepare_walk_inputs,
    )

    build_model = ValhallaBuild(**artifact_data["build"])
    connectors = [
        WalkConnector(
            **dict(
                row,
                physical_location=WalkCoordinate(**row["physical_location"]),
                access_location=WalkCoordinate(**row["access_location"]),
            )
        )
        for row in artifact_data["connectors"]
    ]
    correlations = [
        ValhallaCorrelation(
            **dict(
                row,
                build=build_model,
                source_location=WalkCoordinate(**row["source_location"]),
                correlated_location=WalkCoordinate(**row["correlated_location"]),
            )
        )
        for row in artifact_data["correlations"]
    ]
    status, _ = post(args, "/status", {}, save)
    if any(status.get(key) != value for key, value in artifact_data["status_expectation"].items()):
        raise RuntimeError("Running service differs from pinned artifact status")
    config_path = args.repo_root / "config/product.json"
    config = load_urban_config(config_path)
    inputs = prepare_walk_inputs(
        accepted,
        city_id="kaliningrad",
        departure_at=datetime.fromisoformat(args.departure_at),
        methodology_version=config.methodology_version,
        config_hash=sha(config_path),
        search_parameters_id="real-e2e-reproduction",
        build=build_model,
        connectors=connectors,
        correlations=correlations,
    )
    plan = plan_walk_routes(inputs, point_ids=artifact_data["point_ids"], categories=["school"])
    envelopes = []
    for request in plan.requests:
        actual, locate_hash = locate(args, request.payload()["locations"], save)
        matched = []
        for row, correlation in zip(actual, (request.origin, request.destination), strict=True):
            projections = {
                (edge["correlated_lon"], edge["correlated_lat"]) for edge in row.get("edges") or []
            }
            if projections != {
                (correlation.correlated_location.lon, correlation.correlated_location.lat)
            }:
                raise RuntimeError("Actual endpoint no longer matches accepted projection")
            matched.append(correlation.correlated_location)
        body, response_hash = post(args, "/route", request.payload(), save)
        trip = body.get("trip", {})
        maneuvers = [m for leg in trip.get("legs", []) for m in leg.get("maneuvers", [])]
        if not (
            maneuvers
            and trip.get("summary", {}).get("has_ferry") is False
            and all(
                maneuver.get("travel_mode") == "pedestrian"
                and maneuver.get("travel_type") == "foot"
                and not maneuver.get("ferry", False)
                and maneuver.get("type") not in range(28, 37)
                for maneuver in maneuvers
            )
        ):
            raise RuntimeError("Actual response does not attest a pure foot path")
        # These hash IDs identify observations just inspected; read-only verification
        # reports them to stdout and never publishes production evidence records.
        envelopes.append(
            WalkResponseEnvelope(
                request.request_id,
                request.point_id,
                request.destination_id,
                build_model,
                request.payload_hash,
                request.origin.correlation_id,
                request.destination.correlation_id,
                "response",
                json.dumps(body),
                ("verified-response-" + response_hash,),
                pure_walk_evidence_refs=("verified-response-" + response_hash,),
                matched_origin=matched[0],
                matched_destination=matched[1],
                endpoint_evidence_refs=("verified-locate-" + locate_hash,),
            )
        )
    result = analyze_walk_responses(
        plan,
        envelopes,
        selected_categories={"school": "high"},
        config=config,
        rules=load_urban_journey_rules(config_path),
    )
    if not result.routes or not all(
        route.state == "reachable" and not route.minimum_proven for route in result.routes
    ):
        raise RuntimeError("Actual route failed normalization; do not publish runtime")
    report = {
        "requests": len(plan.requests),
        "blocked_pairs": len(plan.issues),
        "network_seconds": [route.network_seconds for route in result.routes],
        "total_seconds": [journey.total_seconds for journey in result.analysis.journeys],
        "status": result.analysis.status,
        "minimum_proven": False,
        "ranking_eligible": result.analysis.modes[0].ranking_eligible,
    }
    print(json.dumps(report))
    return report


def verify(args):
    verify_files(args)
    _, accepted = source_models(args)
    payload = read(args.runtime / args.artifact)
    if payload["bundle_id"] != args.bundle_id or payload["build"]["snapshot_id"] != args.bundle_id:
        raise RuntimeError("Requested accepted bundle differs from artifact snapshot")
    if sha(args.runtime / "valhalla-build-manifest.json") not in payload["build"]["graph_version"]:
        raise RuntimeError("Build manifest differs from artifact graph identity")
    if payload["endpoint_url"] != args.endpoint:
        raise RuntimeError("Endpoint differs; do not silently reroute artifact")
    if (
        args.expected_artifact_sha256
        and sha(args.runtime / args.artifact) != args.expected_artifact_sha256
    ):
        raise RuntimeError("Artifact checksum mismatch")
    verify_route(args, payload, accepted)


def environment(args):
    path = args.runtime / args.artifact
    digest = sha(path)
    folder = args.runtime / "observations"
    if not folder.is_dir():
        raise RuntimeError("Create the writable observations directory first")
    for name, value in dict(
        A2_WALK_PILOT_ARTIFACT_PATH=str(path),
        A2_WALK_PILOT_ARTIFACT_SHA256=digest,
        A2_WALK_PILOT_EVIDENCE_DIR=str(folder),
        A2_CODE_VERSION="walk-pilot-" + args.code_version + "-" + digest,
    ).items():
        print("export " + name + "=" + shlex.quote(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", type=Path, required=True, help="Isolated directory outside the repository"
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--bundle-id", choices=SUPPORTED_BUNDLES, default=BUNDLE)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8007")
    parser.add_argument("--name", default="waydex-e2e-valhalla")
    parser.add_argument("--port", type=int, default=8007)
    parser.add_argument(
        "--artifact", default="walk-pilot-artifact.json", help="Artifact filename inside runtime"
    )
    parser.add_argument("--expected-artifact-sha256")
    parser.add_argument("--departure-at", default="2026-11-10T09:00:00+00:00")
    parser.add_argument("--code-version", default="local")
    parser.add_argument("--point-id", help="Validate this exact accepted point instead of scanning")
    parser.add_argument("--destination-id", help="Validate this exact accepted school")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "build", "serve", "select", "artifact", "verify", "env"):
        commands.add_parser(
            name,
            help={
                "fetch": "Download and verify fixed source files",
                "build": "Create graph once in a fresh runtime",
                "serve": "Start an isolated read-only graph service",
                "select": "Observe and record compatible point/school candidates",
                "artifact": "Validate a real route and publish its runtime contract",
                "verify": "Read-only checksum, status, locate and route verification",
                "env": "Print artifact-pinned API/worker exports",
            }[name],
        )
    args = parser.parse_args()
    args.runtime = args.runtime.expanduser().resolve()
    args.repo_root = args.repo_root.expanduser().resolve()
    args.endpoint = args.endpoint.rstrip("/")
    if bool(args.point_id) != bool(args.destination_id):
        parser.error("Explicit selection requires both --point-id and --destination-id")
    if args.runtime.is_relative_to(args.repo_root) or Path(args.artifact).name != args.artifact:
        parser.error("Runtime must be outside repository; artifact must be a plain filename")
    globals()[{"env": "environment"}.get(args.command, args.command)](args)


if __name__ == "__main__":
    main()
