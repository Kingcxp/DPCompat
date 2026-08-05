import unittest

from dpcompat import nbt


class NbtTests(unittest.TestCase):
    def test_binary_round_trip_gzip(self) -> None:
        document = nbt.NbtDocument(
            "",
            nbt.NbtTag(
                nbt.TAG_COMPOUND,
                {
                    "name": nbt.NbtTag(nbt.TAG_STRING, "demo"),
                    "values": nbt.NbtTag(
                        nbt.TAG_LIST,
                        nbt.NbtList(
                            nbt.TAG_INT,
                            [nbt.NbtTag(nbt.TAG_INT, 1), nbt.NbtTag(nbt.TAG_INT, 2)],
                        ),
                    ),
                },
            ),
            compressed=True,
        )
        decoded = nbt.loads(nbt.dumps(document))
        self.assertEqual(decoded.name, "")
        self.assertEqual(decoded.root.value["name"].value, "demo")


if __name__ == "__main__":
    unittest.main()
