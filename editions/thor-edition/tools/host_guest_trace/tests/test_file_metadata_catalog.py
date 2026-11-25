from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_metadata_catalog as catalog


class FileMetadataTests(unittest.TestCase):
    def test_normalization_is_absolute_ascii_and_nontraversing(self):
        self.assertEqual(catalog.canonical_path("\\??\\GAME:/Folder/A.BIN"), "game:\\folder\\a.bin")
        self.assertEqual(catalog.canonical_path("game:"), "game:\\")
        self.assertEqual(catalog.canonical_path("1GAME:/a"), "1game:\\a")
        for name in ("", "../a", "game:relative", "game:\\..\\a", "game:\\.\\a", "game:\\\\a", "game:\\a?",
                     "game:\\a:stream", "game:\\a.", "game:\\a ", "!game:\\a", "game:\\é", "game:\\a\0b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                catalog.canonical_path(name)

    def test_snapshot_has_real_size_and_every_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Folder").mkdir()
            (root / "Folder/A.BIN").write_bytes(b"synthetic" * 17)
            data = catalog.snapshot(root, "game:")
            self.assertEqual(catalog.validate_catalog(data), 3)
            self.assertIn(struct.pack(">QQI", 2048, 153, 1), data)
            self.assertIn(b"game:\\folder\\a.bin", data)
            self.assertNotIn(str(root).encode(), data)
            for end in range(len(data)):
                with self.assertRaises(ValueError):
                    catalog.validate_catalog(data[:end])
            for changed in (data + b"x", b"BADMAGIC" + data[8:], data[:8] + struct.pack(">I", 0) + data[12:]):
                with self.assertRaises(ValueError):
                    catalog.validate_catalog(changed)

    def test_links_and_case_collisions_are_not_silently_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one").write_bytes(b"a")
            (root / "linked").symlink_to(root / "one")
            with self.assertRaises(ValueError):
                catalog.snapshot(root, "game:")
            (root / "linked").unlink()
            # Case-insensitive host volumes cannot create both spellings. Feed
            # colliding names through the canonical path contract separately.
            self.assertEqual(catalog.canonical_path("game:\\ONE"), catalog.canonical_path("game:\\one"))
            with mock.patch.object(catalog, "MAX_RECORDS", 1), self.assertRaises(ValueError):
                catalog.snapshot(root, "game:")


if __name__ == "__main__":
    unittest.main()
