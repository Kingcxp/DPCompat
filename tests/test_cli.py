import json
import os
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

    def test_plugin_commands_round_trip(self) -> None:
        project = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_file = Path(temp_dir) / "demo.py"
            plugin_file.write_text(
                "from dpcompat.migrations.base import MigrationContext, RuleResult, crosses\n"
                "from dpcompat.models import Compatibility, MigrationRecord, PackFormat\n"
                "PLUGIN = {\n"
                '    "id": "cli.demo@80",\n'
                '    "name": "CLI 演示",\n'
                '    "description": "测试用插件。",\n'
                '    "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-6"],\n'
                "}\n"
                "class DemoRule:\n"
                '    id = "cli.demo-rule@80"\n'
                "    boundary = PackFormat(80)\n"
                "    def applies(self, source, target):\n"
                "        return crosses(source, target, self.boundary)\n"
                "    def apply(self, context):\n"
                "        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))\n"
                "RULES = (DemoRule(),)\n",
                encoding="utf-8",
            )
            env = {**os.environ, "DPCOMPAT_PLUGIN_DIR": str(Path(temp_dir) / "plugins")}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-m", "dpcompat", "plugin", *args],
                    cwd=project,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

            install = run("install", str(plugin_file))
            self.assertEqual(install.returncode, 0, install.stderr)
            listing = run("list", "--json")
            self.assertEqual(listing.returncode, 0, listing.stderr)
            infos = json.loads(listing.stdout)
            demo = next(item for item in infos if item["id"] == "cli.demo@80")
            self.assertTrue(demo["enabled"])
            self.assertEqual(demo["origin"], "file")
            self.assertEqual(demo["rules"], ["cli.demo-rule@80"])

            self.assertEqual(run("disable", "cli.demo@80").returncode, 0)
            listing = run("list", "--json")
            demo = next(item for item in json.loads(listing.stdout) if item["id"] == "cli.demo@80")
            self.assertFalse(demo["enabled"])

            # The disabled plugin's rule must disappear from the effective rules.
            rules = subprocess.run(
                [sys.executable, "-m", "dpcompat", "rules", "--json"],
                cwd=project,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(rules.returncode, 0, rules.stderr)
            rule_ids = [item["id"] for item in json.loads(rules.stdout)]
            self.assertNotIn("cli.demo-rule@80", rule_ids)

            self.assertEqual(run("enable", "cli.demo@80").returncode, 0)
            rules = subprocess.run(
                [sys.executable, "-m", "dpcompat", "rules", "--json"],
                cwd=project,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("cli.demo-rule@80", [item["id"] for item in json.loads(rules.stdout)])

            self.assertEqual(run("remove", "cli.demo@80").returncode, 0)
            listing = run("list", "--json")
            self.assertNotIn("cli.demo@80", {item["id"] for item in json.loads(listing.stdout)})


if __name__ == "__main__":
    unittest.main()
