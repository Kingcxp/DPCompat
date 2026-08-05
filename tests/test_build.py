import json
import tempfile
import unittest
from pathlib import Path

from dpcompat.detector import detect_pack
from dpcompat.models import PackFormat

from helpers import make_pack, write


class DetectorTests(unittest.TestCase):
    def test_exact_metadata_format_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), 61)
            result = detect_pack(root)
            self.assertEqual(result.source_format, PackFormat(61))
            self.assertEqual(result.confidence, 0.98)

    def test_content_evidence_raises_the_inferred_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/test.mcfunction", "give @s minecraft:iron_chain\n")
            result = detect_pack(root)
            self.assertEqual(result.source_format, PackFormat(88))
            self.assertTrue(any(item.code == "metadata-understates-content" for item in result.diagnostics))

    def test_overlay_range_selects_a_known_registered_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [71, 0])
            metadata = {
                "pack": {"min_format": [71, 0], "max_format": [94, 1], "description": "x"},
                "overlays": {"entries": [{"directory": "fmt94", "min_format": [94, 1], "max_format": [94, 1]}]},
            }
            (root / "pack.mcmeta").write_text(json.dumps(metadata), encoding="utf-8")
            result = detect_pack(root)
            self.assertGreaterEqual(result.source_format, PackFormat(94, 1))

    def test_source_outside_declared_range_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), 61)
            metadata = {"pack": {"pack_format": 71, "supported_formats": 61, "description": "x"}}
            (root / "pack.mcmeta").write_text(json.dumps(metadata), encoding="utf-8")
            result = detect_pack(root)
            self.assertTrue(any(item.code == "metadata-source-outside-range" for item in result.diagnostics))


if __name__ == "__main__":
    unittest.main()
