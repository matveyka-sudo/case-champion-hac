"""Transfer regressions use tiny local files; no services or graph inputs required."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "four_profile_restore", Path(__file__).with_name("four_profile_restore.py")
)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class GraphTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "tile.gph").write_bytes(b"reviewed tile bytes")
        self.sources = {"graphs/test": self.source}
        self.manifest = {
            "schema_version": "waydex-four-profile-graph-transfer/1",
            "groups": {"graphs/test": transfer.inventory(self.source)},
        }

    def test_copy_preserves_bytes_and_publishes_read_only_completion_last(self):
        output = self.root / "transfer"
        transfer.copy_sources(self.sources, self.manifest, output)
        copied = output / "graphs/test/tile.gph"
        self.assertEqual(copied.read_bytes(), b"reviewed tile bytes")
        self.assertEqual(copied.stat().st_mode & 0o777, 0o444)
        self.assertEqual((output / transfer.MARKER).read_bytes(), transfer.encoded(self.manifest))
        self.assertEqual(transfer.inventory(copied.parent), self.manifest["groups"]["graphs/test"])

    def test_changed_source_leaves_no_completion_record(self):
        (self.source / "tile.gph").write_bytes(b"changed source")
        output = self.root / "transfer"
        with self.assertRaisesRegex(ValueError, "source changed"):
            transfer.copy_sources(self.sources, self.manifest, output)
        self.assertFalse((output / transfer.MARKER).exists())

    def test_existing_destination_is_never_replaced(self):
        output = self.root / "transfer"
        output.mkdir()
        (output / "sentinel").write_bytes(b"retained")
        with self.assertRaisesRegex(ValueError, "must be new"):
            transfer.copy_sources(self.sources, self.manifest, output)
        self.assertEqual((output / "sentinel").read_bytes(), b"retained")

    def test_destination_inside_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside its sources"):
            transfer.copy_sources(self.sources, self.manifest, self.source / "nested")
        self.assertFalse((self.source / "nested").exists())

    def test_file_and_parent_links_are_rejected(self):
        (self.source / "link.gph").symlink_to(self.source / "tile.gph")
        with self.assertRaisesRegex(ValueError, "cannot contain links"):
            transfer.inventory(self.source)
        link = self.root / "linked-directory"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "cannot contain links"):
            transfer.read_file(link / "tile.gph")

    def test_oversized_regular_file_is_rejected_before_reading(self):
        large = self.source / "oversized.pbf"
        with large.open("wb") as stream:
            stream.truncate(1024**3 + 1)
        with self.assertRaisesRegex(ValueError, "bounded regular file"):
            transfer.read_file(large)

    def test_non_regular_file_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.source / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "bounded regular file"):
            transfer.read_file(fifo)

    def test_unreviewed_file_fails_inventory_pin_before_graph_loading(self):
        (self.source / "extra.gph").write_bytes(b"unreviewed")
        with patch.object(transfer, "PINS", {"graphs/test": (1, 19, "0" * 64)}):
            # A fake bundle checker isolates the inventory failure from runtime fixtures.
            checker = type("Bundle", (), {"verify": lambda *args: None})()
            with patch.object(transfer, "load_module", return_value=checker):
                with patch.object(transfer, "read_file", wraps=transfer.read_file) as reader:
                    (self.root / "manifest.json").write_bytes(b"{}")
                    with patch.object(
                        transfer,
                        "PRODUCT_SHA",
                        transfer.read_file(self.root / "manifest.json")["sha256"],
                    ):
                        with self.assertRaisesRegex(ValueError, "inventory differs"):
                            transfer.verify_sources(self.root, self.sources)
                    self.assertGreater(reader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
