import unittest

from dpcompat import snbt


class SnbtTests(unittest.TestCase):
    def test_round_trip_compound_and_array(self) -> None:
        value = snbt.loads('{id:"minecraft:pig",Pos:[1.0d,2.0d,3.0d],sleeping_pos:[I;1,2,3]}')
        encoded = snbt.dumps(value)
        self.assertEqual(snbt.loads(encoded), value)

    def test_duplicate_keys_fail(self) -> None:
        with self.assertRaises(snbt.SnbtError):
            snbt.loads("{a:1,a:2}")


if __name__ == "__main__":
    unittest.main()
