import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from dpcompat.models import PackFormat
from dpcompat.packio import create_deterministic_zip, flatten_pack, materialize_source

from helpers import make_pack, write


class PackIoTests(unittest.TestCase):
    def test_source_overlay_is_flattened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            metadata = {
                "pack": {"pack_format": 61, "description": "x"},
                "overlays": {"entries": [{"directory": "fmt71", "formats": 71}]},
            }
            (root / "pack.mcmeta").write_text(json.dumps(metadata), encoding="utf-8")
            write(root, "data/demo/function/test.mcfunction", "say base\n")
            write(root, "fmt71/data/demo/function/test.mcfunction", "say overlay\n")
            destination = base / "flat"
            applied = flatten_pack(root, destination, PackFormat(71), metadata)
            self.assertEqual(applied, ["fmt71"])
            self.assertEqual(
                (destination / "data/demo/function/test.mcfunction").read_text(),
                "say overlay\n",
            )

    def test_non_matching_overlay_never_enters_effective_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            metadata = {
                "pack": {"pack_format": 61, "description": "x"},
                "overlays": {"entries": [{"directory": "fmt71", "formats": 71}]},
            }
            write(root, "data/demo/function/test.mcfunction", "say base\n")
            write(root, "fmt71/data/demo/function/test.mcfunction", "say overlay\n")
            destination = base / "flat"
            applied = flatten_pack(root, destination, PackFormat(61), metadata)
            self.assertEqual(applied, [])
            self.assertEqual(
                (destination / "data/demo/function/test.mcfunction").read_text(),
                "say base\n",
            )

    def test_deterministic_zip_is_reproducible_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            write(root, "data/demo/function/test.mcfunction", "say hello\n")
            first = base / "first.zip"
            second = base / "second.zip"
            create_deterministic_zip(root, first)
            create_deterministic_zip(root, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            if os.name != "nt":
                self.assertEqual(first.stat().st_mode & 0o777, 0o644)

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape", "bad")
            with self.assertRaises(ValueError), materialize_source(archive_path):
                pass

    def test_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            outside = base / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = root / "data/demo/function/leak.mcfunction"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available on this platform")
            with self.assertRaises(ValueError), materialize_source(root):
                pass

    def test_ambiguous_pack_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            make_pack(base / "other")
            with self.assertRaisesRegex(ValueError, "Multiple possible"), materialize_source(base):
                pass


if __name__ == "__main__":
    unittest.main()
