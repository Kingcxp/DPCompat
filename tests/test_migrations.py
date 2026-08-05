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
from dpcompat import nbt
from dpcompat.migrations.base import MigrationContext
from dpcompat.migrations.commands import HorseSaddleSlotRule, SpawnRotationRule
from dpcompat.migrations.entities import EntitySnbtRule
from dpcompat.migrations.identifiers import ChainRenameRule
from dpcompat.migrations.resources import FilteredLootRule, TestEnvironmentClockRule, TimelineClockRule
from dpcompat.migrations.items import ItemTooltipComponentsRule
from dpcompat.migrations.structures import StructureEntityNbtRule
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


class EntitySnbtRuleTests(unittest.TestCase):
    def test_summon_payload_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/test.mcfunction",
                "summon minecraft:zombie ~ ~ ~ "
                '{FallDistance:1.0f,ArmorItems:[{},{},{},{id:"minecraft:diamond_helmet",count:1}]}\n',
            )
            rule = EntitySnbtRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn("fall_distance", text)
            self.assertIn("equipment", text)
            self.assertNotIn("ArmorItems", text)

    def test_data_merge_entity_payload_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/test.mcfunction", "data merge entity @s {FallDistance:2.0f}\n")
            rule = EntitySnbtRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn("fall_distance", text)

    def test_storage_compounds_are_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/test.mcfunction",
                "data modify storage demo:main equipment set value {ArmorItems:[1,2,3]}\n",
            )
            before = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            rule = EntitySnbtRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            after = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_unquoted_macro_entity_nbt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/function/test.mcfunction", "$summon minecraft:zombie ~ ~ ~ $(nbt)\n")
            rule = EntitySnbtRule()
            result = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertEqual(
                {item.code for item in result.diagnostics},
                {"macro-entity-nbt-needs-runtime-parse"},
            )


class StructureNbtRuleTests(unittest.TestCase):
    def _structure(self) -> nbt.NbtDocument:
        entity = nbt.NbtTag(
            nbt.TAG_COMPOUND,
            {
                "id": nbt.NbtTag(nbt.TAG_STRING, "minecraft:zombie"),
                "FallDistance": nbt.NbtTag(nbt.TAG_FLOAT, 2.0),
            },
        )
        entry = nbt.NbtTag(
            nbt.TAG_COMPOUND,
            {
                "pos": nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_DOUBLE, [])),
                "blockPos": nbt.NbtTag(nbt.TAG_INT_ARRAY, [0, 0, 0]),
                "nbt": entity,
            },
        )
        return nbt.NbtDocument(
            "",
            nbt.NbtTag(
                nbt.TAG_COMPOUND,
                {
                    "DataVersion": nbt.NbtTag(nbt.TAG_INT, 0),
                    "size": nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_INT, [])),
                    "palette": nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_COMPOUND, [])),
                    "blocks": nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_COMPOUND, [])),
                    "entities": nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_COMPOUND, [entry])),
                },
            ),
            compressed=True,
        )

    def test_structure_entity_upgrade_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            structure = root / "data/demo/structure/test.nbt"
            structure.parent.mkdir(parents=True, exist_ok=True)
            nbt.dump_path(structure, self._structure())
            rule = StructureEntityNbtRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            document = nbt.load_path(structure)
            root_tag = nbt.compound(document.root)
            assert root_tag is not None
            entries = nbt.list_values(root_tag["entities"], nbt.TAG_COMPOUND)
            assert entries is not None
            entity = nbt.compound(nbt.compound(entries[0])["nbt"])
            assert entity is not None
            self.assertIn("fall_distance", entity)
            self.assertNotIn("FallDistance", entity)

    def test_non_structure_nbt_files_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            other = root / "data/demo/whatever/data.nbt"
            other.parent.mkdir(parents=True, exist_ok=True)
            nbt.dump_path(other, self._structure())
            before = other.read_bytes()
            rule = StructureEntityNbtRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertEqual(other.read_bytes(), before)


class HorseSaddleSlotRuleTests(unittest.TestCase):
    def test_slot_token_is_renamed_in_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text("item replace entity @s horse.saddle with minecraft:saddle\n", encoding="utf-8")
            rule = HorseSaddleSlotRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertIn("item replace entity @s saddle with ", function.read_text(encoding="utf-8"))
            rule.apply(MigrationContext(root, PackFormat(71), PackFormat(61), BuildPolicy()))
            self.assertIn(" horse.saddle ", function.read_text(encoding="utf-8"))

    def test_storage_paths_are_not_substring_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text('data get entity @s horse.saddle\n', encoding="utf-8")
            rule = HorseSaddleSlotRule()
            rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertIn("horse.saddle", function.read_text(encoding="utf-8"))


class ChainRenameRuleTests(unittest.TestCase):
    def test_json_scalars_are_renamed_but_object_keys_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/recipe/test.json",
                '{"type":"minecraft:crafting_shapeless","chain":"minecraft:chain",'
                '"result":{"id":"minecraft:chain","count":1}}\n',
            )
            rule = ChainRenameRule()
            rule.apply(MigrationContext(root, PackFormat(80), PackFormat(88), BuildPolicy()))
            value = (root / "data/demo/recipe/test.json").read_text(encoding="utf-8")
            self.assertIn('"chain": "minecraft:iron_chain"', value)
            self.assertIn('"id": "minecraft:iron_chain"', value)
            self.assertNotIn("minecraft:chain", value)  # the object key "chain" stays

    def test_command_atoms_are_renamed_without_matching_substrings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text("give @s minecraft:chain\ngive @s minecraft:chainmail_helmet\n", encoding="utf-8")
            rule = ChainRenameRule()
            rule.apply(MigrationContext(root, PackFormat(80), PackFormat(88), BuildPolicy()))
            text = function.read_text(encoding="utf-8")
            self.assertIn("minecraft:iron_chain\n", text)
            self.assertIn("minecraft:chainmail_helmet\n", text)

    def test_downgrade_restores_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [88, 0])
            write(root, "data/demo/recipe/test.json", '{"id":"minecraft:iron_chain"}\n')
            rule = ChainRenameRule()
            rule.apply(MigrationContext(root, PackFormat(88), PackFormat(80), BuildPolicy()))
            value = (root / "data/demo/recipe/test.json").read_text(encoding="utf-8")
            self.assertIn("minecraft:chain", value)


