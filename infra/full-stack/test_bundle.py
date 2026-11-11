"""Runtime restoration rejects incomplete, altered or escaping archive inputs."""

import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "runtime_bundle", Path(__file__).with_name("bundle.py")
)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class RuntimeBundle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.bin"
        self.source.write_bytes(b"TEST_ONLY immutable byte-restoration fixture")
        self.row = {
            "path": "native/source-bound-walk",
            "source": str(self.source.resolve()),
            "bytes": self.source.stat().st_size,
            "sha256": bundle.checksum(self.source),
            "executable": True,
        }
        self.specification = self.root / "inputs.json"
        self.write_specification()

    def tearDown(self):
        self.temporary.cleanup()

    def write_specification(self):
        self.specification.write_text(
            json.dumps({"schema_version": "waydex-runtime-bundle-inputs/1", "files": [self.row]})
        )

    def custom_archive(self, rows, members):
        path = self.root / "custom.tar.gz"
        body = bundle.encoded({"schema_version": "waydex-runtime-bundle/1", "files": rows})
        with (
            path.open("xb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            archive.addfile(bundle.member(bundle.MANIFEST, len(body)), io.BytesIO(body))
            for member, contents in members:
                archive.addfile(member, io.BytesIO(contents) if contents is not None else None)
        return path, bundle.checksum(path), hashlib.sha256(body).hexdigest()

    def published_row(self):
        return {key: value for key, value in self.row.items() if key != "source"}

    def test_deterministic_pack_restores_bytes_and_executable_mode(self):
        first, second = self.root / "first.tar.gz", self.root / "second.tar.gz"
        result = bundle.pack(self.specification, first)
        self.assertEqual(result, bundle.pack(self.specification, second))
        destination = self.root / "restored"
        bundle.restore(first, result["archive_sha256"], result["manifest_sha256"], destination)
        self.assertEqual(bundle.verify(destination, result["manifest_sha256"]), 1)
        restored = destination / self.row["path"]
        self.assertEqual(restored.read_bytes(), self.source.read_bytes())
        self.assertEqual(restored.stat().st_mode & 0o777, 0o555)
        self.assertNotIn(str(self.source), (destination / bundle.MANIFEST).read_text())

    def test_pack_refuses_existing_archive(self):
        archive = self.root / "existing.tar.gz"
        archive.write_bytes(b"preserve existing bytes")
        with self.assertRaises(FileExistsError):
            bundle.pack(self.specification, archive)
        self.assertEqual(archive.read_bytes(), b"preserve existing bytes")

    def test_restore_refuses_wrong_archive_hash_before_creating_destination(self):
        archive = self.root / "archive.tar.gz"
        result = bundle.pack(self.specification, archive)
        destination = self.root / "must-not-exist"
        with self.assertRaisesRegex(ValueError, "archive SHA256"):
            bundle.restore(archive, "0" * 64, result["manifest_sha256"], destination)
        self.assertFalse(destination.exists())

    def test_restore_refuses_existing_destination(self):
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            bundle.restore(self.source, "0" * 64, "0" * 64, destination)

    def test_wrong_manifest_hash_does_not_create_destination(self):
        archive = self.root / "archive.tar.gz"
        result = bundle.pack(self.specification, archive)
        destination = self.root / "must-not-exist"
        with self.assertRaisesRegex(ValueError, "manifest SHA256"):
            bundle.restore(archive, result["archive_sha256"], "0" * 64, destination)
        self.assertFalse(destination.exists())

    def test_manifest_path_escape_is_rejected(self):
        row = dict(self.published_row(), path="../outside")
        archive, digest, manifest = self.custom_archive([row], [])
        with self.assertRaisesRegex(ValueError, "relative path"):
            bundle.restore(archive, digest, manifest, self.root / "restored")
        self.assertFalse((self.root / "outside").exists())

    def test_symlink_archive_member_is_rejected(self):
        link = tarfile.TarInfo(self.row["path"])
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive, digest, manifest = self.custom_archive([self.published_row()], [(link, None)])
        destination = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "linked"):
            bundle.restore(archive, digest, manifest, destination)
        self.assertFalse((destination / bundle.READY).exists())

    def test_duplicate_archive_member_is_rejected(self):
        info = bundle.member(self.row["path"], self.row["bytes"])
        data = self.source.read_bytes()
        archive, digest, manifest = self.custom_archive(
            [self.published_row()], [(info, data), (info, data)]
        )
        destination = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            bundle.restore(archive, digest, manifest, destination)
        self.assertFalse((destination / bundle.READY).exists())

    def test_missing_declared_file_is_rejected(self):
        archive, digest, manifest = self.custom_archive([self.published_row()], [])
        destination = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "missing"):
            bundle.restore(archive, digest, manifest, destination)
        self.assertFalse((destination / bundle.READY).exists())

    def test_unlisted_archive_member_is_not_written(self):
        data = self.source.read_bytes()
        expected = bundle.member(self.row["path"], len(data))
        extra = bundle.member("not-approved.txt", 1)
        archive, digest, manifest = self.custom_archive(
            [self.published_row()], [(expected, data), (extra, b"x")]
        )
        destination = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "Unknown"):
            bundle.restore(archive, digest, manifest, destination)
        self.assertFalse((destination / "not-approved.txt").exists())
        self.assertFalse((destination / bundle.READY).exists())

    def test_oversized_declared_file_is_rejected(self):
        row = dict(self.published_row(), bytes=bundle.MAX_FILE_BYTES + 1)
        archive, digest, manifest = self.custom_archive([row], [])
        with self.assertRaisesRegex(ValueError, "size"):
            bundle.restore(archive, digest, manifest, self.root / "restored")

    def test_changed_mounted_file_fails_verification(self):
        archive = self.root / "archive.tar.gz"
        result = bundle.pack(self.specification, archive)
        destination = self.root / "restored"
        bundle.restore(archive, result["archive_sha256"], result["manifest_sha256"], destination)
        changed = destination / self.row["path"]
        changed.chmod(0o644)
        changed.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "differs"):
            bundle.verify(destination, result["manifest_sha256"])

    def test_private_environment_source_is_rejected(self):
        private = self.root / ".env.private"
        private.write_bytes(self.source.read_bytes())
        self.row["source"] = str(private.resolve())
        self.write_specification()
        with self.assertRaisesRegex(ValueError, "private"):
            bundle.pack(self.specification, self.root / "must-not-be-published.tar.gz")


if __name__ == "__main__":
    unittest.main()
