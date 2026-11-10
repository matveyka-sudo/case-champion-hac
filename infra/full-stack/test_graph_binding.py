"""TEST_ONLY graph transport controls; no numerical route/minimum proof."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "bound_runtime", Path(__file__).with_name("runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class OperatorGraphBinding(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.graph = self.root / "graph"
        self.graph.mkdir()
        self.image = runtime.real.IMAGE
        self.generator = b"TEST_ONLY pinned configuration generator"
        self.profile = {
            "engine_version": "3.5.1",
            "profile_version": "TEST_ONLY_walk",
            "pedestrian_options": {"type": "foot", "walking_speed": 5},
        }
        self.snapshot = {
            "schema_version": "2",
            "snapshot_id": "TEST_ONLY_snapshot",
            "qa": {"acceptance_state": "accepted"},
            "geographic_scope": ["TEST_ONLY_city"],
            "territories": [{"territory_id": "TEST_ONLY_city", "level": "city"}],
            "source_versions": {"TEST_ONLY_osm": "TEST_ONLY_osm.261107"},
            "provenance": {"graph_version": "TEST_ONLY_osm.261107:walk-network"},
        }
        pbf = self.write("graph/source.osm.pbf", b"TEST_ONLY source")
        self.source = {
            "status": "accepted",
            "source_id": "TEST_ONLY_osm",
            "dataset_id": "TEST_ONLY_osm.261107",
            "raw_path": "TEST_ONLY_osm/source.osm.pbf",
            "checksum": {"algorithm": "sha256", "value": pbf["sha256"], "size_bytes": 16},
        }
        configuration = self.write(
            "graph/valhalla.json", {"mjolnir": {"tile_dir": "/data/tiles", "tile_extract": ""}}
        )
        profile = self.write("graph/walking-profile.json", self.profile)
        self.write("graph/valhalla-build-config-3.5.1.py", self.generator)
        self.write("graph/valhalla-version.txt", b"valhalla_build_tiles 3.5.1\n")
        tile = self.write("graph/tiles/0/001.gph", b"TEST_ONLY tile")
        self.manifest = {
            "source_graph_version": self.snapshot["provenance"]["graph_version"],
            "source_filename": "source.osm.pbf",
            "source_sha256": pbf["sha256"],
            "source_bytes": 16,
            "config_sha256": configuration["sha256"],
            "profile_sha256": profile["sha256"],
            "image_id": self.image.split("@")[1],
            "image_repo_digests": [self.image],
            "files": [{"path": "tiles/0/001.gph", "sha256": tile["sha256"], "bytes": 14}],
        }
        self.binding = {
            "schema_version": "waydex-graph-source-binding/1",
            "city_id": "TEST_ONLY_city",
        }
        self.parent = {
            "bundle_id": "TEST_ONLY_snapshot",
            "build": {
                "snapshot_id": "TEST_ONLY_snapshot",
                "source_graph_version": self.snapshot["provenance"]["graph_version"],
                "engine_version": self.profile["engine_version"],
                "profile_version": self.profile["profile_version"],
                "pedestrian_options_json": json.dumps(self.profile["pedestrian_options"]),
            },
        }
        self.publish()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else runtime.real.encoded(value))
        return {"path": name, "sha256": runtime.real.sha(path)}

    def publish(self):
        source = self.write("graph/source-manifest.json", self.source)
        self.manifest["source_manifest_sha256"] = source["sha256"]
        manifest = self.write("graph/valhalla-build-manifest.json", self.manifest)
        self.parent["build"]["graph_version"] = "valhalla-351-tiles-" + manifest["sha256"]
        snapshot = self.write("accepted-data.json", self.snapshot)
        snapshot.update(
            snapshot_id=self.snapshot["snapshot_id"],
            content_hash=runtime.real.sha(self.root / "accepted-data.json"),
        )
        self.binding.update(snapshot=snapshot, source_manifest=source, graph_manifest=manifest)
        self.reference = self.write("source-binding.json", self.binding)

    def verify(self):
        with (
            patch.dict(
                os.environ,
                {
                    "WAYDEX_GRAPH_BINDING_PATH": str(self.root / "source-binding.json"),
                    "WAYDEX_GRAPH_BINDING_SHA256": self.reference["sha256"],
                },
            ),
            patch.object(
                runtime.real,
                "CONFIG_SHA",
                runtime.real.sha(self.graph / "valhalla-build-config-3.5.1.py"),
            ),
        ):
            return runtime.graph_check(self.graph, self.parent)

    def test_complete_operator_packet_passes(self):
        self.assertEqual(self.verify(), self.binding["graph_manifest"]["sha256"])

    def test_wrong_city_is_rejected(self):
        self.binding["city_id"] = "ANOTHER_CITY"
        self.publish()
        with self.assertRaisesRegex(ValueError, "city"):
            self.verify()

    def test_wrong_source_dataset_is_rejected(self):
        self.source["dataset_id"] = "ANOTHER_SOURCE.261107"
        self.publish()
        with self.assertRaisesRegex(ValueError, "source version"):
            self.verify()

    def test_wrong_graph_artifact_is_rejected(self):
        self.parent["build"]["graph_version"] = "valhalla-351-tiles-" + "0" * 64
        with self.assertRaisesRegex(ValueError, "build binding"):
            self.verify()

    def test_wrong_pbf_bytes_are_rejected(self):
        self.write("graph/source.osm.pbf", b"modified TEST_ONLY source")
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_tile_bytes_are_rejected(self):
        self.write("graph/tiles/0/001.gph", b"modified TEST_ONLY tile")
        with self.assertRaises(ValueError):
            self.verify()

    def test_unlisted_tile_is_rejected(self):
        self.write("graph/tiles/0/002.gph", b"unlisted tile")
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.verify()

    def test_tile_symlink_is_rejected(self):
        path = self.graph / "tiles/0/001.gph"
        original = self.root / "original.gph"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "linked"):
            self.verify()

    def test_wrong_configuration_is_rejected(self):
        self.write("graph/valhalla.json", {"mjolnir": {"tile_dir": "/data/other"}})
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_image_is_rejected(self):
        self.manifest["image_id"] = "sha256:" + "0" * 64
        self.publish()
        with self.assertRaisesRegex(ValueError, "image"):
            self.verify()

    def test_profile_mismatch_is_rejected(self):
        self.parent["build"]["pedestrian_options_json"] = '{"type":"foot","walking_speed":9}'
        with self.assertRaisesRegex(ValueError, "profile"):
            self.verify()

    def test_canonical_snapshot_mismatch_is_rejected(self):
        self.binding["snapshot"]["content_hash"] = "0" * 64
        self.reference = self.write("source-binding.json", self.binding)
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.verify()

    def test_reference_escape_is_rejected(self):
        self.binding["source_manifest"]["path"] = "../outside.json"
        self.reference = self.write("source-binding.json", self.binding)
        with self.assertRaisesRegex(ValueError, "relative"):
            self.verify()

    def test_half_configured_binding_never_falls_back(self):
        with patch.dict(
            os.environ, {"WAYDEX_GRAPH_BINDING_PATH": "", "WAYDEX_GRAPH_BINDING_SHA256": "1"}
        ):
            with self.assertRaisesRegex(ValueError, "Both"):
                runtime.graph_check(self.graph, self.parent)

    def test_bad_binding_hash_never_falls_back(self):
        self.reference["sha256"] = "0" * 64
        with patch.object(runtime.real, "verify_files") as legacy:
            with self.assertRaises(ValueError):
                self.verify()
            legacy.assert_not_called()

    def test_absent_binding_uses_existing_legacy_verifier(self):
        with (
            patch.dict(
                os.environ,
                {"WAYDEX_GRAPH_BINDING_PATH": "", "WAYDEX_GRAPH_BINDING_SHA256": ""},
            ),
            patch.object(runtime.real, "verify_files", side_effect=RuntimeError("legacy marker")),
        ):
            with self.assertRaisesRegex(RuntimeError, "legacy marker"):
                runtime.graph_check(self.graph, self.parent)


if __name__ == "__main__":
    unittest.main()
