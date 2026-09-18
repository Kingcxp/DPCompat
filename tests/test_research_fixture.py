"""End-to-end migration of the repository's source-attributed compatibility fixture."""

from __future__ import annotations

import json
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


def test_research_fixture_migrates_to_26_3(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples/research_fixture"
    _, results, _ = compile_pack(source, [resolve_profile("26.3")], tmp_path, universal=False)
    result = results[0]
    assert result.successful, [item.message for item in result.diagnostics]
    archive = result.archive
    assert archive is not None
    with zipfile.ZipFile(archive) as bundle:
        loot_table = json.loads(bundle.read("data/demo/loot_table/helmet.json").decode("utf-8"))
    pool = loot_table["pools"][0]
    assert pool["condition"] == {"type": "minecraft:survives_explosion"}
    assert pool["modifier"] == [{"type": "minecraft:set_count", "count": 1}]
    assert pool["entries"][1]["items"] == "minecraft:trim_templates"
    components = loot_table["components"]
    assert "minecraft:swing_animation" not in components
    assert components["minecraft:attack_animation"] == components["minecraft:interact_animation"]
    assert components["minecraft:pot_decorations"]["left"] == {"id": "minecraft:flow_pottery_sherd"}
