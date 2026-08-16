"""Transform the supported subset of entity NBT around format 71.

The functions in this module operate on parsed SNBT/NBT values and return an explicit
compatibility outcome.  They never assume that similarly named keys in arbitrary storage
data are entity fields; callers must establish the entity-NBT context first.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from . import snbt
from .text_components import TextComponentMigrationError, downgrade_component, upgrade_component


@dataclass(slots=True)
class EntityTransformResult:
    """Transformed entity compound plus mutation count and safety diagnostics."""

    value: dict[str, Any]
    changed: int = 0
    warnings: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


def _matches_entity(entity_id: str, path: str) -> bool:
    return entity_id in {path, f"minecraft:{path}"}


def _upgrade_embedded_component(data: dict[str, Any], key: str, unknowns: list[str]) -> int:
    if key not in data:
        return 0
    original = data[key]
    source = original
    if isinstance(original, str):
        try:
            source = json.loads(original)
        except json.JSONDecodeError as exc:
            unknowns.append(f"Embedded text component {key!r} is not valid legacy JSON: {exc}")
            return 0
    try:
        migrated = upgrade_component(source)
    except TextComponentMigrationError as exc:
        unknowns.append(f"Embedded text component {key!r} cannot be upgraded safely: {exc}")
        return 0
    if migrated == original:
        return 0
    data[key] = migrated
    return 1


def _downgrade_embedded_component(data: dict[str, Any], key: str, unknowns: list[str]) -> int:
    if key not in data:
        return 0
    original = data[key]
    try:
        migrated = downgrade_component(original)
        wrapped = json.dumps(
            snbt.to_json_compatible(migrated),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TextComponentMigrationError, TypeError, ValueError) as exc:
        unknowns.append(f"Embedded text component {key!r} cannot be downgraded safely: {exc}")
        return 0
    if wrapped == original:
        return 0
    data[key] = wrapped
    return 1


def _is_empty_item(value: Any) -> bool:
    return not isinstance(value, dict) or not value or value.get("id") in {None, "minecraft:air"}


def _number_zero(value: Any) -> bool:
    if isinstance(value, snbt.SnbtNumber):
        parsed = value.as_int()
        return parsed == 0
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value == 0
    return False


def upgrade_entity_nbt(entity_id: str, value: dict[str, Any]) -> EntityTransformResult:
    """Upgrade the reviewed legacy entity fields to the format-71 layout."""

    data = deepcopy(value)
    changed = 0
    warnings: list[str] = []
    unknowns: list[str] = []

    def rename(old: str, new: str) -> None:
        nonlocal changed
        if old in data and new not in data:
            data[new] = data.pop(old)
            changed += 1

    rename("FallDistance", "fall_distance")
    changed += _upgrade_embedded_component(data, "CustomName", unknowns)
    if _matches_entity(entity_id, "text_display"):
        changed += _upgrade_embedded_component(data, "text", unknowns)

    if all(key in data for key in ("SleepingX", "SleepingY", "SleepingZ")) and "sleeping_pos" not in data:
        data["sleeping_pos"] = snbt.SnbtArray(
            "I", (data.pop("SleepingX"), data.pop("SleepingY"), data.pop("SleepingZ"))
        )
        changed += 1

    equipment = data.get("equipment")
    if equipment is None:
        equipment = {}
    if not isinstance(equipment, dict):
        warnings.append("Existing equipment field is not a compound; legacy equipment was not merged")
        equipment = None

    if equipment is not None:
        armor = data.pop("ArmorItems", None)
        if isinstance(armor, list):
            for slot, item in zip(("feet", "legs", "chest", "head"), armor, strict=False):
                if not _is_empty_item(item):
                    equipment.setdefault(slot, item)
            changed += 1
        hands = data.pop("HandItems", None)
        if isinstance(hands, list):
            for slot, item in zip(("mainhand", "offhand"), hands, strict=False):
                if not _is_empty_item(item):
                    equipment.setdefault(slot, item)
            changed += 1
        if "body_armor_item" in data:
            equipment.setdefault("body", data.pop("body_armor_item"))
            changed += 1
        if "SaddleItem" in data:
            equipment.setdefault("saddle", data.pop("SaddleItem"))
            changed += 1
        if "Saddle" in data:
            saddle = data.pop("Saddle")
            if not _number_zero(saddle):
                equipment.setdefault(
                    "saddle",
                    {"id": "minecraft:saddle", "count": snbt.SnbtNumber("1")},
                )
            changed += 1
        if equipment:
            data["equipment"] = equipment

    drop_chances = data.get("drop_chances")
    if drop_chances is None:
        drop_chances = {}
    if isinstance(drop_chances, dict):
        armor_drop = data.pop("ArmorDropChances", None)
        if isinstance(armor_drop, list):
            for slot, chance in zip(("feet", "legs", "chest", "head"), armor_drop, strict=False):
                drop_chances.setdefault(slot, chance)
            changed += 1
        hand_drop = data.pop("HandDropChances", None)
        if isinstance(hand_drop, list):
            for slot, chance in zip(("mainhand", "offhand"), hand_drop, strict=False):
                drop_chances.setdefault(slot, chance)
            changed += 1
        if "body_armor_drop_chance" in data:
            drop_chances.setdefault("body", data.pop("body_armor_drop_chance"))
            changed += 1
        if drop_chances:
            data["drop_chances"] = drop_chances

    if (
        entity_id in {"minecraft:item_frame", "minecraft:glow_item_frame", "minecraft:painting", "minecraft:leash_knot"}
        and all(key in data for key in ("TileX", "TileY", "TileZ"))
        and "block_pos" not in data
    ):
        data["block_pos"] = snbt.SnbtArray("I", (data.pop("TileX"), data.pop("TileY"), data.pop("TileZ")))
        changed += 1
    if entity_id == "minecraft:phantom":
        rename("Size", "size")
        if all(key in data for key in ("AX", "AY", "AZ")) and "anchor_pos" not in data:
            data["anchor_pos"] = snbt.SnbtArray("I", (data.pop("AX"), data.pop("AY"), data.pop("AZ")))
            changed += 1
    if entity_id == "minecraft:player":
        respawn_keys = ("SpawnX", "SpawnY", "SpawnZ")
        if all(key in data for key in respawn_keys) and "respawn" not in data:
            respawn: dict[str, Any] = {"pos": snbt.SnbtArray("I", tuple(data.pop(key) for key in respawn_keys))}
            mapping = {
                "SpawnAngle": "angle",
                "SpawnDimension": "dimension",
                "SpawnForced": "forced",
            }
            for old, new in mapping.items():
                if old in data:
                    respawn[new] = data.pop(old)
            data["respawn"] = respawn
            changed += 1
        rename("enteredNetherPosition", "entered_nether_pos")

    passengers = data.get("Passengers")
    if isinstance(passengers, list):
        for index, passenger in enumerate(passengers):
            if not isinstance(passenger, dict):
                continue
            nested_id = passenger.get("id")
            result = upgrade_entity_nbt(nested_id if isinstance(nested_id, str) else "", passenger)
            if result.changed:
                passengers[index] = result.value
                changed += result.changed
            warnings.extend(result.warnings)
            unknowns.extend(result.unknowns)

    return EntityTransformResult(data, changed, warnings, unknowns)


def downgrade_entity_nbt(entity_id: str, value: dict[str, Any]) -> EntityTransformResult:
    """Reconstruct legacy entity fields and report information that cannot fit."""

    data = deepcopy(value)
    changed = 0
    warnings: list[str] = []
    unknowns: list[str] = []

    def rename(old: str, new: str) -> None:
        nonlocal changed
        if old in data and new not in data:
            data[new] = data.pop(old)
            changed += 1

    rename("fall_distance", "FallDistance")
    changed += _downgrade_embedded_component(data, "CustomName", unknowns)
    if _matches_entity(entity_id, "text_display"):
        changed += _downgrade_embedded_component(data, "text", unknowns)
    sleeping = data.pop("sleeping_pos", None)
    if isinstance(sleeping, snbt.SnbtArray) and len(sleeping.values) == 3:
        data["SleepingX"], data["SleepingY"], data["SleepingZ"] = sleeping.values
        changed += 1

    equipment = data.pop("equipment", None)
    if isinstance(equipment, dict):
        empty: dict[str, Any] = {}
        armor = [equipment.pop(slot, empty) for slot in ("feet", "legs", "chest", "head")]
        hands = [equipment.pop(slot, empty) for slot in ("mainhand", "offhand")]
        if any(not _is_empty_item(item) for item in armor):
            data["ArmorItems"] = armor
        if any(not _is_empty_item(item) for item in hands):
            data["HandItems"] = hands
        if "body" in equipment:
            data["body_armor_item"] = equipment.pop("body")
        if "saddle" in equipment:
            saddle = equipment.pop("saddle")
            if entity_id in {"minecraft:pig", "minecraft:strider"}:
                data["Saddle"] = True
                warnings.append("Saddle item components are lost when downgrading Pig/Strider Saddle")
            else:
                data["SaddleItem"] = saddle
        if equipment:
            warnings.append(f"Unsupported equipment slots were retained: {', '.join(equipment)}")
            data["equipment"] = equipment
        changed += 1

    drop_chances = data.pop("drop_chances", None)
    if isinstance(drop_chances, dict):
        default = snbt.SnbtNumber("0.085f")
        armor = [drop_chances.pop(slot, default) for slot in ("feet", "legs", "chest", "head")]
        hands = [drop_chances.pop(slot, default) for slot in ("mainhand", "offhand")]
        if any(item != default for item in armor):
            data["ArmorDropChances"] = armor
        if any(item != default for item in hands):
            data["HandDropChances"] = hands
        if "body" in drop_chances:
            data["body_armor_drop_chance"] = drop_chances.pop("body")
        if drop_chances:
            warnings.append(f"Unsupported drop chance slots were retained: {', '.join(drop_chances)}")
            data["drop_chances"] = drop_chances
        changed += 1

    if entity_id in {
        "minecraft:item_frame",
        "minecraft:glow_item_frame",
        "minecraft:painting",
        "minecraft:leash_knot",
    }:
        block_pos = data.pop("block_pos", None)
        if isinstance(block_pos, snbt.SnbtArray) and len(block_pos.values) == 3:
            data["TileX"], data["TileY"], data["TileZ"] = block_pos.values
            changed += 1
    if entity_id == "minecraft:phantom":
        rename("size", "Size")
        anchor = data.pop("anchor_pos", None)
        if isinstance(anchor, snbt.SnbtArray) and len(anchor.values) == 3:
            data["AX"], data["AY"], data["AZ"] = anchor.values
            changed += 1
    if entity_id == "minecraft:player":
        respawn = data.pop("respawn", None)
        if isinstance(respawn, dict):
            pos = respawn.pop("pos", None)
            if isinstance(pos, snbt.SnbtArray) and len(pos.values) == 3:
                data["SpawnX"], data["SpawnY"], data["SpawnZ"] = pos.values
            mapping = {
                "angle": "SpawnAngle",
                "dimension": "SpawnDimension",
                "forced": "SpawnForced",
            }
            for old, new in mapping.items():
                if old in respawn:
                    data[new] = respawn.pop(old)
            if respawn:
                warnings.append("Unknown respawn fields were retained")
                data["respawn"] = respawn
            changed += 1
        rename("entered_nether_pos", "enteredNetherPosition")

    passengers = data.get("Passengers")
    if isinstance(passengers, list):
        for index, passenger in enumerate(passengers):
            if not isinstance(passenger, dict):
                continue
            nested_id = passenger.get("id")
            result = downgrade_entity_nbt(nested_id if isinstance(nested_id, str) else "", passenger)
            if result.changed:
                passengers[index] = result.value
                changed += result.changed
            warnings.extend(result.warnings)
            unknowns.extend(result.unknowns)

    return EntityTransformResult(data, changed, warnings, unknowns)
