import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from dpcompat.detector import detect_pack
from dpcompat.engine import compile_pack
from dpcompat.models import PackFormat
from dpcompat.versions import resolve_profile

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


class EngineBuildTests(unittest.TestCase):
    def test_build_chain_rename_through_the_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            write(root, "data/demo/function/load.mcfunction", "give @s minecraft:chain\n")
            write(
                root,
                "data/demo/recipe/chain_copy.json",
                '{"type":"minecraft:crafting_shapeless","ingredients":["minecraft:chain"],'
                '"result":{"id":"minecraft:chain","count":1}}\n',
            )
            output = base / "out"
            detection, results, universal = compile_pack(
                root,
                [resolve_profile("1.21.4"), resolve_profile("1.21.9")],
                output,
                universal=False,
            )
            self.assertEqual(str(detection.source_format), "61")
            self.assertTrue(all(result.successful for result in results))
            self.assertIsNone(universal)
            modern = results[1].archive
            assert modern is not None
            with zipfile.ZipFile(modern) as archive:
                text = archive.read("data/demo/function/load.mcfunction").decode()
                recipe = json.loads(archive.read("data/demo/recipe/chain_copy.json"))
            self.assertIn("minecraft:iron_chain", text)
            self.assertEqual(recipe["result"]["id"], "minecraft:iron_chain")

    def test_failed_target_never_publishes_an_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            write(
                root,
                "data/demo/timeline/test.json",
                '{"clock":"demo:clock","tracks":{}}\n',
            )
            output = base / "out"
            _, results, _ = compile_pack(
                root,
                [resolve_profile("1.21.11")],
                output,
                source_format=PackFormat(101, 1),
            )
            self.assertFalse(results[0].successful)
            self.assertIsNone(results[0].archive)
            self.assertEqual(list(output.glob("*.zip")), [])

    def test_explicit_source_format_is_recorded_as_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            detection, results, _ = compile_pack(
                root,
                [resolve_profile("1.21.4")],
                Path(temp_dir) / "out",
                source_format=PackFormat(71),
                emit_archives=False,
            )
            self.assertEqual(detection.source_format, PackFormat(71))
            self.assertTrue(any(item.code == "source-format-overridden" for item in detection.diagnostics))

    def test_universal_guard_and_complete_overlay_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = make_pack(base / "pack")
            write(root, "data/demo/function/load.mcfunction", "say loaded\n")
            output = base / "out"
            _, results, universal = compile_pack(
                root,
                [resolve_profile("1.21.4"), resolve_profile("1.21.9")],
                output,
                universal=True,
            )
            self.assertTrue(all(result.successful for result in results))
            self.assertIsNotNone(universal)
            assert universal is not None
            with zipfile.ZipFile(universal) as archive:
                names = set(archive.namelist())
                self.assertIn("data/dpcompat/function/unsupported_format.mcfunction", names)
                self.assertIn("fmt_61_0/data/minecraft/tags/function/load.json", names)
                self.assertIn("fmt_88_0/data/demo/function/load.mcfunction", names)
                metadata = json.loads(archive.read("pack.mcmeta"))
            self.assertEqual(len(metadata["overlays"]["entries"]), 2)

    def test_single_format_build_has_no_universal_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/load.mcfunction", "say loaded\n")
            _, results, universal = compile_pack(
                root,
                [resolve_profile("1.21.9"), resolve_profile("1.21.10")],
                Path(temp_dir) / "out",
                universal=True,
            )
            self.assertTrue(all(result.successful for result in results))
            self.assertIsNone(universal)


if __name__ == "__main__":
    unittest.main()