class SpawnRotationRuleTests(unittest.TestCase):
    def test_upgrade_adds_zero_pitch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text("spawnpoint @s ~ ~ ~ 90\nsetworldspawn ~ ~ ~ 90\n", encoding="utf-8")
            rule = SpawnRotationRule()
            rule.apply(MigrationContext(root, PackFormat(80), PackFormat(88), BuildPolicy()))
            text = function.read_text(encoding="utf-8")
            self.assertIn("spawnpoint @s ~ ~ ~ 90 0\n", text)
            self.assertIn("setworldspawn ~ ~ ~ 90 0\n", text)

    def test_zero_pitch_downgrades_losslessly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [88, 0])
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text("spawnpoint @s ~ ~ ~ 90 0\n", encoding="utf-8")
            rule = SpawnRotationRule()
            rule.apply(MigrationContext(root, PackFormat(88), PackFormat(80), BuildPolicy()))
            self.assertIn("spawnpoint @s ~ ~ ~ 90\n", function.read_text(encoding="utf-8"))

    def test_nonzero_pitch_downgrade_requires_lossy_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [88, 0])
            function = root / "data/demo/function/test.mcfunction"
            function.parent.mkdir(parents=True, exist_ok=True)
            function.write_text("spawnpoint @s ~ ~ ~ 90 30\n", encoding="utf-8")
            rule = SpawnRotationRule()
            strict = rule.apply(MigrationContext(root, PackFormat(88), PackFormat(80), BuildPolicy()))
            self.assertEqual({item.code for item in strict.diagnostics}, {"spawnpoint-pitch-cannot-downgrade"})
            permitted = rule.apply(
                MigrationContext(root, PackFormat(88), PackFormat(80), BuildPolicy(allow_lossy=True))
            )
            self.assertIn("spawnpoint-pitch-cannot-downgrade", {item.code for item in permitted.diagnostics})
            self.assertTrue(all(item.severity.value < 30 for item in permitted.diagnostics))


class FilteredLootRuleTests(unittest.TestCase):
    def test_modifier_is_renamed_to_on_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/item_modifier/test.json",
                '{"function":"minecraft:filtered","modifier":{"function":"minecraft:set_count","count":2}}\n',
            )
            rule = FilteredLootRule()
            rule.apply(MigrationContext(root, PackFormat(88), PackFormat(94, 1), BuildPolicy()))
            value = (root / "data/demo/item_modifier/test.json").read_text(encoding="utf-8")
            self.assertIn("on_pass", value)
            self.assertNotIn("\"modifier\"", value)

    def test_on_fail_blocks_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [94, 1])
            write(
                root,
                "data/demo/item_modifier/test.json",
                '{"function":"minecraft:filtered","on_pass":{"function":"minecraft:set_count","count":2},'
                '"on_fail":{"function":"minecraft:set_count","count":0}}\n',
            )
            rule = FilteredLootRule()
            result = rule.apply(MigrationContext(root, PackFormat(94, 1), PackFormat(88), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"filtered-on-fail-cannot-downgrade"})

    def test_unrelated_loot_functions_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/loot_table/test.json", '{"function":"minecraft:set_count","count":2}\n')
            before = (root / "data/demo/loot_table/test.json").read_text(encoding="utf-8")
            rule = FilteredLootRule()
            rule.apply(MigrationContext(root, PackFormat(88), PackFormat(94, 1), BuildPolicy()))
            self.assertEqual((root / "data/demo/loot_table/test.json").read_text(encoding="utf-8"), before)


class WorldClockRuleTests(unittest.TestCase):
    def test_timeline_default_clock_is_inserted_on_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/timeline/test.json", '{"tracks":{}}\n')
            rule = TimelineClockRule()
            rule.apply(MigrationContext(root, PackFormat(94, 1), PackFormat(101, 1), BuildPolicy()))
            value = (root / "data/demo/timeline/test.json").read_text(encoding="utf-8")
            self.assertIn('"clock": "minecraft:overworld"', value)

    def test_custom_timeline_clock_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(root, "data/demo/timeline/test.json", '{"clock":"demo:clock","tracks":{}}\n')
            rule = TimelineClockRule()
            result = rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"timeline-custom-clock-cannot-downgrade"})

    def test_test_environment_time_of_day_to_clock_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/test_environment/test.json", '{"time_of_day":6000}\n')
            rule = TestEnvironmentClockRule()
            rule.apply(MigrationContext(root, PackFormat(94, 1), PackFormat(101, 1), BuildPolicy()))
            value = (root / "data/demo/test_environment/test.json").read_text(encoding="utf-8")
            self.assertIn("clock_time", value)
            self.assertNotIn("time_of_day", value)


if __name__ == "__main__":
    unittest.main()
