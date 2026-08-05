import unittest

from dpcompat.metadata import (
    detect_format_range,
    overlay_matches,
    render_single_target_metadata,
    render_universal_metadata,
)
from dpcompat.models import PackFormat, PackFormatRange


class MetadataTests(unittest.TestCase):
    def test_detect_old_metadata(self) -> None:
        declared, preferred = detect_format_range({"pack": {"pack_format": 61, "supported_formats": [61, 71]}})
        self.assertEqual(declared.minimum, PackFormat(61))
        self.assertEqual(declared.maximum, PackFormat(71))
        self.assertEqual(preferred, PackFormat(61))

    def test_new_integer_max_includes_minor_versions(self) -> None:
        declared, _ = detect_format_range({"pack": {"min_format": 88, "max_format": 94}})
        self.assertTrue(declared.contains(PackFormat(94, 1)))

    def test_render_new_metadata(self) -> None:
        result = render_single_target_metadata(
            {"pack": {"pack_format": 61, "description": "x"}},
            PackFormat(94, 1),
            "x",
        )
        self.assertEqual(result["pack"]["min_format"], [94, 1])
        self.assertNotIn("pack_format", result["pack"])

    def test_universal_metadata_has_legacy_and_new_fields(self) -> None:
        result = render_universal_metadata(
            {"pack": {"description": "x"}},
            [
                (PackFormatRange(PackFormat(61), PackFormat(61)), "old"),
                (PackFormatRange(PackFormat(94, 1), PackFormat(94, 1)), "new"),
            ],
            "x",
        )
        self.assertEqual(result["pack"]["pack_format"], 61)
        self.assertEqual(result["pack"]["max_format"], [94, 1])
        self.assertTrue(overlay_matches(result["overlays"]["entries"][1], PackFormat(94, 1)))


if __name__ == "__main__":
    unittest.main()
