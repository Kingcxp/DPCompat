import unittest

from dpcompat.models import PackFormat


class VersionTests(unittest.TestCase):
    def test_pack_format_parse(self) -> None:
        self.assertEqual(PackFormat.parse(88), PackFormat(88, 0))
        self.assertEqual(PackFormat.parse([94, 1]), PackFormat(94, 1))
        self.assertEqual(PackFormat.parse("107.1"), PackFormat(107, 1))

    def test_pack_format_comparison_and_minor_versions(self) -> None:
        self.assertLess(PackFormat(88), PackFormat(94, 1))
        self.assertEqual(PackFormat(94, 1).exact_metadata_value(), [94, 1])
        self.assertEqual(PackFormat(61).compact_metadata_value(), 61)


if __name__ == "__main__":
    unittest.main()
