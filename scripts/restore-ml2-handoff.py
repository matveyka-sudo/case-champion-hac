"""Restore the pinned external ML2 ZIP into ignored data/processed storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_NAME = "ml2-handoff-2026-11-10.zip"
ARCHIVE_SHA256 = "e975a069c8b5db6949694e610008cbf05b48cc493cf8436fdf56820c96e6bacb"
PREFIX = "ml2-handoff-2026-11-10"
MAX_BYTES = 64 * 1024 * 1024
REPOSITORY = Path(__file__).resolve().parents[1]


def unpack_verified(archive: Path, destination: Path) -> Path:
    """Validate before extracting; no archive path, link or ZIP bomb is trusted."""
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ARCHIVE_SHA256:
            raise ValueError("ML2_HANDOFF_ARCHIVE_SHA256_MISMATCH")
        stream.seek(0)
        with zipfile.ZipFile(stream) as source:
            entries = source.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 32 or len(set(names)) != len(names):
                raise ValueError("ML2_HANDOFF_INVALID_ARCHIVE_INVENTORY")
            if sum(entry.file_size for entry in entries) > MAX_BYTES:
                raise ValueError("ML2_HANDOFF_UNCOMPRESSED_SIZE_EXCEEDED")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or not path.parts
                    or path.parts[0] != PREFIX
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG))
                ):
                    raise ValueError("ML2_HANDOFF_UNSAFE_ARCHIVE_ENTRY")
            for entry in entries:
                target = destination.joinpath(*PurePosixPath(entry.filename).parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with source.open(entry) as incoming, target.open("xb") as outgoing:
                        target.chmod(0o600)
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    handoff = destination / PREFIX
    for line in (handoff / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("ML2_HANDOFF_UNSAFE_CHECKSUM_PATH")
        with handoff.joinpath(*relative.parts).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("ML2_HANDOFF_MEMBER_SHA256_MISMATCH")
    return handoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=REPOSITORY / ARCHIVE_NAME)
    parser.add_argument("--data-root", type=Path, default=REPOSITORY / "data")
    args = parser.parse_args()
    # The installed workspace supplies this existing atomic/hash-checked loader.
    from a2_data_pipeline.runtime_inputs import restore_handoff

    processed = args.data_root.resolve() / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ml2-handoff-", dir=processed) as staging:
        handoff = unpack_verified(args.archive.resolve(), Path(staging))
        report = restore_handoff(handoff, root=args.data_root.resolve())
    print(
        json.dumps(
            {
                "archive": str(args.archive.resolve()),
                "sha256": ARCHIVE_SHA256,
                "data_root": str(args.data_root.resolve()),
                "artifacts": report,
            },
            indent=2,
        )
    )
    if any(row["state"] != "verified" for row in report):
        raise SystemExit("ML2_HANDOFF_RESTORE_INCOMPLETE")


if __name__ == "__main__":
    main()
