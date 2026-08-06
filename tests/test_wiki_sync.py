"""Tests for the GitHub Wiki page generator (scripts/sync_wiki.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sync_wiki_generates_pages_and_rewrites_links(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "sync_wiki.py"), "--output", str(tmp_path / "wiki")],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr

    out = tmp_path / "wiki"
    assert (out / "Home.md").is_file()
    assert (out / "_Sidebar.md").is_file()
    assert (out / "Plugin-Development.md").is_file()
    assert (out / "Changelog.md").is_file()

    # Links to shipped docs are rewritten to their wiki page names...
    home = (out / "Home.md").read_text(encoding="utf-8")
    assert "](Plugin-Development)" in home
    # ...while links to unshipped targets stay untouched.
    assert "docs/VERSION_MATRIX.md" in home
