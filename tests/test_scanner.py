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

    def test_object_valued_type_is_not_treated_as_text_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/advancement/hit.json",
                '{"criteria":{"hit":{"trigger":"entity_hurt_player","conditions":'
                '{"damage":{"type":{"tags":[{"expected":true,"id":"demo:mob_attack"}]}}}}}}\n',
            )

            scan = scan_pack(root)

            self.assertFalse(any(item.code == "invalid-json" for item in scan.diagnostics))
            self.assertEqual(scan.inferred_format, PackFormat(61))

    def test_invalid_runtime_path_errors_but_readme_only_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/tags/README.md", "Contributor notes\n")
            write(root, "data/demo/function/BadName.mcfunction", "say invalid\n")

            scan = scan_pack(root)

            by_code = {item.code: item for item in scan.diagnostics}
            self.assertEqual(by_code["non-runtime-file-invalid-path"].severity, Severity.WARNING)
            self.assertEqual(by_code["invalid-resource-path"].severity, Severity.ERROR)


if __name__ == "__main__":
    unittest.main()
