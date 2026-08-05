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

    def test_reconstruction_guide_contains_every_checkpoint(self) -> None:
        guide = (PROJECT_ROOT / "docs/FROM_ZERO_FILE_BY_FILE.zh-CN.md").read_text(encoding="utf-8")
        missing = [f"C{number:02d}" for number in range(54) if f"## C{number:02d}" not in guide]
        self.assertEqual([], missing, "The reconstruction guide lost a checkpoint")


if __name__ == "__main__":
    unittest.main()
