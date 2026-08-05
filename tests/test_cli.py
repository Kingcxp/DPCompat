import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_plan_json_stdout_is_machine_readable(self) -> None:
        project = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dpcompat",
                    "--log-dir",
                    str(Path(temp_dir) / "logs"),
                    "plan",
                    str(project / "examples/simple_pack"),
                    "--target",
                    "1.21.4",
                    "--json",
                ],
                cwd=project,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
        parsed = json.loads(process.stdout)
        self.assertIn("targets", parsed)


if __name__ == "__main__":
    unittest.main()
