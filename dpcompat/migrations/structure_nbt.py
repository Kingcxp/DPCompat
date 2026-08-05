"""Transform entity payloads stored inside binary structure NBT files.

This module keeps NBT tag types intact and limits changes to ``entities[].nbt``.  Palette and
block-entity data are not interpreted as entity fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import nbt


@dataclass(slots=True)
class NbtEntityResult:
    """Mutation count and safety warnings for one structure entity compound."""

    changed: int = 0
    warnings: list[str] = field(default_factory=list)


def _rename(data: dict[str, nbt.NbtTag], old: str, new: str) -> int:
    if old in data and new not in data:
        data[new] = data.pop(old)
        return 1
    return 0


def _compound_tag(value: dict[str, nbt.NbtTag] | None = None) -> nbt.NbtTag:
    return nbt.NbtTag(nbt.TAG_COMPOUND, value or {})


def _empty_item(tag: nbt.NbtTag) -> bool:
    value = nbt.compound(tag)
    if not value:
        return True
    item_id = value.get("id")
    return item_id is None or (item_id.type_id == nbt.TAG_STRING and item_id.value == "minecraft:air")


def _numeric_zero(tag: nbt.NbtTag) -> bool:
    return (
        tag.type_id
        in {
            nbt.TAG_BYTE,
            nbt.TAG_SHORT,
            nbt.TAG_INT,
            nbt.TAG_LONG,
            nbt.TAG_FLOAT,
            nbt.TAG_DOUBLE,
        }
        and tag.value == 0
    )


def upgrade_entity(entity_id: str, data: dict[str, nbt.NbtTag]) -> NbtEntityResult:
    """Upgrade typed entity tags in place to the format-71 layout."""

    result = NbtEntityResult()
    result.changed += _rename(data, "FallDistance", "fall_distance")

    if all(key in data for key in ("SleepingX", "SleepingY", "SleepingZ")) and "sleeping_pos" not in data:
        values = [int(data.pop(key).value) for key in ("SleepingX", "SleepingY", "SleepingZ")]
        data["sleeping_pos"] = nbt.NbtTag(nbt.TAG_INT_ARRAY, values)
        result.changed += 1

    equipment_tag = data.get("equipment")
    equipment = nbt.compound(equipment_tag) if equipment_tag else {}
    if equipment_tag is not None and equipment is None:
        result.warnings.append("Existing equipment tag is not a compound")
        equipment = None
    if equipment is not None:
        armor_tag = data.pop("ArmorItems", None)
        armor = nbt.list_values(armor_tag, nbt.TAG_COMPOUND) if armor_tag else None
        if armor is not None:
            for slot, item in zip(("feet", "legs", "chest", "head"), armor, strict=False):
                if not _empty_item(item):
                    equipment.setdefault(slot, item)
            result.changed += 1
        hands_tag = data.pop("HandItems", None)
        hands = nbt.list_values(hands_tag, nbt.TAG_COMPOUND) if hands_tag else None
        if hands is not None:
            for slot, item in zip(("mainhand", "offhand"), hands, strict=False):
                if not _empty_item(item):
                    equipment.setdefault(slot, item)
            result.changed += 1
        for old, slot in (("body_armor_item", "body"), ("SaddleItem", "saddle")):
            if old in data:
                equipment.setdefault(slot, data.pop(old))
                result.changed += 1
        if "Saddle" in data:
            saddle = data.pop("Saddle")
            if not _numeric_zero(saddle):
                equipment.setdefault(
                    "saddle",
                    _compound_tag(
                        {
                            "id": nbt.NbtTag(nbt.TAG_STRING, "minecraft:saddle"),
                            "count": nbt.NbtTag(nbt.TAG_INT, 1),
                        }
                    ),
                )
            result.changed += 1
        if equipment:
            data["equipment"] = _compound_tag(equipment)

    chances_tag = data.get("drop_chances")
    chances = nbt.compound(chances_tag) if chances_tag else {}
    if chances_tag is not None and chances is None:
        result.warnings.append("Existing drop_chances tag is not a compound")
        chances = None
    if chances is not None:
        armor_drop_tag = data.pop("ArmorDropChances", None)
        armor_drop = nbt.list_values(armor_drop_tag, nbt.TAG_FLOAT) if armor_drop_tag else None
        if armor_drop is not None:
            for slot, chance in zip(("feet", "legs", "chest", "head"), armor_drop, strict=False):
                chances.setdefault(slot, chance)
            result.changed += 1
        hand_drop_tag = data.pop("HandDropChances", None)
        hand_drop = nbt.list_values(hand_drop_tag, nbt.TAG_FLOAT) if hand_drop_tag else None
        if hand_drop is not None:
            for slot, chance in zip(("mainhand", "offhand"), hand_drop, strict=False):
                chances.setdefault(slot, chance)
            result.changed += 1
        if "body_armor_drop_chance" in data:
            chances.setdefault("body", data.pop("body_armor_drop_chance"))
            result.changed += 1
        if chances:
            data["drop_chances"] = _compound_tag(chances)

    if (
        entity_id in {"minecraft:item_frame", "minecraft:glow_item_frame"}
        and all(key in data for key in ("TileX", "TileY", "TileZ"))
        and "block_pos" not in data
    ):
        data["block_pos"] = nbt.NbtTag(
            nbt.TAG_INT_ARRAY,
            [int(data.pop(key).value) for key in ("TileX", "TileY", "TileZ")],
        )
        result.changed += 1
    if entity_id == "minecraft:phantom":
        result.changed += _rename(data, "Size", "size")
        if all(key in data for key in ("AX", "AY", "AZ")) and "anchor_pos" not in data:
            data["anchor_pos"] = nbt.NbtTag(
                nbt.TAG_INT_ARRAY,
                [int(data.pop(key).value) for key in ("AX", "AY", "AZ")],
            )
            result.changed += 1
    return result


def downgrade_entity(entity_id: str, data: dict[str, nbt.NbtTag]) -> NbtEntityResult:
    """Downgrade typed entity tags and report values legacy NBT cannot express."""

    result = NbtEntityResult()
    result.changed += _rename(data, "fall_distance", "FallDistance")
    sleeping = data.pop("sleeping_pos", None)
    if sleeping and sleeping.type_id == nbt.TAG_INT_ARRAY and len(sleeping.value) == 3:
        for key, number in zip(("SleepingX", "SleepingY", "SleepingZ"), sleeping.value, strict=True):
            data[key] = nbt.NbtTag(nbt.TAG_INT, number)
        result.changed += 1
    elif sleeping is not None:
        data["sleeping_pos"] = sleeping
        result.warnings.append("sleeping_pos was not a three-value int array")

    equipment_tag = data.pop("equipment", None)
    equipment = nbt.compound(equipment_tag) if equipment_tag else None
    if equipment is not None:
        empty = _compound_tag()
        armor = [equipment.pop(slot, empty) for slot in ("feet", "legs", "chest", "head")]
        hands = [equipment.pop(slot, empty) for slot in ("mainhand", "offhand")]
        if any(not _empty_item(item) for item in armor):
            data["ArmorItems"] = nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_COMPOUND, armor))
        if any(not _empty_item(item) for item in hands):
            data["HandItems"] = nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_COMPOUND, hands))
        if "body" in equipment:
            data["body_armor_item"] = equipment.pop("body")
        if "saddle" in equipment:
            saddle = equipment.pop("saddle")
            if entity_id in {"minecraft:pig", "minecraft:strider"}:
                data["Saddle"] = nbt.NbtTag(nbt.TAG_BYTE, 1)
                result.warnings.append("Saddle item components are lost for Pig/Strider")
            else:
                data["SaddleItem"] = saddle
        if equipment:
            data["equipment"] = _compound_tag(equipment)
            result.warnings.append("Unknown equipment slots cannot be represented in legacy fields")
        result.changed += 1
    elif equipment_tag is not None:
        data["equipment"] = equipment_tag
        result.warnings.append("equipment was not a compound")

    chances_tag = data.pop("drop_chances", None)
    chances = nbt.compound(chances_tag) if chances_tag else None
    if chances is not None:
        default = nbt.NbtTag(nbt.TAG_FLOAT, 0.085)
        armor = [chances.pop(slot, default) for slot in ("feet", "legs", "chest", "head")]
        hands = [chances.pop(slot, default) for slot in ("mainhand", "offhand")]
        if any(item.value != default.value for item in armor):
            data["ArmorDropChances"] = nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_FLOAT, armor))
        if any(item.value != default.value for item in hands):
            data["HandDropChances"] = nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_FLOAT, hands))
        if "body" in chances:
            data["body_armor_drop_chance"] = chances.pop("body")
        if chances:
            data["drop_chances"] = _compound_tag(chances)
            result.warnings.append("Unknown drop chance slots cannot be represented in legacy fields")
        result.changed += 1
    elif chances_tag is not None:
        data["drop_chances"] = chances_tag
        result.warnings.append("drop_chances was not a compound")

    if entity_id in {"minecraft:item_frame", "minecraft:glow_item_frame"}:
        block_pos = data.pop("block_pos", None)
        if block_pos and block_pos.type_id == nbt.TAG_INT_ARRAY and len(block_pos.value) == 3:
            for key, number in zip(("TileX", "TileY", "TileZ"), block_pos.value, strict=True):
                data[key] = nbt.NbtTag(nbt.TAG_INT, number)
            result.changed += 1
        elif block_pos is not None:
            data["block_pos"] = block_pos
            result.warnings.append("block_pos was not a three-value int array")
    if entity_id == "minecraft:phantom":
        result.changed += _rename(data, "size", "Size")
        anchor = data.pop("anchor_pos", None)
        if anchor and anchor.type_id == nbt.TAG_INT_ARRAY and len(anchor.value) == 3:
            for key, number in zip(("AX", "AY", "AZ"), anchor.value, strict=True):
                data[key] = nbt.NbtTag(nbt.TAG_INT, number)
            result.changed += 1
        elif anchor is not None:
            data["anchor_pos"] = anchor
            result.warnings.append("anchor_pos was not a three-value int array")
    return result
