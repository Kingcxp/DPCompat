import tempfile
import unittest
from pathlib import Path

from dpcompat.servercheck import check_with_server


class ServerCheckTests(unittest.TestCase):
    def test_eula_requires_explicit_consent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack = root / "pack.zip"
            jar = root / "server.jar"
            pack.write_bytes(b"zip")
            jar.write_bytes(b"jar")
            with self.assertRaisesRegex(ValueError, "EULA"):
                check_with_server(pack, jar)


if __name__ == "__main__":
    unittest.main()
