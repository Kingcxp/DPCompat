import unittest

from dpcompat.models import PackFormat
from dpcompat.versions import resolve_profile


class VersionTests(unittest.TestCase):
    def test_pack_format_parse(self) -> None:
        self.assertEqual(PackFormat.parse(88), PackFormat(88, 0))
        self.assertEqual(PackFormat.parse([94, 1]), PackFormat(94, 1))
        self.assertEqual(PackFormat.parse("107.1"), PackFormat(107, 1))

    def test_resolve_latest(self) -> None:
        self.assertEqual(resolve_profile("latest").game_version, "26.2")


if __name__ == "__main__":
    unittest.main()
