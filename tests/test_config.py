import tempfile
import unittest
from pathlib import Path

from dpcompat.config import load_config


class ConfigTests(unittest.TestCase):
    def test_policy_and_relative_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Resolve the base so the comparison is canonical: on Windows CI the
            # temp dir may use the 8.3 short name (e.g. RUNNER~1) while
            # load_config resolves fallbacks to the long form (runneradmin).
            root = Path(temp_dir).resolve()
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

    def test_bundle_pack_root_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "dpcompat.toml"
            config.write_text('[build]\npack_root="bundle/datapack"\n', encoding="utf-8")
            self.assertEqual(load_config(config).pack_root, "bundle/datapack")
            config.write_text('[build]\npack_root="../escape"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "safe relative"):
                load_config(config)


if __name__ == "__main__":
    unittest.main()
