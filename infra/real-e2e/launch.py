#!/usr/bin/env python3
"""Launch one API or worker with the same hash-pinned walk runtime configuration."""

import argparse
import hashlib
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["api", "worker"])
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    artifact = args.artifact.resolve()
    evidence = args.evidence_dir.resolve()
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != args.sha256:
        parser.error("Artifact SHA256 mismatch")
    if not evidence.is_dir() or not os.access(evidence, os.W_OK):
        parser.error("A writable observations directory is required")
    env = dict(os.environ)
    env.update(
        A2_WALK_PILOT_ARTIFACT_PATH=str(artifact),
        A2_WALK_PILOT_ARTIFACT_SHA256=args.sha256,
        A2_WALK_PILOT_EVIDENCE_DIR=str(evidence),
        A2_CODE_VERSION="walk-pilot-" + args.code_version + "-" + args.sha256,
        A2_REGISTRY_HOOK="a2_api.adapters.walk_pilot:registry",
        A2_PREFLIGHT_HOOK="a2_api.adapters.walk_pilot:preflight",
        A2_CALCULATION_HANDLER="a2_api.adapters.walk_pilot:calculate",
        A2_CONTRACT_VERSION="2",
        A2_DATA_DIR=str(repo / "data"),
    )
    python = repo / ".venv/bin/python"
    command = (
        [
            str(python),
            "-m",
            "uvicorn",
            "a2_api.api.bootstrap:build_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ]
        if args.command == "api"
        else [str(python), "-m", "a2_api.workers.cli", "worker"]
    )
    os.chdir(repo)
    os.execve(python, command, env)


if __name__ == "__main__":
    main()
