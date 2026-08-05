import tempfile
import unittest
from pathlib import Path

from dpcompat.config import load_config


class ConfigTests(unittest.TestCase):
    def test_policy_and_relative_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "dpcompat.toml"
            config.write_text(
                '[build]\ntargets=["1.21.4"]\noutput_name="demo"\n'
                "[policy]\nallow_lossy=true\n"
                '[fallbacks]\n"1.21.4"="compat/1.21.4"\n',
                encoding="utf-8",
            )
            value = load_config(config)
            self.assertTrue(value.policy.allow_lossy)
            self.assertEqual(value.fallbacks["1.21.4"], root / "compat/1.21.4")

    def test_unknown_key_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "dpcompat.toml"
            config.write_text("[build]\ntragets=[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"Unknown \[build\] key"):
                load_config(config)


if __name__ == "__main__":
    unittest.main()
