"""Unit tests for conservative command parsing and the migration rules that build on it.

This file grows with each boundary rule.  The tests are written against the rule
:meth:`apply` methods directly so a failure points at one rule instead of the whole
build pipeline; end-to-end behavior is covered by ``test_build.py`` and
``test_research_fixture.py``.
"""

from __future__ import annotations

import unittest

from dpcompat.commands import (
    is_zero_rotation,
    iter_execute_segments,
    macro_placeholders_are_quoted,
    parse_command_line,
)
from dpcompat.entity_data import downgrade_entity_nbt, upgrade_entity_nbt
from dpcompat.text_components import (
    TextComponentMigrationError,
    downgrade_component,
    upgrade_component,
)


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


if __name__ == "__main__":
    unittest.main()
