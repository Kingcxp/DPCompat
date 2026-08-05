"""End-to-end migration of the repository's source-attributed compatibility fixture."""

from __future__ import annotations

import zipfile
from pathlib import Path

from dpcompat.engine import compile_pack
from dpcompat.versions import resolve_profile


def test_research_fixture_builds_across_major_boundaries(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples/research_fixture"
    _, results, universal = compile_pack(
        source,
        [resolve_profile("1.21.4"), resolve_profile("1.21.5"), resolve_profile("1.21.11")],
        tmp_path,
        universal=True,
    )
    assert all(result.successful for result in results)
    assert universal is not None and universal.is_file()
    modern = results[-1].archive
    assert modern is not None
    with zipfile.ZipFile(modern) as archive:
        text = archive.read("data/demo/function/load.mcfunction").decode("utf-8")
    assert "click_event" in text
    assert "minecraft:iron_chain" in text
    assert "gamerule minecraft:raids false" in text
