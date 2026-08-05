"""Unit tests for conservative command parsing and the migration rules that build on it.

This file grows with each boundary rule.  The tests are written against the rule
:meth:`apply` methods directly so a failure points at one rule instead of the whole
build pipeline; end-to-end behavior is covered by ``test_build.py`` and
``test_research_fixture.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dpcompat.commands import (
    is_zero_rotation,
    iter_execute_segments,
    macro_placeholders_are_quoted,
    parse_command_line,
)
from dpcompat.entity_data import downgrade_entity_nbt, upgrade_entity_nbt
from dpcompat.migrations.base import MigrationContext
from dpcompat.migrations.items import ItemTooltipComponentsRule
from dpcompat.migrations.text import TextComponentRule
from dpcompat.models import BuildPolicy, PackFormat, Severity
from dpcompat.text_components import (
    TextComponentMigrationError,
    downgrade_component,
    upgrade_component,
)

from helpers import make_pack, write


class CommandParserTests(unittest.TestCase):
    def test_tokens_keep_offsets_and_nested_containers(self) -> None:
        line = 'tellraw @s {"text":"x","clickEvent":{"action":"run_command","value":"/say hi"}}'
        parsed = parse_command_line(line)
        self.assertEqual(len(parsed.tokens), 3)
        self.assertEqual(parsed.tokens[0].value, "tellraw")
        self.assertEqual(parsed.tokens[0].start, 0)
        self.assertTrue(parsed.tokens[2].value.startswith("{"))

    def test_quoted_and_bracket_content_stays_in_one_token(self) -> None:
        line = 'summon minecraft:zombie ~ ~ ~ {FallDistance:1.0f,ArmorItems:[{},{},{}]}'
        parsed = parse_command_line(line)
        self.assertEqual([token.value for token in parsed.tokens][:2], ["summon", "minecraft:zombie"])
        self.assertEqual(parsed.tokens[-1].value, "{FallDistance:1.0f,ArmorItems:[{},{},{}]}")

    def test_execute_segments_split_on_run(self) -> None:
        line = "execute if score a b matches 1 run say hi"
        parsed = parse_command_line(line)
        segments = iter_execute_segments(parsed)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][0].value, "execute")
        self.assertEqual(segments[1][0].value, "say")

    def test_macro_flags(self) -> None:
        self.assertTrue(parse_command_line("$tellraw @s $(message)").macro)
        self.assertFalse(parse_command_line("tellraw @s $(message)").macro)

    def test_macro_placeholders_inside_quoted_scalars_are_safe(self) -> None:
        self.assertTrue(macro_placeholders_are_quoted('{"text":"$(label)"}'))
        self.assertFalse(macro_placeholders_are_quoted("$(component)"))

    def test_rotation_helpers(self) -> None:
        self.assertTrue(is_zero_rotation("0"))
        self.assertTrue(is_zero_rotation("0.0f"))
        self.assertFalse(is_zero_rotation("30"))


class TextComponentTests(unittest.TestCase):
    def test_click_event_upgrade_maps_action_specific_value(self) -> None:
        value = upgrade_component(
            {"text": "x", "clickEvent": {"action": "run_command", "value": "/say hi"}}
        )
        self.assertEqual(
            value,
            {"text": "x", "click_event": {"action": "run_command", "command": "/say hi"}},
        )

    def test_click_event_downgrade_restores_legacy_value(self) -> None:
        value = downgrade_component(
            {"text": "x", "click_event": {"action": "run_command", "command": "/say hi"}}
        )
        self.assertEqual(
            value,
            {"text": "x", "clickEvent": {"action": "run_command", "value": "/say hi"}},
        )

    def test_hover_show_item_round_trip(self) -> None:
        modern = {"hover_event": {"action": "show_item", "id": "minecraft:stick", "count": 2}}
        legacy = downgrade_component(modern)
        self.assertEqual(
            legacy,
            {"hoverEvent": {"action": "show_item", "contents": {"id": "minecraft:stick", "count": 2}}},
        )
        self.assertEqual(upgrade_component(legacy), modern)

    def test_duplicate_event_forms_fail_closed(self) -> None:
        with self.assertRaises(TextComponentMigrationError):
            upgrade_component({"clickEvent": {"action": "run_command"}, "click_event": {"action": "open_url"}})  # type: ignore[call-overload]


class EntityDataTests(unittest.TestCase):
    def test_equipment_upgrade_merges_legacy_lists(self) -> None:
        result = upgrade_entity_nbt(
            "minecraft:zombie",
            {
                "FallDistance": 1.0,
                "ArmorItems": [{}, {}, {}, {"id": "minecraft:diamond_helmet", "count": 1}],
                "HandItems": [{"id": "minecraft:stick", "count": 1}, {}],
            },
        )
        self.assertIn("fall_distance", result.value)
        self.assertNotIn("ArmorItems", result.value)
        equipment = result.value["equipment"]
        self.assertEqual(equipment["head"], {"id": "minecraft:diamond_helmet", "count": 1})
        self.assertEqual(equipment["mainhand"], {"id": "minecraft:stick", "count": 1})
        self.assertEqual(result.changed, 3)

    def test_equipment_downgrade_reconstructs_lists_and_flags_pig_saddle_loss(self) -> None:
        result = downgrade_entity_nbt(
            "minecraft:pig",
            {
                "equipment": {
                    "head": {"id": "minecraft:carved_pumpkin", "count": 1},
                    "saddle": {"id": "minecraft:saddle", "count": 1},
                }
            },
        )
        self.assertEqual(result.value["ArmorItems"][3], {"id": "minecraft:carved_pumpkin", "count": 1})
        self.assertEqual(result.value["Saddle"], True)
        self.assertTrue(any("Saddle item components are lost" in warning for warning in result.warnings))

    def test_item_frame_position_upgrade(self) -> None:
        result = upgrade_entity_nbt(
            "minecraft:item_frame",
            {"TileX": 1, "TileY": 2, "TileZ": 3},
        )
        self.assertIn("block_pos", result.value)
        self.assertNotIn("TileX", result.value)

    def test_player_respawn_upgrade(self) -> None:
        result = upgrade_entity_nbt(
            "minecraft:player",
            {"SpawnX": 1, "SpawnY": 2, "SpawnZ": 3, "SpawnForced": True},
        )
        self.assertEqual(result.value["respawn"]["forced"], True)

    def test_unknown_entity_id_is_ignored_not_guessed(self) -> None:
        result = upgrade_entity_nbt("minecraft:custom_thing", {"TileX": 1})
        self.assertEqual(result.value, {"TileX": 1})
        self.assertEqual(result.changed, 0)


class TextComponentRuleTests(unittest.TestCase):
    """Exercise the rule through a real MigrationContext without the engine."""

    def _run(self, root: Path, source: int, target: int) -> list:
        rule = TextComponentRule()
        result = rule.apply(MigrationContext(root, PackFormat(source), PackFormat(target), BuildPolicy()))
        return result.diagnostics

    def test_tellraw_json_upgrade_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/test.mcfunction",
                'tellraw @s {"text":"x","clickEvent":{"action":"run_command","value":"/say hi"}}\n',
            )
            diagnostics = self._run(root, 61, 71)
            self.assertEqual([item.severity for item in diagnostics], [])
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn("click_event", text)
            self.assertIn("command", text)

    def test_known_json_resource_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/item_modifier/test.json",
                '{"function":"minecraft:set_name","name":'
                '{"text":"x","hoverEvent":{"action":"show_text","value":"tooltip"}}}\n',
            )
            self._run(root, 61, 71)
            value = (root / "data/demo/item_modifier/test.json").read_text(encoding="utf-8")
            self.assertIn("hover_event", value)

    def test_unquoted_macro_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/test.mcfunction", "$tellraw @s $(component)\n")
            diagnostics = self._run(root, 61, 71)
            self.assertEqual(
                {item.code for item in diagnostics},
                {"macro-component-needs-runtime-parse"},
            )
            self.assertTrue(all(item.severity == Severity.ERROR for item in diagnostics))


class ItemTooltipRuleTests(unittest.TestCase):
    def test_local_show_in_tooltip_is_consolidated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/loot_table/test.json",
                '{"pools":[],"components":'
                '{"minecraft:dyed_color":{"rgb":123,"show_in_tooltip":false}}}\n',
            )
            rule = ItemTooltipComponentsRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            value = (root / "data/demo/loot_table/test.json").read_text(encoding="utf-8")
            self.assertIn("tooltip_display", value)
            self.assertIn("hidden_components", value)
            self.assertNotIn("show_in_tooltip", value)

    def test_downgrade_restores_local_flags_when_possible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), 71)
            write(
                root,
                "data/demo/loot_table/test.json",
                '{"pools":[],"components":'
                '{"minecraft:tooltip_display":{"hide_tooltip":true,"hidden_components":["minecraft:dyed_color"]},'
                '"minecraft:dyed_color":{"rgb":123}}}\n',
            )
            rule = ItemTooltipComponentsRule()
            rule.apply(MigrationContext(root, PackFormat(71), PackFormat(61), BuildPolicy()))
            value = (root / "data/demo/loot_table/test.json").read_text(encoding="utf-8")
            self.assertIn("minecraft:hide_tooltip", value)
            self.assertIn("show_in_tooltip", value)
            self.assertNotIn("tooltip_display", value)


if __name__ == "__main__":
    unittest.main()
