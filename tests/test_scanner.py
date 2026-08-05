import tempfile
import unittest
from pathlib import Path

from dpcompat.models import PackFormat, Severity
from dpcompat.scanner import scan_pack

from helpers import make_pack, write


class ScannerTests(unittest.TestCase):
    def test_new_resource_blocks_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/sulfur_cube_archetype/test.json", "{}\n")
            scan = scan_pack(root, target=PackFormat(101, 1))
            self.assertEqual(scan.inferred_format, PackFormat(107, 1))
            self.assertTrue(any(item.severity == Severity.ERROR for item in scan.diagnostics))

    def test_iron_chain_infers_1_21_9_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/test.mcfunction", "give @s minecraft:iron_chain\n")
            scan = scan_pack(root)
            self.assertEqual(scan.inferred_format, PackFormat(88))

    def test_environment_attributes_refuse_old_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [94, 1])
            write(
                root,
                "data/demo/dimension_type/test.json",
                '{"attributes":{"minecraft:gameplay/water_evaporates":true}}\n',
            )
            scan = scan_pack(root, target=PackFormat(88))
            self.assertTrue(any(item.code == "environment-attributes-too-new" for item in scan.diagnostics))

    def test_invalid_resource_paths_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/BadName.mcfunction", "say invalid\n")
            scan = scan_pack(root)
            self.assertEqual(
                {item.code for item in scan.diagnostics},
                {"invalid-resource-path"},
            )


if __name__ == "__main__":
    unittest.main()
