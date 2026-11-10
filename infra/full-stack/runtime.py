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
from pathlib import Path
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


def graph_check(graph, parent):
    graph = Path(graph).resolve(strict=True)
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
    commands.add_parser("worker-health")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "graph-check":
        configured()
        print("Pinned source graph verified")
    elif args.command == "verify":
        verify(args)
    else:
        asyncio.run(worker_health())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        sys.exit(str(exc))
