"""Regression tests for contributor-facing source documentation."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "dpcompat"


class DocumentationTests(unittest.TestCase):
    """Keep the teaching edition's public contracts visible to contributors."""

    def test_modules_and_public_apis_have_docstrings(self) -> None:
        missing: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if ast.get_docstring(tree) is None:
                missing.append(f"{relative}: module")
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    continue
                if node.name.startswith("_"):
                    continue
                if ast.get_docstring(node) is None:
                    missing.append(f"{relative}:{node.lineno}: {node.name}")
        self.assertEqual([], missing, "Missing contributor-facing docstrings")

    def test_ai_agent_docs_cover_every_subsystem(self) -> None:
        agent_dir = PROJECT_ROOT / "docs" / "agent"
        required = {
            "README.md": "index and navigation",
            "ARCHITECTURE.md": "module map and data flow",
            "CODING_CONVENTIONS.md": "style and gate rules",
            "MIGRATION_RULES.md": "rule protocol and boundaries",
            "UI_I18N.md": "TUI and localization system",
            "PLUGIN_SYSTEM.md": "plugin store internals",
            "TESTING.md": "test layout and verification",
            "RELEASE.md": "release process",
        }
        for name, purpose in required.items():
            path = agent_dir / name
            self.assertTrue(path.is_file(), f"docs/agent/{name} is missing ({purpose})")
            self.assertGreater(len(path.read_text(encoding="utf-8")), 500, f"docs/agent/{name} looks empty")

    def test_doc_links_in_readme_resolve(self) -> None:
        """Every markdown link under docs/ and plugin-development/ in the README resolves."""

        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        targets = {match.group(1) for match in __import__("re").finditer(r"\]\(([^)#]+)\.md\)", readme)}
        for target in targets:
            if not target.startswith(("docs/", "plugin-development/")):
                continue
            self.assertTrue(
                (PROJECT_ROOT / f"{target}.md").is_file(),
                f"README links to a missing document: {target}.md",
            )


if __name__ == "__main__":
    unittest.main()
