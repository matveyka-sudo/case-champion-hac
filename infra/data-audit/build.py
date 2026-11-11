"""Build an isolated native-linkage repair; never change a production image/tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

BASE_TAG = "waydex-full-api:e5e067e1b4d2a9021ddfa1d91bf918957a94a203"
BASE_ID = "sha256:a6ca2a64d1c33e359810a373ab0b2f672e5cba87b6cf3e0e72aed21597c6f61a"
PACKAGE = "libexpat1_2.5.0-1+deb12u5_arm64.deb"
PACKAGE_URL = "https://security.debian.org/debian-security/pool/updates/main/e/expat/" + PACKAGE
PACKAGE_SHA256 = "4a733e7f636de4a4483e3e5d7b02dc8e788b115361a04dd58df89461ce2ae65a"
PACKAGE_BYTES = 91748
HERE = Path(__file__).resolve().parent


def inspect_image(reference: str) -> dict:
    return json.loads(subprocess.check_output(["docker", "image", "inspect", reference]))[0]


def check_base(image: dict) -> None:
    if (image["Id"], image["Os"], image["Architecture"]) != (BASE_ID, "linux", "arm64"):
        raise ValueError("The frozen local e5 arm64 base image does not match; refusing build")


def check_package(payload: bytes) -> None:
    if len(payload) != PACKAGE_BYTES or hashlib.sha256(payload).hexdigest() != PACKAGE_SHA256:
        raise ValueError("Debian libexpat1 size/SHA256 mismatch; refusing build")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private receipt/build directory")
    parser.add_argument("--tag", default="waydex-data-audit:expat-e5-arm64-v1")
    args = parser.parse_args()
    if not args.tag.startswith("waydex-data-audit:"):
        raise ValueError("Only the isolated waydex-data-audit tag namespace is allowed")
    before = inspect_image(BASE_TAG)
    check_base(before)
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    context = args.output / "context"
    context.mkdir(mode=0o700)
    request = urllib.request.Request(PACKAGE_URL, headers={"User-Agent": "waydex-audit-native-build/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(PACKAGE_BYTES + 1)
    check_package(payload)
    (context / PACKAGE).write_bytes(payload)
    dockerfile = HERE / "audit.Dockerfile"
    shutil.copyfile(dockerfile, context / "Dockerfile")
    command = ["docker", "build", "--pull=false", "--network=none", "--platform=linux/arm64",
               "--tag", args.tag, str(context)]
    started = time.monotonic()
    with (args.output / "build.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    after = inspect_image(BASE_TAG)
    check_base(after)
    result = inspect_image(args.tag)
    layers = before["RootFS"]["Layers"]
    if result["RootFS"]["Layers"][:len(layers)] != layers:
        raise ValueError("Audit image does not preserve the frozen base layers")
    if result["Config"]["User"] != "10001:10001":
        raise ValueError("Audit image must retain the unprivileged runtime user")
    receipt = {
        "scope": "isolated audit image; no production tag/container/service edits",
        "base_image_id": BASE_ID,
        "base_tag": BASE_TAG,
        "base_tag_before_after_equal": before["Id"] == after["Id"],
        "base_layers_preserved": True,
        "image_id": result["Id"],
        "image_tag": args.tag,
        "platform": "linux/arm64",
        "native_package": {"name": PACKAGE, "url": PACKAGE_URL,
                           "sha256": PACKAGE_SHA256, "bytes": PACKAGE_BYTES,
                           "official_pin_source": "https://packages.debian.org/bookworm/arm64/libexpat1/download"},
        "dockerfile_sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
        "command": command,
        "elapsed_seconds": time.monotonic() - started,
        "python_package_install_commands": 0,
        "original_pbf_scans": 0,
    }
    (args.output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"image_id": result["Id"], "receipt": str(args.output / "build-receipt.json")}))


if __name__ == "__main__":
    main()
