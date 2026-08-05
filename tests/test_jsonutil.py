import unittest

from dpcompat.jsonutil import DuplicateJsonKeyError, loads_lenient, loads_strict


class JsonUtilTests(unittest.TestCase):
    def test_comments_and_trailing_commas(self) -> None:
        value = loads_lenient('{/* hello */ "a": [1, 2,], // line\n}')
        self.assertEqual(value, {"a": [1, 2]})

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(DuplicateJsonKeyError):
            loads_strict('{"a":1,"a":2}')


if __name__ == "__main__":
    unittest.main()
