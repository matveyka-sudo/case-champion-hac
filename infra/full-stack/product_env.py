#!/usr/bin/env python3
"""Create a private deployment environment from a verified restored release."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "product_transfer", Path(__file__).with_name("four_profile_restore.py")
)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


def prepare(args):
    runtime = transfer.checked_path(args.runtime)
    graphs = transfer.checked_path(args.transfer)
    source = transfer.checked_path(args.source)
    output = transfer.checked_path(args.output)
    if output.exists() or any(output.is_relative_to(p) for p in (source, runtime, graphs)):
        raise ValueError("Environment destination must be new and outside source/data trees")
    if (
        not re.fullmatch(r"[a-f0-9]{40}", args.revision)
        or not re.fullmatch(r"[a-f0-9]{64}", args.executor_sha256)
        or not re.fullmatch(r"waydex-[a-z0-9-]{1,48}", args.project)
        or not 1024 <= args.port <= 65535
    ):
        raise ValueError("Explicit source, executor, project and port identities are required")
    actual_revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual_revision != args.revision:
        raise ValueError("Source checkout revision differs")
    subprocess.run(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--"], check=True)
    if subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "--others", "--exclude-standard"], text=True
    ).strip():
        raise ValueError("Source checkout has untracked files")
    release = transfer.load_release(args.release_pins, args.release_pins_sha256)
    sources = {group: graphs / group for group in transfer.ARGUMENTS}
    manifest = transfer.verify_sources(runtime, sources, release)
    if (graphs / transfer.MARKER).read_bytes() != transfer.encoded(manifest):
        raise ValueError("Graph transfer completion receipt differs")

    def digest(path):
        return transfer.read_file(path)["sha256"]

    values = {
        "COMPOSE_PROJECT_NAME": args.project,
        "WAYDEX_HTTP_PORT": str(args.port),
        "WAYDEX_REVISION": args.revision,
        "WAYDEX_SOURCE_DIR": str(source),
        "WAYDEX_DEPLOYMENT_DIR": str(runtime),
        "WAYDEX_PRODUCT_SHA256": release["product_sha256"],
        "WAYDEX_BUNDLE_MANIFEST_SHA256": release["bundle_sha256"],
        "WAYDEX_WALK_EXECUTOR_SHA256": args.executor_sha256,
        "WAYDEX_CERTIFIED_SHA256": digest(runtime / "kaliningrad/proof-binding.json"),
        "WAYDEX_GRAPH_DIR": str(sources["graphs/moscow"]),
        "WAYDEX_GRAPH_BINDING_DIR": str(sources["bindings/moscow"]),
        "WAYDEX_GRAPH_BINDING_SHA256": release["bindings"]["moscow"],
        "WAYDEX_WALK_ARTIFACT": "moscow/artifact.json",
        "WAYDEX_WALK_ARTIFACT_SHA256": digest(runtime / "moscow/artifact.json"),
        "WAYDEX_NOVOROSSIYSK_GRAPH_DIR": str(sources["graphs/novorossiysk"]),
        "WAYDEX_NOVOROSSIYSK_GRAPH_BINDING_DIR": str(sources["bindings/novorossiysk"]),
        "WAYDEX_NOVOROSSIYSK_GRAPH_BINDING_SHA256": release["bindings"]["novorossiysk"],
        "WAYDEX_NOVOROSSIYSK_PARENT_SHA256": digest(
            sources["bindings/novorossiysk"] / "parent-artifact.json"
        ),
        "WAYDEX_KALININGRAD_GRAPH_DIR": str(sources["graphs/kaliningrad"]),
        "WAYDEX_KALININGRAD_TRUCK_GRAPH_DIR": str(sources["graphs/kaliningrad"]),
        "WAYDEX_KALININGRAD_PARENT_SHA256": digest(runtime / "kaliningrad/native/artifact.json"),
        "POSTGRES_PASSWORD": secrets.token_hex(24),
    }
    if any(any(c in value for c in "\r\n$#'\"") for value in values.values()):
        raise ValueError("Environment paths must not contain interpolation or quoting characters")
    raw = "".join(f"{key}={value}\n" for key, value in values.items()).encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as destination:
        destination.write(raw)
        destination.flush()
        os.fsync(destination.fileno())
    return {
        "environment": str(output),
        "project": args.project,
        "port": args.port,
        "revision": args.revision,
        "product_sha256": release["product_sha256"],
        "environment_sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "transfer", "source", "output", "release-pins"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("revision", "executor-sha256", "release-pins-sha256", "project"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--port", type=int, default=5294)
    print(json.dumps(prepare(parser.parse_args())))


if __name__ == "__main__":
    main()
