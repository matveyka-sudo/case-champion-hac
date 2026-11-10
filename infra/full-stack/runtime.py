#!/usr/bin/env python3
"""Bind an immutable walk artifact to an isolated deployment; verify before use."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "waydex_real_runtime", ROOT / "infra/real-e2e/runtime.py"
)
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)
ENDPOINT = "http://valhalla:8002"


def load(path, expected):
    content = Path(path).read_bytes()
    if len(expected) != 64 or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Artifact SHA256 mismatch")
    return json.loads(content)


def directory_sources(graph, configuration):
    """GraphReader prefers an archive and may fetch missing tiles remotely."""
    reader = configuration.get("mjolnir", {})
    if (
        not isinstance(reader, dict)
        or reader.get("tile_dir") != "/data/tiles"
        or reader.get("tile_url") not in (None, "")
    ):
        raise ValueError("Directory graph requires /data/tiles and no remote tile URL")
    extract = reader.get("tile_extract")
    if extract is not None and extract != "":
        if not isinstance(extract, str):
            raise ValueError("Configured tile archive must be a path or absent")
        try:
            relative = PurePosixPath(extract).relative_to("/data")
        except ValueError as exc:
            raise ValueError("Configured tile archive is outside the verified graph") from exc
        if ".." in relative.parts:
            raise ValueError("Configured tile archive is outside the verified graph")
        archive = graph.joinpath(*relative.parts)
        if archive.exists() or archive.is_symlink():
            raise ValueError("Unverified tile archive would override the checked directory graph")


def graph_check(graph, parent):
    graph = Path(graph).resolve(strict=True)
    binding_path = os.environ.get("WAYDEX_GRAPH_BINDING_PATH", "")
    binding_sha = os.environ.get("WAYDEX_GRAPH_BINDING_SHA256", "")
    if binding_path or binding_sha:
        if not binding_path or not binding_sha:
            raise ValueError("Both operator graph binding path and SHA256 are required")
        binding_spec = importlib.util.spec_from_file_location(
            "waydex_graph_binding", Path(__file__).with_name("graph_binding.py")
        )
        binding = importlib.util.module_from_spec(binding_spec)
        binding_spec.loader.exec_module(binding)
        return binding.verify(
            graph, parent, binding_path, binding_sha, real.IMAGE, real.CONFIG_SHA, directory_sources
        )
    directory_sources(graph, real.read(graph / "valhalla.json"))
    manifest_path = graph / "valhalla-build-manifest.json"
    digest = real.sha(manifest_path)
    if parent["build"]["graph_version"] != "valhalla-351-tiles-" + digest:
        raise ValueError("Artifact is not bound to this graph manifest")
    manifest = real.verify_files(SimpleNamespace(runtime=graph))
    if (
        manifest["source_sha256"] != real.PBF_SHA
        or real.IMAGE not in manifest["image_repo_digests"]
    ):
        raise ValueError("Graph source or image differs from pinned build")
    if real.sha(graph / "valhalla-build-config-3.5.1.py") != real.CONFIG_SHA:
        raise ValueError("Graph configuration generator differs")
    if (graph / "valhalla-version.txt").read_text().strip() != "valhalla_build_tiles 3.5.1":
        raise ValueError("Graph binary version differs")
    declared = [row["path"] for row in manifest["files"]]
    actual = sorted(
        str(path.relative_to(graph)) for path in (graph / "tiles").rglob("*") if path.is_file()
    )
    if not declared or len(set(declared)) != len(declared) or sorted(declared) != actual:
        raise ValueError("Graph file inventory differs from manifest")
    if any((graph / name).is_symlink() for name in declared):
        raise ValueError("Graph tiles must be regular immutable files")
    return digest


def prepare(args):
    parent = load(args.parent, args.parent_sha256)
    digest = graph_check(args.graph, parent)
    output = args.output.resolve()
    if output.is_relative_to(args.graph.resolve()):
        raise ValueError("Deployment artifact must be outside the source graph")
    payload = dict(parent)
    payload["endpoint_url"] = ENDPOINT
    payload["deployment_binding"] = {
        "parent_artifact_sha256": args.parent_sha256,
        "parent_endpoint_url": parent["endpoint_url"],
        "endpoint_url": ENDPOINT,
        "graph_manifest_sha256": digest,
        "meaning": "Endpoint binding only; preserves historical evidence and adds no routing proof.",
    }
    real.write(output, payload)
    print(real.sha(output))


def configured():
    parent = load(os.environ["WAYDEX_PARENT_ARTIFACT_PATH"], os.environ["WAYDEX_PARENT_SHA256"])
    graph = Path(os.environ.get("WAYDEX_GRAPH_PATH", "/data"))
    digest = graph_check(graph, parent)
    return parent, graph, digest


def verify(args):
    parent, graph, digest = configured()
    artifact_path = Path(os.environ["A2_WALK_PILOT_ARTIFACT_PATH"])
    payload = load(artifact_path, os.environ["A2_WALK_PILOT_ARTIFACT_SHA256"])
    binding = payload.pop("deployment_binding")
    expected = dict(parent, endpoint_url=ENDPOINT)
    if payload != expected or binding != {
        "parent_artifact_sha256": os.environ["WAYDEX_PARENT_SHA256"],
        "parent_endpoint_url": parent["endpoint_url"],
        "endpoint_url": ENDPOINT,
        "graph_manifest_sha256": digest,
        "meaning": "Endpoint binding only; preserves historical evidence and adds no routing proof.",
    }:
        raise ValueError("Deployment artifact differs beyond its declared endpoint binding")
    deadline = time.monotonic() + 90
    while True:
        try:
            with urlopen(
                Request(
                    ENDPOINT + "/status", data=b"{}", headers={"Content-Type": "application/json"}
                ),
                timeout=3,
            ) as response:
                if response.status == 200:
                    break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Pinned Valhalla did not become ready") from None
            time.sleep(1)
    runtime_args = SimpleNamespace(
        runtime=graph,
        repo_root=ROOT,
        bundle_id=parent["bundle_id"],
        endpoint=ENDPOINT,
        departure_at="2026-11-10T09:00:00+00:00",
    )
    _, accepted = real.source_models(runtime_args)
    real.verify_route(runtime_args, payload, accepted)
    # Exercise the exact backend loader used by both API and spawned worker.
    from a2_api.adapters.walk_pilot import load_runtime

    load_runtime()
    print("Source graph, deployment binding, accepted data and real walk route verified")


def product_check(args):
    from a2_api.adapters.product_runtime import load_runtime, registry
    from a2_api.core.config import RuntimeSettings

    runtime = load_runtime()
    if not runtime.profiles or len(runtime.profiles) != len(runtime.manifest.profiles):
        raise RuntimeError("Every configured product profile must have valid accepted inputs")
    if runtime.digest not in (RuntimeSettings().code_version or ""):
        raise RuntimeError("Product manifest digest is missing from the code fingerprint")
    executors = {profile.executor for profile, _ in runtime.profiles.values()}
    if executors - {"accepted_inputs", "walk_pilot", "certified_walk_model"}:
        raise RuntimeError("This deployment has no approved checker for the configured executor")
    if "walk_pilot" in executors:
        verify(args)
    if "certified_walk_model" in executors:
        from a2_api.adapters.certified_walk_model import load_binding

        if os.environ["A2_CERTIFIED_WALK_MODEL_SHA256"] not in (
            RuntimeSettings().code_version or ""
        ):
            raise RuntimeError("Certified proof digest is missing from the code fingerprint")
        bundle_spec = importlib.util.spec_from_file_location(
            "waydex_runtime_bundle", Path(__file__).with_name("bundle.py")
        )
        bundle = importlib.util.module_from_spec(bundle_spec)
        bundle_spec.loader.exec_module(bundle)
        bundle.verify(
            Path(os.environ["WAYDEX_RUNTIME_BUNDLE_PATH"]),
            os.environ["WAYDEX_RUNTIME_BUNDLE_SHA256"],
        )
        geometry = bundle.verify_geometry(
            Path(os.environ["WAYDEX_RUNTIME_BUNDLE_PATH"]),
            Path(os.environ["A2_CERTIFIED_WALK_MODEL_PATH"]),
            os.environ["A2_CERTIFIED_WALK_MODEL_SHA256"],
        )
        if geometry["configured"]:
            print(json.dumps({"verified_geometry_packet": geometry}))
        proof = load_binding(registry().config_hash)
        graph = Path(os.environ["WAYDEX_GRAPH_PATH"])
        digest = graph_check(graph, real.read(proof.native_root / "artifact.json"))
        if (
            digest != proof.native["files_sha256"]["build-manifest.json"]
            or real.sha(graph / "valhalla.json") != proof.native["files_sha256"]["valhalla.json"]
            or real.sha(graph / "walking-profile.json")
            != proof.native["files_sha256"]["walking-profile.json"]
        ):
            raise ValueError("Mounted directory graph differs from the certified native proof")
        print("Restored native proof files and mounted graph verified; no HTTP actor requested")
    registry()
    print(
        json.dumps(
            {
                "product_manifest_sha256": runtime.digest,
                "verified_profiles": sorted(runtime.profiles),
                "analysis_kinds": sorted(
                    {profile.analysis_kind for profile, _ in runtime.profiles.values()}
                ),
            }
        )
    )


async def worker_health():
    import asyncpg
    from a2_api.core.config import RuntimeSettings

    processes = [path.read_bytes() for path in Path("/proc").glob("[0-9]*/cmdline")]
    if not any(b"a2_api.workers.cli\x00worker\x00" in command for command in processes):
        raise RuntimeError("Worker process is not running")
    settings = RuntimeSettings()
    connection = await asyncpg.connect(
        settings.database_url, server_settings={"search_path": settings.database_schema}, timeout=3
    )
    try:
        if not await connection.fetchval("SELECT to_regclass('jobs') IS NOT NULL"):
            raise RuntimeError("Worker database migrations are unavailable")
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--graph", type=Path, required=True)
    preparation.add_argument("--parent", type=Path, required=True)
    preparation.add_argument("--parent-sha256", required=True)
    preparation.add_argument("--output", type=Path, required=True)
    commands.add_parser("graph-check")
    commands.add_parser("verify")
    commands.add_parser("product-check")
    commands.add_parser("worker-health")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "graph-check":
        configured()
        print("Pinned source graph verified")
    elif args.command == "verify":
        verify(args)
    elif args.command == "product-check":
        product_check(args)
    else:
        asyncio.run(worker_health())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        sys.exit(str(exc))
