"""TEST_ONLY byte-transport controls; these fixtures prove no route or index."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "geometry_bundle", Path(__file__).with_name("bundle.py")
)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class OptionalGeometryTransport(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.builder = "sha256:" + "0" * 64
        self.library = "a" * 64
        self.snapshot = self.write("accepted-data.json", {"label": "TEST_ONLY transport"})
        self.canonical = "b" * 64  # Semantic canonical verification is the backend's separate gate.
        self.scope = "c" * 64
        native_files = {}
        for name in (
            "build-manifest.json",
            "valhalla.json",
            "walking-profile.json",
            "turn-candidate.json",
        ):
            native_files[name] = self.write("native/" + name, ("TEST_ONLY " + name).encode())[
                "sha256"
            ]
        self.native = {
            "files_sha256": native_files,
            "closed_turn_certificate": {"source_scope_id": self.scope},
            "builder_image_id": self.builder,
            "runtime_library_sha256": self.library,
        }
        native_ref = self.write("native/receipt.json", self.native)
        physical_ref = self.write("physical/input.json", b"TEST_ONLY physical source")
        self.core = {
            "schema_version": "waydex-certified-walk-model/1",
            "snapshot_id": "TEST_ONLY",
            "snapshot_hash": self.canonical,
            "native_receipt": native_ref,
            "physical_access": physical_ref,
        }
        self.binding = self.write("proof-binding.json", self.core)
        self.geometry_paths = {
            "proof-binding.json": self.root / "proof-binding.json",
            "native-receipt.json": self.root / "native/receipt.json",
            "physical-receipt.json": self.root / "physical/input.json",
        }
        for name in native_files:
            self.geometry_paths[name] = self.root / "native" / name
        model_ref = self.write(
            "physical/model.json",
            {
                "native_receipt_sha256": native_ref["sha256"],
                "physical_receipt_sha256": physical_ref["sha256"],
            },
        )
        self.geometry_paths["model-receipt.json"] = self.root / model_ref["path"]
        for name, source in self.geometry_paths.items():
            self.write("geometry/" + name, source.read_bytes())
        binary = self.write("geometry/export-certified-geometry", b"TEST_ONLY binary")
        source = self.write("geometry/export_certified_geometry.cc", b"TEST_ONLY source")
        headers = {}
        for index in range(21):
            name = f"headers/source-{index}.h"
            headers[name] = self.write("geometry/" + name, ("TEST_ONLY " + name).encode())["sha256"]
        compiler = {
            "builder_image_id": self.builder,
            "runtime_library_sha256": self.library,
            "binary_sha256": binary["sha256"],
            "source_sha256": source["sha256"],
            "headers_sha256": headers,
        }
        self.write("geometry/compiler-manifest.json", compiler)
        indexed = {
            str(p.relative_to(self.root / "geometry")): bundle.checksum(p)
            for p in (self.root / "geometry").rglob("*")
            if p.is_file()
        }
        self.receipt = {
            "schema": "waydex-certified-walk-geometry/1",
            "geometry_only": True,
            "promotes_minimum_or_coverage": False,
            "files_sha256": indexed,
            "proof_binding_sha256": self.binding["sha256"],
            "native_receipt_sha256": native_ref["sha256"],
            "physical_receipt_sha256": physical_ref["sha256"],
            "model_receipt_sha256": model_ref["sha256"],
            "snapshot_id": "TEST_ONLY",
            "snapshot_hash": self.canonical,
            "source_scope_id": self.scope,
            "source_files_sha256": native_files,
            "source_build_manifest_sha256": native_files["build-manifest.json"],
            "candidate_sha256": native_files["turn-candidate.json"],
            "builder_image_id": self.builder,
            "runtime_library_sha256": self.library,
        }
        self.supplement = {
            "schema_version": "waydex-certified-walk-geometry-binding/1",
            "receipt": self.write("geometry/receipt.json", self.receipt),
            "original_snapshot": self.snapshot,
        }
        self.write(
            "provenance/original-product-manifest.json",
            {
                "snapshots": [
                    {
                        "snapshot_id": "TEST_ONLY",
                        "content_hash": self.canonical,
                        "sha256": self.snapshot["sha256"],
                        "path": "accepted-data.json",
                    }
                ]
            },
        )
        self.publish_binding()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else bundle.encoded(value))
        return {"path": name, "sha256": bundle.checksum(path)}

    def publish_binding(self):
        self.supplement["receipt"] = self.write("geometry/receipt.json", self.receipt)
        ref = self.write("geometry-supplement.json", self.supplement)
        self.binding = self.write("proof-binding.json", dict(self.core, geometry=ref))

    def verify(self):
        return bundle.verify_geometry(
            self.root, self.root / "proof-binding.json", self.binding["sha256"]
        )

    def test_all_indexed_files_are_checked(self):
        result = self.verify()
        self.assertTrue(result["configured"])
        self.assertEqual(result["files"], 32)

    def test_mount_root_alias_keeps_relative_references(self):
        alias = self.root / "packet-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        result = bundle.verify_geometry(alias, alias / "proof-binding.json", self.binding["sha256"])
        self.assertTrue(result["configured"])

    def test_omitted_reference_keeps_legacy_transport_unchanged(self):
        self.binding = self.write("proof-binding.json", self.core)
        self.assertEqual(self.verify(), {"configured": False})

    def test_null_reference_keeps_legacy_transport_unchanged(self):
        self.binding = self.write("proof-binding.json", dict(self.core, geometry=None))
        self.assertEqual(self.verify(), {"configured": False})

    def test_unused_header_tampering_is_rejected(self):
        self.write("geometry/headers/source-20.h", b"modified unused source")
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_indexed_header_is_rejected(self):
        (self.root / "geometry/headers/source-20.h").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_core_binding_change_is_rejected(self):
        value = json.loads((self.root / "proof-binding.json").read_text())
        value["snapshot_hash"] = "d" * 64
        self.binding = self.write("proof-binding.json", value)
        with self.assertRaisesRegex(ValueError, "core"):
            self.verify()

    def test_canonical_hash_cannot_replace_snapshot_byte_hash(self):
        self.supplement["original_snapshot"]["sha256"] = self.canonical
        self.publish_binding()
        with self.assertRaises(ValueError):
            self.verify()

    def test_index_path_escape_is_rejected(self):
        self.receipt["files_sha256"]["../outside.h"] = "e" * 64
        self.publish_binding()
        with self.assertRaises(ValueError):
            self.verify()

    def test_header_symlink_is_rejected(self):
        path = self.root / "geometry/headers/source-20.h"
        path.unlink()
        path.symlink_to(self.root / "geometry/headers/source-19.h")
        with self.assertRaises(ValueError):
            self.verify()

    def test_oversized_geometry_file_is_rejected(self):
        path = self.root / "geometry/oversized.bin"
        with path.open("wb") as target:
            target.truncate(bundle.MAX_GEOMETRY_BYTES + 1)
        self.receipt["files_sha256"]["oversized.bin"] = bundle.checksum(path)
        self.publish_binding()
        with self.assertRaisesRegex(ValueError, "budget"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
