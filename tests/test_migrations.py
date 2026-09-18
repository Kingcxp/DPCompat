"""Unit tests for conservative command parsing and the migration rules that build on it.

This file grows with each boundary rule.  The tests are written against the rule
:meth:`apply` methods directly so a failure points at one rule instead of the whole
build pipeline; end-to-end behavior is covered by ``test_build.py`` and
``test_research_fixture.py``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dpcompat import nbt
from dpcompat.commands import (
    is_zero_rotation,
    iter_execute_segments,
    macro_placeholders_are_quoted,
    parse_command_line,
)
from dpcompat.entity_data import downgrade_entity_nbt, upgrade_entity_nbt
from dpcompat.fallback import apply_fallback_files, load_fallback, resolve_with_fallback
from dpcompat.migrations import BUILTIN_RULES
from dpcompat.migrations.base import MigrationContext
from dpcompat.migrations.commands import HorseSaddleSlotRule, SpawnRotationRule
from dpcompat.migrations.entities import EntitySnbtRule
from dpcompat.migrations.identifiers import ChainRenameRule
from dpcompat.migrations.items import ItemTooltipComponentsRule
from dpcompat.migrations.recipes import Recipe26Rule, TimeCheckClockRule
from dpcompat.migrations.resources import FilteredLootRule, TestEnvironmentClockRule, TimelineClockRule
from dpcompat.migrations.structures import StructureEntityNbtRule
from dpcompat.migrations.text import TextComponentRule
from dpcompat.migrations.wilderness import (
    BedRuleFieldsRule,
    BlockEntitySherdsNbtRule,
    BlockEntitySherdsSnbtRule,
    LootSchemaKeysRule,
    MapColorRemovalRule,
    NumberProviderSumRule,
    PotDecorationsFacesRule,
    SwingAnimationSplitRule,
    TrimMaterialPaletteRule,
    WorldgenSchemaRule,
)
from dpcompat.models import BuildPolicy, Compatibility, PackFormat, Severity
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
        line = "summon minecraft:zombie ~ ~ ~ {FallDistance:1.0f,ArmorItems:[{},{},{}]}"
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
        value = upgrade_component({"text": "x", "clickEvent": {"action": "run_command", "value": "/say hi"}})
        self.assertEqual(
            value,
            {"text": "x", "click_event": {"action": "run_command", "command": "/say hi"}},
        )

    def test_click_event_downgrade_restores_legacy_value(self) -> None:
        value = downgrade_component({"text": "x", "click_event": {"action": "run_command", "command": "/say hi"}})
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

    def test_custom_click_action_cannot_downgrade(self) -> None:
        with self.assertRaises(TextComponentMigrationError):
            downgrade_component({"click_event": {"action": "custom", "id": "minecraft:boom"}})

    def test_show_dialog_click_action_cannot_downgrade(self) -> None:
        with self.assertRaises(TextComponentMigrationError):
            downgrade_component({"click_event": {"action": "show_dialog", "dialog": "minecraft:info"}})


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

    def test_painting_and_leash_knot_positions_use_the_same_rename(self) -> None:
        # 1.21.5 moved TileX/Y/Z into block_pos for painting and leash_knot as well.
        for entity_id in ("minecraft:painting", "minecraft:leash_knot"):
            result = upgrade_entity_nbt(entity_id, {"TileX": 4, "TileY": 5, "TileZ": 6})
            self.assertEqual(result.value["block_pos"].values, (4, 5, 6))
            self.assertNotIn("TileX", result.value)
            downgraded = downgrade_entity_nbt(entity_id, result.value)
            self.assertEqual(
                (downgraded.value["TileX"], downgraded.value["TileY"], downgraded.value["TileZ"]),
                (4, 5, 6),
            )
            self.assertNotIn("block_pos", downgraded.value)

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

    def test_custom_click_action_json_downgrade_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [80, 0])
            raw = (
                '{"function":"minecraft:set_name","entity":"this","name":'
                '{"text":"x","click_event":{"action":"custom","id":"minecraft:boom"}}}\n'
            )
            write(root, "data/demo/item_modifier/test.json", raw)
            diagnostics = self._run(root, 80, 61)
            self.assertEqual(
                {item.code for item in diagnostics},
                {"text-component-json-failed"},
            )
            self.assertEqual({item.compatibility for item in diagnostics}, {Compatibility.UNSUPPORTED})
            # The unrepresentable file is left untouched instead of half-migrated.
            self.assertEqual((root / "data/demo/item_modifier/test.json").read_text(encoding="utf-8"), raw)


class ItemTooltipRuleTests(unittest.TestCase):
    def test_local_show_in_tooltip_is_consolidated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/loot_table/test.json",
                '{"pools":[],"components":{"minecraft:dyed_color":{"rgb":123,"show_in_tooltip":false}}}\n',
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

    def test_removed_hide_additional_tooltip_is_diagnosed_not_silent(self) -> None:
        # hide_additional_tooltip was removed in 1.21.5; its hidden_components
        # replacement depends on co-present components and cannot be inferred.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/loot_table/test.json",
                '{"pools":[],"components":{"minecraft:hide_additional_tooltip":{}}}\n',
            )
            rule = ItemTooltipComponentsRule()
            result = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"hide-additional-tooltip-cannot-upgrade"})
            self.assertEqual({item.compatibility for item in result.diagnostics}, {Compatibility.UNKNOWN})


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
            entry = nbt.compound(entries[0])
            assert entry is not None
            entity = nbt.compound(entry["nbt"])
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
            function.write_text("data get entity @s horse.saddle\n", encoding="utf-8")
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
            self.assertNotIn('"modifier"', value)

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


class RecipeRuleTests(unittest.TestCase):
    def test_cooking_result_object_downgrades_when_count_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/recipe/test.json",
                '{"type":"minecraft:smelting","ingredient":"minecraft:stone",'
                '"result":{"id":"minecraft:stone","count":1},"experience":0,"cookingtime":200}\n',
            )
            rule = Recipe26Rule()
            rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            value = (root / "data/demo/recipe/test.json").read_text(encoding="utf-8")
            self.assertIn('"result": "minecraft:stone"', value)

    def test_show_notification_true_is_removed_on_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/recipe/test.json",
                '{"type":"minecraft:smelting","ingredient":"minecraft:stone","result":"minecraft:stone",'
                '"show_notification":true}\n',
            )
            rule = Recipe26Rule()
            rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            value = (root / "data/demo/recipe/test.json").read_text(encoding="utf-8")
            self.assertNotIn("show_notification", value)

    def test_new_recipe_types_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(root, "data/demo/recipe/test.json", '{"type":"minecraft:crafting_dye","ingredients":[]}\n')
            rule = Recipe26Rule()
            result = rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"new-recipe-type-cannot-downgrade"})

    def test_cooking_result_extra_fields_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/recipe/test.json",
                '{"type":"minecraft:smelting","ingredient":"minecraft:stone",'
                '"result":{"id":"minecraft:stone","components":{"minecraft:custom_data":{"x":1}}},'
                '"experience":0,"cookingtime":200}\n',
            )
            rule = Recipe26Rule()
            result = rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"cooking-result-fields-cannot-downgrade"})
            self.assertEqual({item.compatibility for item in result.diagnostics}, {Compatibility.UNKNOWN})

    def test_time_check_clock_default_is_inserted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(root, "data/demo/predicate/test.json", '{"condition":"minecraft:time_check","value":1000}\n')
            rule = TimeCheckClockRule()
            rule.apply(MigrationContext(root, PackFormat(94, 1), PackFormat(101, 1), BuildPolicy()))
            value = (root / "data/demo/predicate/test.json").read_text(encoding="utf-8")
            self.assertIn('"clock": "minecraft:overworld"', value)

    def test_custom_time_check_clock_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/predicate/test.json",
                '{"condition":"minecraft:time_check","value":1000,"clock":"demo:clock"}\n',
            )
            rule = TimeCheckClockRule()
            result = rule.apply(MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy()))
            self.assertEqual({item.code for item in result.diagnostics}, {"time-check-custom-clock-cannot-downgrade"})


class FallbackTests(unittest.TestCase):
    def _fallback_dir(self, base: Path, *, manifest: str | None = None) -> Path:
        fallback = base / "fallback"
        fallback.mkdir(exist_ok=True)
        if manifest is not None:
            (fallback / ".dpcompat-fallback.toml").write_text(manifest, encoding="utf-8")
        return fallback

    def test_exact_deletion_and_overlay_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            spec = load_fallback(
                self._fallback_dir(
                    base,
                    manifest='delete = ["data/demo/timeline/test.json"]\n',
                )
            )
            write(spec.root, "data/demo/function/legacy.mcfunction", "say legacy fallback\n")
            target = make_pack(base / "target")
            write(target, "data/demo/timeline/test.json", "{}\n")
            application = apply_fallback_files(spec, target)
            self.assertFalse((target / "data/demo/timeline/test.json").exists())
            self.assertEqual(
                (target / "data/demo/function/legacy.mcfunction").read_text(encoding="utf-8"),
                "say legacy fallback\n",
            )
            self.assertEqual(application.deleted_paths, 1)
            self.assertEqual(application.changed_files, 1)

    def test_diagnostic_resolution_requires_code_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            spec = load_fallback(
                self._fallback_dir(
                    Path(temp_dir),
                    manifest=(
                        "[[resolve]]\n"
                        'code = "timeline-custom-clock-cannot-downgrade"\n'
                        'path = "data/demo/timeline/test.json"\n'
                        'reason = "The old target intentionally omits the cosmetic custom timeline."\n'
                    ),
                )
            )
            from dpcompat.models import Compatibility, Diagnostic, Severity

            diagnostics = [
                Diagnostic(
                    Severity.ERROR,
                    "timeline-custom-clock-cannot-downgrade",
                    "Custom clock",
                    path="data/demo/timeline/test.json",
                    compatibility=Compatibility.UNSUPPORTED,
                )
            ]
            application = apply_fallback_files(spec, make_pack(Path(temp_dir) / "target"))
            resolve_with_fallback(diagnostics, spec, application)
            self.assertEqual(application.resolved_diagnostics, 1)
            self.assertEqual(diagnostics[0].severity, Severity.INFO)
            self.assertEqual(diagnostics[0].compatibility, Compatibility.EMULATED)

    def test_unused_resolution_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            spec = load_fallback(
                self._fallback_dir(
                    Path(temp_dir),
                    manifest=('[[resolve]]\ncode = "never-emitted-code"\nreason = "Reviewed for future use."\n'),
                )
            )
            application = apply_fallback_files(spec, make_pack(Path(temp_dir) / "target"))
            resolve_with_fallback([], spec, application)
            self.assertEqual(
                {item.code for item in application.diagnostics},
                {"fallback-resolution-unused"},
            )


class MacroAndNestedEntityTextTests(unittest.TestCase):
    """Regressions for quoted scalar macros and recursive entity text components."""

    def _snbt_quoted_json(self, payload: dict) -> str:
        """Serialize a dict the way SNBT quotes an embedded JSON component string."""

        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return json.dumps(compact, ensure_ascii=False)

    def test_quoted_macro_preserves_static_component_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/test.mcfunction",
                '$tellraw @s {"text":"$(label)","clickEvent":'
                '{"action":"suggest_command","value":"/tp @s $(x) $(y) $(z)"}}\n',
            )
            rule = TextComponentRule()
            diagnostics = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy())).diagnostics
            self.assertEqual(diagnostics, [])
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn("click_event:", text)
            self.assertIn('command:"/tp @s $(x) $(y) $(z)"', text)
            self.assertIn('text:"$(label)"', text)

    def test_nested_passenger_entity_text_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            custom_name = self._snbt_quoted_json({"text": "root"})
            display_text = self._snbt_quoted_json({"text": "$(damage)", "bold": True})
            write(
                root,
                "data/demo/function/test.mcfunction",
                f"$summon item ~ ~ ~ {{CustomName:{custom_name},"
                f'Passengers:[{{id:"text_display",text:{display_text}}}]}}\n',
            )
            rule = EntitySnbtRule()
            diagnostics = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy())).diagnostics
            self.assertEqual(diagnostics, [])
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn("CustomName:{text:root}", text)
            self.assertIn('text:{text:"$(damage)",bold:true}', text)

    def test_nested_passenger_entity_text_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), 71)
            write(
                root,
                "data/demo/function/test.mcfunction",
                'summon item ~ ~ ~ {CustomName:{text:"root"},'
                'Passengers:[{id:"text_display",text:{text:"damage",bold:true}}]}\n',
            )
            rule = EntitySnbtRule()
            diagnostics = rule.apply(MigrationContext(root, PackFormat(71), PackFormat(61), BuildPolicy())).diagnostics
            self.assertEqual(diagnostics, [])
            text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
            self.assertIn(f"CustomName:{self._snbt_quoted_json({'text': 'root'})}", text)
            self.assertIn(f"text:{self._snbt_quoted_json({'text': 'damage', 'bold': True})}", text)

    def test_unparseable_embedded_text_is_an_unknown_not_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/test.mcfunction",
                'summon item ~ ~ ~ {CustomName:"not-json-at-all"}\n',
            )
            rule = EntitySnbtRule()
            result = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertEqual(
                {item.code for item in result.diagnostics},
                {"entity-text-component-unknown"},
            )
            self.assertTrue(all(item.compatibility == Compatibility.UNKNOWN for item in result.diagnostics))


class AuditRegressionTests(unittest.TestCase):
    """Regression coverage for defects found by the 2026-09 code audit."""

    def test_summon_prefers_a_literal_compound_over_a_position_macro(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir))
            write(
                root,
                "data/demo/function/load.mcfunction",
                "summon minecraft:zombie $(pos) {FallDistance:1.0f}\n",
            )
            result = EntitySnbtRule().apply(MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy()))
            self.assertEqual([item.code for item in result.diagnostics], [])
            self.assertIn(
                "fall_distance",
                (root / "data/demo/function/load.mcfunction").read_text(encoding="utf-8"),
            )

    def test_non_list_armor_items_is_reported_instead_of_vanishing(self) -> None:
        result = upgrade_entity_nbt(
            "minecraft:zombie",
            {"ArmorItems": {"head": {"id": "minecraft:stone", "count": 1}}},
        )
        self.assertTrue(any("ArmorItems" in warning for warning in result.warnings))
        self.assertNotIn("ArmorItems", result.value)

    def test_non_compound_equipment_is_reported_on_downgrade(self) -> None:
        result = downgrade_entity_nbt("minecraft:zombie", {"equipment": ["minecraft:stone"]})
        self.assertTrue(any("equipment" in warning for warning in result.warnings))

    def test_non_object_tooltip_display_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [71, 0])
            write(
                root,
                "data/demo/item_modifier/tooltip.json",
                '{"components":{"minecraft:tooltip_display":true}}\n',
            )
            result = ItemTooltipComponentsRule().apply(
                MigrationContext(root, PackFormat(71), PackFormat(61), BuildPolicy())
            )
            self.assertEqual({item.code for item in result.diagnostics}, {"tooltip-display-not-an-object"})

    def test_empty_time_markers_are_removed_without_a_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(root, "data/demo/timeline/test.json", '{"clock":"minecraft:overworld","time_markers":[]}\n')
            result = TimelineClockRule().apply(
                MigrationContext(root, PackFormat(101, 1), PackFormat(94, 1), BuildPolicy())
            )
            self.assertEqual([item.code for item in result.diagnostics], [])
            self.assertNotIn("time_markers", (root / "data/demo/timeline/test.json").read_text(encoding="utf-8"))

    def test_conflicting_test_environment_clocks_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [94, 1])
            write(
                root,
                "data/demo/test_environment/test.json",
                '{"time_of_day":6000,"clock_time":{"clock":"minecraft:overworld","time":1000}}\n',
            )
            result = TestEnvironmentClockRule().apply(
                MigrationContext(root, PackFormat(94, 1), PackFormat(101, 1), BuildPolicy())
            )
            self.assertEqual({item.code for item in result.diagnostics}, {"test-environment-clock-conflict"})
            text = (root / "data/demo/test_environment/test.json").read_text(encoding="utf-8")
            self.assertNotIn("time_of_day", text)
            self.assertIn("clock_time", text)


class WildernessBoundRuleTests(unittest.TestCase):
    """26.3 (format 121.0) boundary rules."""

    def _run(self, root: Path, rule: object, source: int | list[int], target: int | list[int]) -> list[Any]:
        result = rule.apply(  # type: ignore[attr-defined]
            MigrationContext(root, PackFormat.parse(source), PackFormat.parse(target), BuildPolicy())
        )
        return list(result.diagnostics)

    def _text(self, root: Path, relative: str) -> str:
        return (root / relative).read_text(encoding="utf-8")

    def test_swing_animation_splits_on_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/swing.json",
                '{"components":{"minecraft:swing_animation":{"type":"stab","duration":4}}}\n',
            )
            self._run(root, SwingAnimationSplitRule(), [107, 1], [121, 0])
            value = json.loads(self._text(root, "data/demo/item_modifier/swing.json"))
            components = value["components"]
            self.assertNotIn("minecraft:swing_animation", components)
            self.assertEqual(components["minecraft:attack_animation"], {"type": "stab", "duration": 4})
            self.assertEqual(components["minecraft:interact_animation"], {"type": "stab", "duration": 4})

    def test_swing_animation_split_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/swing.json",
                '{"components":{"minecraft:swing_animation":{"type":"whack"}}}\n',
            )
            rule = SwingAnimationSplitRule()
            self._run(root, rule, [107, 1], [121, 0])
            once = self._text(root, "data/demo/item_modifier/swing.json")
            self._run(root, rule, [107, 1], [121, 0])
            self.assertEqual(self._text(root, "data/demo/item_modifier/swing.json"), once)

    def test_swing_animation_split_conflict_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/swing.json",
                '{"components":{"minecraft:swing_animation":{"type":"whack"},'
                '"minecraft:attack_animation":{"type":"stab"}}}\n',
            )
            diagnostics = self._run(root, SwingAnimationSplitRule(), [107, 1], [121, 0])
            self.assertEqual({item.code for item in diagnostics}, {"swing-animation-split-conflict"})

    def test_animation_components_merge_when_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/item_modifier/swing.json",
                '{"components":{"minecraft:attack_animation":{"type":"stab","duration":4},'
                '"minecraft:interact_animation":{"type":"stab","duration":4}}}\n',
            )
            self._run(root, SwingAnimationSplitRule(), [121, 0], [107, 1])
            components = json.loads(self._text(root, "data/demo/item_modifier/swing.json"))["components"]
            self.assertEqual(components, {"minecraft:swing_animation": {"type": "stab", "duration": 4}})

    def test_animation_components_differing_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/item_modifier/swing.json",
                '{"components":{"minecraft:attack_animation":{"type":"stab"},'
                '"minecraft:interact_animation":{"type":"whack"}}}\n',
            )
            diagnostics = self._run(root, SwingAnimationSplitRule(), [121, 0], [107, 1])
            self.assertEqual({item.code for item in diagnostics}, {"animation-components-cannot-downgrade"})

    def test_map_color_is_dropped_on_upgrade_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/map.json",
                '{"components":{"minecraft:map_color":12345,"minecraft:custom_name":"x"}}\n',
            )
            rule = MapColorRemovalRule()
            self._run(root, rule, [107, 1], [121, 0])
            components = json.loads(self._text(root, "data/demo/item_modifier/map.json"))["components"]
            self.assertEqual(components, {"minecraft:custom_name": "x"})
            self._run(root, rule, [121, 0], [107, 1])
            self.assertEqual(
                json.loads(self._text(root, "data/demo/item_modifier/map.json"))["components"],
                {"minecraft:custom_name": "x"},
            )

    def test_pot_decorations_list_uses_documented_face_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/pot.json",
                '{"components":{"minecraft:pot_decorations":["minecraft:brick","minecraft:angler_pottery_sherd"]}}\n',
            )
            self._run(root, PotDecorationsFacesRule(), [107, 1], [121, 0])
            faces = json.loads(self._text(root, "data/demo/item_modifier/pot.json"))["components"][
                "minecraft:pot_decorations"
            ]
            self.assertEqual(
                faces,
                {
                    "back": {"id": "minecraft:brick"},
                    "left": {"id": "minecraft:angler_pottery_sherd"},
                    "right": {"id": "minecraft:brick"},
                    "front": {"id": "minecraft:brick"},
                },
            )

    def test_pot_decorations_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            original = (
                '{"components":{"minecraft:pot_decorations":'
                '["minecraft:brick","minecraft:flow_pottery_sherd","minecraft:brick",'
                '"minecraft:guster_pottery_sherd"]}}\n'
            )
            write(root, "data/demo/item_modifier/pot.json", original)
            rule = PotDecorationsFacesRule()
            self._run(root, rule, [107, 1], [121, 0])
            self._run(root, rule, [121, 0], [107, 1])
            self.assertEqual(json.loads(self._text(root, "data/demo/item_modifier/pot.json")), json.loads(original))

    def test_pot_decorations_empty_face_is_lossy_on_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/item_modifier/pot.json",
                '{"components":{"minecraft:pot_decorations":{"front":{"id":"minecraft:angler_pottery_sherd"}}}}\n',
            )
            diagnostics = self._run(root, PotDecorationsFacesRule(), [121, 0], [107, 1])
            self.assertEqual({item.code for item in diagnostics}, {"pot-decorations-empty-face"})
            entries = json.loads(self._text(root, "data/demo/item_modifier/pot.json"))["components"][
                "minecraft:pot_decorations"
            ]
            self.assertEqual(
                entries,
                [
                    "minecraft:brick",
                    "minecraft:brick",
                    "minecraft:brick",
                    "minecraft:angler_pottery_sherd",
                ],
            )

    def test_pot_decorations_item_data_cannot_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/item_modifier/pot.json",
                json.dumps(
                    {
                        "components": {
                            "minecraft:pot_decorations": {
                                "back": {"id": "minecraft:brick"},
                                "left": {"id": "minecraft:brick"},
                                "right": {"id": "minecraft:brick"},
                                "front": {"id": "minecraft:brick", "count": 2},
                            }
                        }
                    }
                ),
            )
            diagnostics = self._run(root, PotDecorationsFacesRule(), [121, 0], [107, 1])
            self.assertEqual({item.code for item in diagnostics}, {"pot-decorations-face-data-cannot-downgrade"})

    def test_bed_rule_field_is_renamed_both_ways(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/dimension_type/test.json",
                '{"attributes":{"minecraft:gameplay/bed_rule":{"can_sleep":"always","explodes":true}}}\n',
            )
            rule = BedRuleFieldsRule()
            self._run(root, rule, [107, 1], [121, 0])
            rule_value = json.loads(self._text(root, "data/demo/dimension_type/test.json"))["attributes"][
                "minecraft:gameplay/bed_rule"
            ]
            self.assertEqual(rule_value, {"can_sleep": "always", "destroy_on_use": True})
            self._run(root, rule, [121, 0], [107, 1])
            rule_value = json.loads(self._text(root, "data/demo/dimension_type/test.json"))["attributes"][
                "minecraft:gameplay/bed_rule"
            ]
            self.assertEqual(rule_value, {"can_sleep": "always", "explodes": True})

    def test_bed_rule_destroy_on_leave_blocks_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/dimension_type/test.json",
                '{"attributes":{"minecraft:gameplay/bed_rule":{"destroy_on_use":true,"destroy_on_leave":true}}}\n',
            )
            diagnostics = self._run(root, BedRuleFieldsRule(), [121, 0], [107, 1])
            self.assertEqual({item.code for item in diagnostics}, {"bed-rule-destroy-on-leave-cannot-downgrade"})

    def test_trim_material_asset_field_is_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/trim_material/quartz.json",
                '{"asset_name":"minecraft:quartz","description":"x"}\n',
            )
            rule = TrimMaterialPaletteRule()
            self._run(root, rule, [107, 1], [121, 0])
            value = json.loads(self._text(root, "data/demo/trim_material/quartz.json"))
            self.assertEqual(value, {"palette_id": "minecraft:quartz", "description": "x"})
            self._run(root, rule, [121, 0], [107, 1])
            self.assertEqual(
                json.loads(self._text(root, "data/demo/trim_material/quartz.json")),
                {"asset_name": "minecraft:quartz", "description": "x"},
            )

    def test_trim_material_overrides_block_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/trim_material/quartz.json",
                '{"asset_name":"minecraft:quartz","override_armor_assets":{"minecraft:iron":"x"}}\n',
            )
            diagnostics = self._run(root, TrimMaterialPaletteRule(), [107, 1], [121, 0])
            self.assertEqual({item.code for item in diagnostics}, {"trim-material-overrides-cannot-upgrade"})

    def test_loot_table_keys_upgrade_to_121(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/loot_table/blocks/stone.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "pools": [
                            {
                                "rolls": 1,
                                "conditions": [{"condition": "minecraft:survives_explosion"}],
                                "functions": [{"function": "minecraft:set_count", "count": 1}],
                                "entries": [
                                    {
                                        "type": "minecraft:item",
                                        "name": "minecraft:stone",
                                        "conditions": [
                                            {"condition": "minecraft:survives_explosion"},
                                            {"condition": "minecraft:random_chance", "chance": 0.5},
                                        ],
                                    },
                                    {"type": "minecraft:tag", "name": "minecraft:stone_buttons", "expand": True},
                                ],
                            }
                        ],
                    }
                ),
            )
            self._run(root, LootSchemaKeysRule(), [107, 1], [121, 0])
            table = json.loads(self._text(root, "data/demo/loot_table/blocks/stone.json"))
            pool = table["pools"][0]
            self.assertEqual(pool["condition"], {"type": "minecraft:survives_explosion"})
            self.assertEqual(pool["modifier"], [{"type": "minecraft:set_count", "count": 1}])
            entry = pool["entries"][0]
            self.assertEqual(
                entry["condition"],
                {
                    "type": "minecraft:all_of",
                    "terms": [
                        {"type": "minecraft:survives_explosion"},
                        {"type": "minecraft:random_chance", "chance": 0.5},
                    ],
                },
            )
            self.assertEqual(
                pool["entries"][1], {"type": "minecraft:tag", "items": "minecraft:stone_buttons", "expand": True}
            )

    def test_loot_table_keys_downgrade_from_121(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/loot_table/blocks/stone.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "pools": [
                            {
                                "rolls": 1,
                                "condition": "minecraft:tool/can_silk_touch",
                                "modifier": {"type": "minecraft:set_count", "count": 1},
                                "entries": [
                                    {"type": "minecraft:item", "name": "minecraft:stone"},
                                    {"type": "minecraft:tag", "items": "minecraft:stone_buttons"},
                                ],
                            }
                        ],
                    }
                ),
            )
            self._run(root, LootSchemaKeysRule(), [121, 0], [107, 1])
            pool = json.loads(self._text(root, "data/demo/loot_table/blocks/stone.json"))["pools"][0]
            self.assertEqual(
                pool["conditions"],
                [{"condition": "minecraft:reference", "name": "minecraft:tool/can_silk_touch"}],
            )
            self.assertEqual(pool["functions"], [{"function": "minecraft:set_count", "count": 1}])
            self.assertEqual(pool["entries"][1], {"type": "minecraft:tag", "name": "minecraft:stone_buttons"})

    def test_loot_table_level_functions_become_a_modifier(self) -> None:
        # A loot table may carry its own functions next to its pools; the vanilla data pack
        # uses this for minecraft:explosion_decay.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/loot_table/blocks/beetroots.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "functions": [{"function": "minecraft:explosion_decay"}],
                        "pools": [],
                    }
                ),
            )
            rule = LootSchemaKeysRule()
            self._run(root, rule, [107, 1], [121, 0])
            table = json.loads(self._text(root, "data/demo/loot_table/blocks/beetroots.json"))
            self.assertEqual(table["modifier"], [{"type": "minecraft:explosion_decay"}])
            self.assertNotIn("functions", table)
            self._run(root, rule, [121, 0], [107, 1])
            table = json.loads(self._text(root, "data/demo/loot_table/blocks/beetroots.json"))
            self.assertEqual(table["functions"], [{"function": "minecraft:explosion_decay"}])

    def test_inline_loot_table_entry_is_migrated(self) -> None:
        # A minecraft:loot_table pool entry may embed a whole loot table in its value field.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/loot_table/equipment/trial.json",
                json.dumps(
                    {
                        "type": "minecraft:equipment",
                        "pools": [
                            {
                                "rolls": 1,
                                "entries": [
                                    {
                                        "type": "minecraft:loot_table",
                                        "value": {
                                            "pools": [
                                                {
                                                    "rolls": 1,
                                                    "conditions": [{"condition": "minecraft:random_chance"}],
                                                    "entries": [{"type": "minecraft:item", "name": "minecraft:stick"}],
                                                }
                                            ]
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ),
            )
            rule = LootSchemaKeysRule()
            self._run(root, rule, [107, 1], [121, 0])
            table = json.loads(self._text(root, "data/demo/loot_table/equipment/trial.json"))
            inner = table["pools"][0]["entries"][0]["value"]["pools"][0]
            self.assertEqual(inner["condition"], {"type": "minecraft:random_chance"})
            self.assertNotIn("conditions", inner)
            self._run(root, rule, [121, 0], [107, 1])
            inner = json.loads(self._text(root, "data/demo/loot_table/equipment/trial.json"))["pools"][0]["entries"][0][
                "value"
            ]["pools"][0]
            self.assertEqual(inner["conditions"], [{"condition": "minecraft:random_chance"}])

    def test_filtered_pass_branch_is_migrated_with_the_whole_rule_chain(self) -> None:
        # 94.1 moves on_pass back to modifier, and the 121.0 rule then has to keep walking
        # that slot: otherwise the nested function keeps the modern discriminator.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/item_modifier/filtered.json",
                json.dumps(
                    {
                        "type": "minecraft:filtered",
                        "item_filter": {"items": "minecraft:stone"},
                        "on_pass": {"type": "minecraft:set_count", "count": 3},
                    }
                ),
            )
            context = MigrationContext(root, PackFormat(121, 0), PackFormat(61), BuildPolicy())
            for rule in BUILTIN_RULES:
                if rule.applies(context.source, context.target):
                    rule.apply(context)
            value = json.loads(self._text(root, "data/demo/item_modifier/filtered.json"))
            self.assertEqual(value["function"], "minecraft:filtered")
            self.assertEqual(value["modifier"], {"function": "minecraft:set_count", "count": 3})

    def test_loot_predicate_file_discriminator_is_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/predicate/tool/can_silk_touch.json",
                json.dumps({"condition": "minecraft:match_tool", "predicate": {"items": "minecraft:shears"}}),
            )
            rule = LootSchemaKeysRule()
            self._run(root, rule, [107, 1], [121, 0])
            self.assertEqual(
                json.loads(self._text(root, "data/demo/predicate/tool/can_silk_touch.json")),
                {"type": "minecraft:match_tool", "predicate": {"items": "minecraft:shears"}},
            )
            self._run(root, rule, [121, 0], [107, 1])
            self.assertEqual(
                json.loads(self._text(root, "data/demo/predicate/tool/can_silk_touch.json")),
                {"condition": "minecraft:match_tool", "predicate": {"items": "minecraft:shears"}},
            )

    def test_loot_removed_constructs_block_the_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(
                root,
                "data/demo/item_modifier/reference.json",
                '{"function":"minecraft:reference","name":"minecraft:some_modifier"}\n',
            )
            write(
                root,
                "data/demo/predicate/value_check.json",
                '{"condition":"minecraft:value_check","value":{"type":"minecraft:score","target":"x"},'
                '"range":{"min":1}}\n',
            )
            diagnostics = self._run(root, LootSchemaKeysRule(), [107, 1], [121, 0])
            self.assertEqual(
                {item.code for item in diagnostics},
                {"loot-reference-removed", "loot-condition-removed"},
            )

    def test_modern_time_check_predicate_downgrades_without_crashing(self) -> None:
        # 26.3 uses ``type`` as the predicate discriminator and ``condition`` for the
        # predicate value, which may be an object; the 101.1 rule must not assume a string.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/loot_table/blocks/stone.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "pools": [
                            {
                                "rolls": 1,
                                "entries": [
                                    {
                                        "type": "minecraft:item",
                                        "name": "minecraft:stone",
                                        "condition": {
                                            "type": "minecraft:time_check",
                                            "clock": "minecraft:overworld",
                                            "value": 1000,
                                        },
                                    },
                                    {
                                        "type": "minecraft:item",
                                        "name": "minecraft:dirt",
                                        "condition": {"type": "minecraft:random_chance", "chance": 0.5},
                                    },
                                ],
                            }
                        ],
                    }
                ),
            )
            context = MigrationContext(root, PackFormat(121, 0), PackFormat(94, 1), BuildPolicy())
            for rule in BUILTIN_RULES:
                if rule.applies(context.source, context.target):
                    rule.apply(context)
            table = json.loads(self._text(root, "data/demo/loot_table/blocks/stone.json"))
            entries = table["pools"][0]["entries"]
            self.assertEqual(entries[0]["conditions"], [{"condition": "minecraft:time_check", "value": 1000}])
            self.assertEqual(entries[1]["conditions"], [{"condition": "minecraft:random_chance", "chance": 0.5}])

    def test_sum_number_provider_becomes_add(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/loot_table/t.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "pools": [{"rolls": {"type": "minecraft:sum", "summands": [1, 2]}, "entries": []}],
                    }
                ),
            )
            rule = NumberProviderSumRule()
            self._run(root, rule, [101, 1], [121, 0])
            value = json.loads(self._text(root, "data/demo/loot_table/t.json"))
            self.assertEqual(value["pools"][0]["rolls"], {"type": "minecraft:add", "inputs": [1, 2]})
            self._run(root, rule, [121, 0], [101, 1])
            value = json.loads(self._text(root, "data/demo/loot_table/t.json"))
            self.assertEqual(value["pools"][0]["rolls"], {"type": "minecraft:sum", "summands": [1, 2]})

    def test_number_provider_rule_ignores_same_named_density_functions(self) -> None:
        # ``add`` is also a level-based value type and a density function; only a provider
        # carries the ``inputs`` field.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(
                root,
                "data/demo/enchantment/riptide.json",
                json.dumps(
                    {"effects": {"minecraft:x": {"type": "minecraft:add", "value": {"type": "minecraft:linear"}}}}
                ),
            )
            write(
                root,
                "data/demo/worldgen/density_function/x.json",
                json.dumps({"type": "minecraft:add", "left": 1, "right": 2}),
            )
            before = [
                self._text(root, "data/demo/enchantment/riptide.json"),
                self._text(root, "data/demo/worldgen/density_function/x.json"),
            ]
            result = NumberProviderSumRule().apply(
                MigrationContext(root, PackFormat(121, 0), PackFormat(101, 1), BuildPolicy())
            )
            self.assertEqual([item.code for item in result.diagnostics], [])
            self.assertEqual(
                [
                    self._text(root, "data/demo/enchantment/riptide.json"),
                    self._text(root, "data/demo/worldgen/density_function/x.json"),
                ],
                before,
            )

    def test_number_provider_registry_split_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root, "data/demo/number_provider/demo/low.json", json.dumps({"type": "minecraft:constant", "value": 1})
            )
            diagnostics = self._run(root, NumberProviderSumRule(), [101, 1], [121, 0])
            self.assertEqual({item.code for item in diagnostics}, {"number-provider-registry-split"})

    def test_block_entity_sherds_convert_in_data_merge_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(
                root,
                "data/demo/function/load.mcfunction",
                'data merge block 1 2 3 {id:"minecraft:decorated_pot",'
                'sherds:["minecraft:brick","minecraft:flow_pottery_sherd"]}\n'
                'data merge block ~ ~ ~ {id:"minecraft:chest",Items:[]}\n',
            )
            rule = BlockEntitySherdsSnbtRule()
            self._run(root, rule, [101, 1], [121, 0])
            text = self._text(root, "data/demo/function/load.mcfunction")
            self.assertIn(
                'sherds:{back:{id:"minecraft:brick"},left:{id:"minecraft:flow_pottery_sherd"},'
                'right:{id:"minecraft:brick"},front:{id:"minecraft:brick"}}',
                text,
            )
            self.assertIn('{id:"minecraft:chest",Items:[]}', text)
            self._run(root, rule, [121, 0], [101, 1])
            text = self._text(root, "data/demo/function/load.mcfunction")
            self.assertIn(
                'sherds:["minecraft:brick","minecraft:flow_pottery_sherd","minecraft:brick","minecraft:brick"]',
                text,
            )

    def test_block_entity_sherds_macro_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            write(root, "data/demo/function/load.mcfunction", "data merge block 1 2 3 $(nbt)\n")
            diagnostics = self._run(root, BlockEntitySherdsSnbtRule(), [101, 1], [121, 0])
            self.assertEqual({item.code for item in diagnostics}, {"macro-block-nbt-needs-runtime-parse"})

    def test_block_entity_sherds_convert_in_structure_nbt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [101, 1])
            path = root / "data/demo/structure/t.nbt"
            path.parent.mkdir(parents=True, exist_ok=True)
            nbt.dump_path(
                path,
                nbt.NbtDocument(
                    "",
                    nbt.NbtTag(
                        nbt.TAG_COMPOUND,
                        {
                            "blocks": nbt.NbtTag(
                                nbt.TAG_LIST,
                                nbt.NbtList(
                                    nbt.TAG_COMPOUND,
                                    [
                                        nbt.NbtTag(
                                            nbt.TAG_COMPOUND,
                                            {
                                                "nbt": nbt.NbtTag(
                                                    nbt.TAG_COMPOUND,
                                                    {
                                                        "id": nbt.NbtTag(nbt.TAG_STRING, "minecraft:decorated_pot"),
                                                        "sherds": nbt.NbtTag(
                                                            nbt.TAG_LIST,
                                                            nbt.NbtList(
                                                                nbt.TAG_STRING,
                                                                [
                                                                    nbt.NbtTag(nbt.TAG_STRING, "minecraft:brick"),
                                                                    nbt.NbtTag(
                                                                        nbt.TAG_STRING,
                                                                        "minecraft:guster_pottery_sherd",
                                                                    ),
                                                                ],
                                                            ),
                                                        ),
                                                    },
                                                )
                                            },
                                        )
                                    ],
                                ),
                            )
                        },
                    ),
                ),
            )
            rule = BlockEntitySherdsNbtRule()
            self._run(root, rule, [101, 1], [121, 0])
            self.assertEqual(
                self._structure_face_ids(path),
                {
                    "back": "minecraft:brick",
                    "left": "minecraft:guster_pottery_sherd",
                    "right": "minecraft:brick",
                    "front": "minecraft:brick",
                },
            )
            self._run(root, rule, [121, 0], [101, 1])
            self.assertEqual(
                self._structure_sherd_list(path),
                [
                    "minecraft:brick",
                    "minecraft:guster_pottery_sherd",
                    "minecraft:brick",
                    "minecraft:brick",
                ],
            )

    @staticmethod
    def _structure_sherds(path: Path) -> nbt.NbtTag:
        loaded = nbt.load_path(path)
        root = nbt.compound(loaded.root)
        assert root is not None
        blocks = nbt.list_values(root["blocks"])
        assert blocks is not None
        entry = nbt.compound(blocks[0])
        assert entry is not None
        block_entity = nbt.compound(entry["nbt"])
        assert block_entity is not None
        return block_entity["sherds"]

    def _structure_face_ids(self, path: Path) -> dict[str, str]:
        sherds = nbt.compound(self._structure_sherds(path))
        assert sherds is not None
        faces: dict[str, str] = {}
        for face, tag in sherds.items():
            payload = nbt.compound(tag)
            assert payload is not None
            item_id = payload["id"].value
            assert isinstance(item_id, str)
            faces[face] = item_id
        return faces

    def _structure_sherd_list(self, path: Path) -> list[str]:
        entries = nbt.list_values(self._structure_sherds(path))
        assert entries is not None
        return [str(tag.value) for tag in entries]

    def test_worldgen_registry_move_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(root, "data/demo/worldgen/configured_feature/oak.json", '{"type":"minecraft:tree"}\n')
            diagnostics = self._run(root, WorldgenSchemaRule(), [107, 1], [121, 0])
            self.assertEqual({item.code for item in diagnostics}, {"worldgen-schema-rewrite-required"})
            self.assertEqual(
                self._text(root, "data/demo/worldgen/configured_feature/oak.json"), '{"type":"minecraft:tree"}\n'
            )

    def test_worldgen_registry_move_is_refused_on_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [121, 0])
            write(root, "data/demo/worldgen/feature/oak.json", '{"type":"minecraft:tree"}\n')
            diagnostics = self._run(root, WorldgenSchemaRule(), [121, 0], [107, 1])
            self.assertEqual({item.code for item in diagnostics}, {"worldgen-schema-rewrite-required"})

    def test_worldgen_rule_ignores_untouched_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = make_pack(Path(temp_dir), [107, 1])
            write(root, "data/demo/worldgen/biome/plains.json", '{"temperature":0.8}\n')
            diagnostics = self._run(root, WorldgenSchemaRule(), [107, 1], [121, 0])
            self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()
