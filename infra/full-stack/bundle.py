#!/usr/bin/env python3
"""Pack explicit runtime files and restore them without changing their bytes."""

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

MANIFEST = "bundle-manifest.json"
READY = ".bundle-ready.json"
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_FILES = 4096


def checksum(path, limit=MAX_TOTAL_BYTES):
    if path.stat().st_size > limit:
        raise ValueError("File exceeds bundle byte budget")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name):
    if not isinstance(name, str) or len(name) > 512 or "\\" in name:
        raise ValueError("Invalid bundle path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != name or name == ".":
        raise ValueError("Bundle path must be a clean relative path")
    if name in (MANIFEST, READY) or any(
        part in (".git", ".ssh", ".aws") or part.startswith(".env") for part in path.parts
    ):
        raise ValueError("Reserved or private file cannot enter a runtime bundle")
    return path


def inventory(value, inputs=False):
    schema = "waydex-runtime-bundle-inputs/1" if inputs else "waydex-runtime-bundle/1"
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "files"}
        or value["schema_version"] != schema
        or not isinstance(value["files"], list)
        or not 1 <= len(value["files"]) <= MAX_FILES
    ):
        raise ValueError("Invalid runtime bundle inventory")
    names = set()
    total = 0
    for row in value["files"]:
        fields = {"path", "sha256", "bytes", "executable"} | ({"source"} if inputs else set())
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Invalid runtime bundle file record")
        safe_name(row["path"])
        if row["path"] in names:
            raise ValueError("Duplicate bundle path")
        names.add(row["path"])
        if (
            not isinstance(row["sha256"], str)
            or re.fullmatch("[a-f0-9]{64}", row["sha256"]) is None
            or type(row["bytes"]) is not int
            or not 0 <= row["bytes"] <= MAX_FILE_BYTES
            or type(row["executable"]) is not bool
        ):
            raise ValueError("Invalid bundle file checksum, size or mode")
        total += row["bytes"]
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Runtime bundle exceeds total byte budget")
    return total


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def member(name, size, executable=False):
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o555 if executable else 0o444
    info.mtime = 0
    return info


class CheckedReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        chunk = self.source.read(size)
        self.count += len(chunk)
        self.digest.update(chunk)
        return chunk


def pack(specification, output):
    if specification.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("Bundle specification exceeds metadata budget")
    spec = json.loads(specification.read_text())
    total = inventory(spec, inputs=True)
    rows = sorted(spec["files"], key=lambda row: row["path"])
    sources = {}
    for row in rows:
        path = Path(row["source"])
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or any(
                part in (".git", ".ssh", ".aws") or part.startswith(".env") for part in path.parts
            )
            or path.stat().st_size != row["bytes"]
            or checksum(path) != row["sha256"]
        ):
            raise ValueError("Source is private, missing or differs from its approved file record")
        sources[row["path"]] = path
    published = {
        "schema_version": "waydex-runtime-bundle/1",
        "files": [{key: value for key, value in row.items() if key != "source"} for row in rows],
    }
    body = encoded(published)
    with (
        output.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=1) as stream,
        tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        archive.addfile(member(MANIFEST, len(body)), io.BytesIO(body))
        for row in rows:
            path = sources[row["path"]]
            with path.open("rb") as source:
                checked = CheckedReader(source)
                archive.addfile(member(row["path"], row["bytes"], row["executable"]), checked)
                if (
                    checked.count != row["bytes"]
                    or checked.digest.hexdigest() != row["sha256"]
                    or path.stat().st_size != row["bytes"]
                ):
                    raise ValueError("Runtime source changed while packing")
    output.chmod(0o444)
    return {
        "archive_sha256": checksum(output),
        "archive_bytes": output.stat().st_size,
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "files": len(rows),
        "uncompressed_bytes": total,
    }


def restore(archive_path, expected_archive, expected_manifest, output):
    if output.exists() or output.is_symlink():
        raise ValueError("Restore destination already exists")
    if checksum(archive_path) != expected_archive:
        raise ValueError("Runtime archive SHA256 mismatch")
    with tarfile.open(archive_path, "r|gz") as archive:
        first = archive.next()
        if (
            not first
            or first.name != MANIFEST
            or not first.isfile()
            or first.size > MAX_METADATA_BYTES
        ):
            raise ValueError("Runtime archive must start with its bounded file manifest")
        body = archive.extractfile(first).read()
        if hashlib.sha256(body).hexdigest() != expected_manifest:
            raise ValueError("Runtime file manifest SHA256 mismatch")
        value = json.loads(body)
        total = inventory(value)
        rows = {row["path"]: row for row in value["files"]}
        output.mkdir(parents=True, exist_ok=False)
        output = output.resolve()
        seen = set()
        for item in iter(archive.next, None):
            safe_name(item.name)
            if not item.isfile() or item.name not in rows or item.name in seen:
                raise ValueError("Unknown, duplicate or linked runtime archive member")
            row = rows[item.name]
            if item.size != row["bytes"]:
                raise ValueError("Runtime archive member size mismatch")
            target = output.joinpath(*PurePosixPath(item.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.resolve().is_relative_to(output):
                raise ValueError("Runtime archive member escapes destination")
            with archive.extractfile(item) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
            if checksum(target) != row["sha256"]:
                raise ValueError("Restored runtime file SHA256 mismatch")
            target.chmod(0o555 if row["executable"] else 0o444)
            seen.add(item.name)
        if seen != rows.keys():
            raise ValueError("Runtime archive is missing declared files")
    with (output / MANIFEST).open("xb") as target:
        target.write(body)
    ready = {"archive_sha256": expected_archive, "manifest_sha256": expected_manifest}
    with (output / READY).open("xb") as target:
        target.write(encoded(ready))
    (output / MANIFEST).chmod(0o444)
    (output / READY).chmod(0o444)
    return dict(ready, files=len(rows), uncompressed_bytes=total)


def verify(folder, expected_manifest):
    body = (folder / MANIFEST).read_bytes()
    if len(body) > MAX_METADATA_BYTES or hashlib.sha256(body).hexdigest() != expected_manifest:
        raise ValueError("Mounted runtime bundle manifest SHA256 mismatch")
    value = json.loads(body)
    inventory(value)
    for row in value["files"]:
        path = folder.joinpath(*PurePosixPath(row["path"]).parts)
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(folder.resolve())
            or path.stat().st_size != row["bytes"]
            or checksum(path) != row["sha256"]
        ):
            raise ValueError("Mounted runtime bundle file differs from its manifest")
    ready = json.loads((folder / READY).read_text())
    if ready["manifest_sha256"] != expected_manifest:
        raise ValueError("Runtime bundle was not completely restored")
    return len(value["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packing = commands.add_parser("pack")
    packing.add_argument("--spec", type=Path, required=True)
    packing.add_argument("--output", type=Path, required=True)
    restoration = commands.add_parser("restore")
    restoration.add_argument("--archive", type=Path, required=True)
    restoration.add_argument("--sha256", required=True)
    restoration.add_argument("--manifest-sha256", required=True)
    restoration.add_argument("--output", type=Path, required=True)
    checking = commands.add_parser("verify")
    checking.add_argument("--directory", type=Path, required=True)
    checking.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if args.command == "pack":
        result = pack(args.spec, args.output)
    elif args.command == "restore":
        result = restore(args.archive, args.sha256, args.manifest_sha256, args.output)
    else:
        result = {"verified_files": verify(args.directory, args.manifest_sha256)}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
