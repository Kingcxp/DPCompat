import tempfile
import unittest
import zipfile
from pathlib import Path

from dpcompat.packio import materialize_source

from helpers import make_pack


class PackIoTests(unittest.TestCase):
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
