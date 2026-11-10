"""Routing source precedence must not bypass the directory tile inventory."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "full_stack_runtime", Path(__file__).with_name("runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class GraphReaderSources(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.graph = Path(self.folder.name)
        self.configuration = {
            "mjolnir": {"tile_dir": "/data/tiles", "tile_extract": "/data/tiles.tar"}
        }

    def tearDown(self):
        self.folder.cleanup()

    def test_absent_configured_archive_uses_verified_directory(self):
        runtime.directory_sources(self.graph, self.configuration)

    def test_archive_appearing_after_build_is_rejected(self):
        (self.graph / "tiles.tar").write_bytes(b"unverified alternative graph archive")
        with self.assertRaisesRegex(ValueError, "override"):
            runtime.directory_sources(self.graph, self.configuration)

    def test_dangling_archive_symlink_is_rejected(self):
        (self.graph / "tiles.tar").symlink_to(self.graph / "not-yet-present.tar")
        with self.assertRaisesRegex(ValueError, "override"):
            runtime.directory_sources(self.graph, self.configuration)

    def test_remote_tile_fallback_is_rejected(self):
        self.configuration["mjolnir"]["tile_url"] = "https://unverified.invalid/{tilePath}"
        with self.assertRaisesRegex(ValueError, "remote"):
            runtime.directory_sources(self.graph, self.configuration)

    def test_different_directory_is_rejected(self):
        self.configuration["mjolnir"]["tile_dir"] = "/data/other-tiles"
        with self.assertRaisesRegex(ValueError, "/data/tiles"):
            runtime.directory_sources(self.graph, self.configuration)

    def test_archive_path_escape_is_rejected(self):
        self.configuration["mjolnir"]["tile_extract"] = "/data/../other.tar"
        with self.assertRaisesRegex(ValueError, "outside"):
            runtime.directory_sources(self.graph, self.configuration)


if __name__ == "__main__":
    unittest.main()
