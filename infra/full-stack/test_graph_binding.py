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


class DerivedOperatorGraphBinding(OperatorGraphBinding):
    """TEST_ONLY source lineage and endpoint controls over a reviewed extract."""

    def setUp(self):
        super().setUp()
        self.snapshot.update(
            sample_points=[
                {
                    "point_id": "p",
                    "territory_id": "TEST_ONLY_city",
                    "access_node_id": "a",
                    "location": {"lon": 0.0, "lat": 0.0},
                }
            ],
            destinations=[
                {
                    "destination_id": "d",
                    "territory_id": "TEST_ONLY_city",
                    "access_node_id": "b",
                    "location": {"lon": 0.5, "lat": 0.5},
                }
            ],
            access_nodes=[
                {"node_id": "a", "location": {"lon": 0.0, "lat": 0.1}},
                {"node_id": "b", "location": {"lon": 0.5, "lat": 0.4}},
            ],
        )
        original = self.graph / "source.osm.pbf"
        original.rename(self.graph / "upstream.osm.pbf")
        self.source["raw_path"] = "TEST_ONLY_osm/upstream.osm.pbf"
        self.manifest["upstream_source"] = {
            "path": "upstream.osm.pbf",
            "sha256": self.source["checksum"]["value"],
            "bytes": 16,
        }
        derived = self.write("graph/source.osm.pbf", b"TEST_ONLY derived PBF")
        self.manifest.update(
            source_sha256=derived["sha256"], source_bytes=21, outside_paths_complete=False
        )
        package = self.write(
            "graph/tool-packages/osmium-tool_1.16.0-1build1_TEST_ONLY.deb", b"TEST_ONLY package"
        )
        self.installed = {
            "schema": "waydex-standard-tool-install/1",
            "exit_code": 0,
            "oom_killed": False,
            "limit_reason": None,
            "version": "osmium version 1.16.0\n",
            "image_id": "sha256:" + "d" * 64,
            "command": ["docker", "run", self.image],
            "packages": [
                {"path": Path(package["path"]).name, "sha256": package["sha256"], "bytes": 17}
            ],
        }
        binary = "4740189c5cf26b399f5bc707fdea0df75cd9b7cd60dc3f9918b0a794c8bd3350"
        self.write("graph/tool-binary-sha256.txt", (binary + "  /usr/bin/osmium\n").encode())
        way_refs = self.write("graph/way-refs-check.txt", b"Nodes in ways missing: 0\n")
        restriction_refs = self.write(
            "graph/restriction-refs-check.txt",
            b"Nodes in ways missing: 0\nNodes in relations missing: 0\nWays in relations missing: 0\nRelations in relations missing: 0\n",
        )
        scope = self.write("graph/restriction-scope.osm.pbf", b"TEST_ONLY restriction subset")
        bbox = [-1, -1, 1, 1]
        self.fileinfo = {
            "header": {"boxes": [bbox]},
            "data": {"objects_ordered": True},
            "file": {"size": 21},
        }
        info = self.write("graph/extract-fileinfo.json", self.fileinfo)
        self.endpoint_inventory = {
            "bbox": bbox,
            "outside_count": 0,
            "snapshot_id": "TEST_ONLY_snapshot",
            "endpoints": [
                {"kind": "point", "id": "p", "lon": 0.0, "lat": 0.0},
                {"kind": "destination", "id": "d", "lon": 0.5, "lat": 0.5},
                {"kind": "access", "id": "a", "lon": 0.0, "lat": 0.1},
                {"kind": "access", "id": "b", "lon": 0.5, "lat": 0.4},
            ],
        }
        endpoints = self.write("graph/endpoint-inventory.json", self.endpoint_inventory)
        self.derivation = {
            "schema": "waydex-source-bound-osm-extract/1",
            "upstream_source_filename": "upstream.osm.pbf",
            "upstream_source_sha256": self.source["checksum"]["value"],
            "upstream_source_bytes": 16,
            "derived_input_filename": "source.osm.pbf",
            "derived_input_sha256": derived["sha256"],
            "derived_input_bytes": 21,
            "exit_code": 0,
            "container_exit_code": 0,
            "oom_killed": False,
            "limit_reason": None,
            "way_references_verified": True,
            "restriction_member_references_verified": True,
            "outside_paths_complete": False,
            "minimum_proven": False,
            "strategy": "smart",
            "strategy_options": ["types=multipolygon,restriction"],
            "tool_version": "1.16.0",
            "tool_binary_sha256": binary,
            "tool_image_id": self.installed["image_id"],
            "bbox": bbox,
            "endpoint_snapshot_id": "TEST_ONLY_snapshot",
            "endpoint_inventory_sha256": endpoints["sha256"],
            "way_refs_report_sha256": way_refs["sha256"],
            "restriction_refs_report_sha256": restriction_refs["sha256"],
            "restriction_scope_sha256": scope["sha256"],
            "fileinfo_sha256": info["sha256"],
        }
        self.publish()

    def publish(self):
        if hasattr(self, "derivation"):
            source = self.write("graph/source-manifest.json", self.source)
            self.derivation["source_manifest_sha256"] = source["sha256"]
            self.manifest["derivation"] = self.write("graph/extract-receipt.json", self.derivation)
            self.manifest["derivation"]["path"] = "extract-receipt.json"
            self.manifest["tool_installation"] = self.write(
                "graph/tool-install-receipt.json", self.installed
            )
            self.manifest["tool_installation"]["path"] = "tool-install-receipt.json"
        super().publish()

    def test_swapped_original_and_derived_identity_is_rejected(self):
        self.manifest["upstream_source"]["sha256"], self.manifest["source_sha256"] = (
            self.manifest["source_sha256"],
            self.manifest["upstream_source"]["sha256"],
        )
        self.publish()
        with self.assertRaisesRegex(ValueError, "separate binding"):
            self.verify()

    def test_missing_derivation_receipt_field_is_rejected(self):
        del self.derivation["upstream_source_sha256"]
        self.publish()
        with self.assertRaises((ValueError, KeyError)):
            self.verify()

    def test_derived_input_without_derivation_cannot_use_original_branch(self):
        self.publish()
        del self.manifest["derivation"]
        OperatorGraphBinding.publish(self)
        with self.assertRaisesRegex(ValueError, "explicit derivation"):
            self.verify()

    def test_wrong_standard_binary_is_rejected(self):
        self.derivation["tool_binary_sha256"] = "0" * 64
        self.publish()
        with self.assertRaisesRegex(ValueError, "tool identity"):
            self.verify()

    def test_wrong_standard_image_is_rejected(self):
        self.derivation["tool_image_id"] = "sha256:" + "0" * 64
        self.publish()
        with self.assertRaisesRegex(ValueError, "tool identity"):
            self.verify()

    def test_missing_tool_package_is_rejected(self):
        (self.graph / "tool-packages/osmium-tool_1.16.0-1build1_TEST_ONLY.deb").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_restriction_member_report_is_rejected(self):
        bad = self.write(
            "graph/restriction-refs-check.txt",
            b"Nodes in ways missing: 0\nNodes in relations missing: 0\nWays in relations missing: 1\nRelations in relations missing: 0\n",
        )
        self.derivation["restriction_refs_report_sha256"] = bad["sha256"]
        self.publish()
        with self.assertRaisesRegex(ValueError, "missing references"):
            self.verify()

    def test_missing_restriction_binary_is_rejected(self):
        (self.graph / "restriction-scope.osm.pbf").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_outside_current_endpoint_is_rejected(self):
        self.snapshot["sample_points"][0]["location"]["lon"] = 2
        self.publish()
        with self.assertRaisesRegex(ValueError, "outside"):
            self.verify()

    def test_changed_inside_endpoint_is_rejected(self):
        self.snapshot["sample_points"][0]["location"]["lon"] = 0.2
        self.publish()
        with self.assertRaisesRegex(ValueError, "outside"):
            self.verify()

    def test_canonical_access_node_cannot_disappear(self):
        self.snapshot["access_nodes"].pop()
        self.publish()
        with self.assertRaisesRegex(ValueError, "access node"):
            self.verify()

    def test_reviewed_endpoint_context_is_preserved_for_corrected_snapshot(self):
        self.snapshot["snapshot_id"] = "TEST_ONLY_corrected_snapshot"
        self.parent["bundle_id"] = self.snapshot["snapshot_id"]
        self.parent["build"]["snapshot_id"] = self.snapshot["snapshot_id"]
        self.publish()
        self.verify()
        self.assertEqual(self.derivation["endpoint_snapshot_id"], "TEST_ONLY_snapshot")


if __name__ == "__main__":
    unittest.main()
