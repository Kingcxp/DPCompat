import unittest

from dpcompat.models import PackFormat
from dpcompat.versions import resolve_profile


class VersionTests(unittest.TestCase):
    def test_pack_format_parse(self) -> None:
        self.assertEqual(PackFormat.parse(88), PackFormat(88, 0))
        self.assertEqual(PackFormat.parse([94, 1]), PackFormat(94, 1))
        self.assertEqual(PackFormat.parse("107.1"), PackFormat(107, 1))
        self.assertEqual(PackFormat.parse("121.0"), PackFormat(121, 0))

    def test_resolve_latest(self) -> None:
        self.assertEqual(resolve_profile("latest").game_version, "26.3")
        self.assertEqual(resolve_profile("26.3").pack_format, PackFormat(121, 0))
        self.assertEqual(resolve_profile("121").game_version, "26.3")


if __name__ == "__main__":
    unittest.main()
