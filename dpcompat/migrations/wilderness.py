"""Migrate the Minecraft 26.3 ("Wilderness Bound") / format-121.0 schema boundary.

26.3 rewrites several unrelated data-pack schemas at once.  Every rule in this module
implements only a transformation whose old and new shapes are fixed by the official 26.3
release notes *and* reproduced by the vanilla 26.3 data pack, so the reverse direction can
be proven instead of guessed:

* ``minecraft:swing_animation`` splits into ``minecraft:attack_animation`` and
  ``minecraft:interact_animation`` (Mojang states the split is equivalent apart from the
  ``/swing`` command and entity drops);
* ``minecraft:map_color`` is removed and stripped by the game on load;
* ``minecraft:pot_decorations`` turns its four-entry list into a face map; the legacy list
  order is ``back, left, right, front`` (``PotDecorations.ordered()``);
* the ``minecraft:gameplay/bed_rule`` environment attribute renames ``explodes``;
* trim material definitions rename ``asset_name`` to ``palette_id``;
* loot functions, loot conditions and loot pool entries move their discriminator to
  ``type`` and their condition to ``condition``/``modifier``.

The 26.3 world-generation rewrite is refused rather than approximated: 26.3 also evaluates
density functions in single precision, so a syntactically correct rewrite would still change
generated terrain.  ``WorldgenSchemaRule`` therefore fails closed with a precise diagnostic.
"""

from __future__ import annotations

import copy
import struct
from pathlib import Path
from typing import Any

from .. import nbt
from ..commands import (
    iter_execute_segments,
    macro_placeholders_are_quoted,
    parse_command_line,
)
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from ..snbt import SnbtError
from ..snbt import dumps as dumps_snbt
from ..snbt import loads as loads_snbt
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic, transform_json_files

BOUNDARY = PackFormat(121)

_POT_FACES = ("back", "left", "right", "front")
_BRICK = "minecraft:brick"

_SWING = "minecraft:swing_animation"
_ATTACK = "minecraft:attack_animation"
_INTERACT = "minecraft:interact_animation"

_MAP_COLOR = "minecraft:map_color"
_POT_DECORATIONS = "minecraft:pot_decorations"

_BED_RULE = "minecraft:gameplay/bed_rule"

_LOOT_DIRECTORIES = ("/loot_table/", "/item_modifier/", "/predicate/")
_REFERENCE_FUNCTION_IDS = {"minecraft:reference", "reference"}
_REMOVED_CONDITION_IDS = {
    "minecraft:value_check",
    "value_check",
    "minecraft:block_state_property",
    "block_state_property",
}
_BEHAVIOUR_CHANGED_FUNCTION_IDS = {"minecraft:exploration_map", "exploration_map"}


def _is_loot_resource(context: MigrationContext, path: Path) -> bool:
    relative = "/" + context.relative(path)
    return any(marker in relative for marker in _LOOT_DIRECTORIES)


def _discriminator(node: dict[str, Any], *keys: str) -> str | None:
    """Return the first string-valued discriminator among ``keys``.

    Loot schemas reuse ``type``/``condition``/``function`` for user data too, so a
    membership test must never assume the value is a string.
    """

    for key in keys:
        value = node.get(key)
        if isinstance(value, str):
            return value
    return None


def _walk_maps(value: Any, transform: Any) -> Any:
    """Rebuild ``value``, applying ``transform`` to every JSON object."""

    if isinstance(value, list):
        return [_walk_maps(item, transform) for item in value]
    if not isinstance(value, dict):
        return value
    rebuilt = {key: _walk_maps(item, transform) for key, item in value.items()}
    return transform(rebuilt)


class SwingAnimationSplitRule:
    """Split ``swing_animation`` into the attack/interact animation components."""

    id = "item-components.swing-animation-split@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            diagnostics: list[Diagnostic] = []
            changed = 0

            def visit(node: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                if upgrading:
                    swing = node.get(_SWING)
                    if swing is None:
                        return node
                    if not isinstance(swing, dict):
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="swing-animation-not-an-object",
                                message="minecraft:swing_animation is not an object and cannot be split",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                        return node
                    if _ATTACK in node or _INTERACT in node:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="swing-animation-split-conflict",
                                message=(
                                    "minecraft:swing_animation cannot be split because an animation "
                                    "component already exists"
                                ),
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                        return node
                    result = dict(node)
                    result.pop(_SWING)
                    result[_ATTACK] = copy.deepcopy(swing)
                    result[_INTERACT] = copy.deepcopy(swing)
                    changed += 1
                    return result

                attack = node.get(_ATTACK)
                interact = node.get(_INTERACT)
                if attack is None and interact is None:
                    return node
                if isinstance(attack, dict) and attack == interact:
                    result = dict(node)
                    result.pop(_ATTACK)
                    result.pop(_INTERACT)
                    result[_SWING] = copy.deepcopy(attack)
                    changed += 1
                    return result
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNSUPPORTED,
                        code="animation-components-cannot-downgrade",
                        message=(
                            "attack_animation and interact_animation are only interchangeable with "
                            "swing_animation when both hold the same value"
                        ),
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                    )
                )
                return node

            return _walk_maps(value, visit), changed, diagnostics

        return transform_json_files(context, self.id, transform)


class MapColorRemovalRule:
    """Drop ``minecraft:map_color`` when upgrading; the game strips it on load."""

    id = "item-components.map-color-removed@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        if not upgrading:
            # 26.3 sources cannot contain a component that no longer exists, so the
            # downgrade direction has nothing to restore and stays a no-op.
            return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0, 0))

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            changed = 0

            def visit(node: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                if _MAP_COLOR not in node:
                    return node
                result = dict(node)
                result.pop(_MAP_COLOR)
                changed += 1
                return result

            return _walk_maps(value, visit), changed, []

        return transform_json_files(context, self.id, transform)


class PotDecorationsFacesRule:
    """Convert ``pot_decorations`` between the legacy list and the face map."""

    id = "item-components.pot-decorations-faces@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            diagnostics: list[Diagnostic] = []
            changed = 0

            def visit(node: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                if _POT_DECORATIONS not in node:
                    return node
                component = node[_POT_DECORATIONS]
                converted: Any
                if upgrading:
                    converted = self._to_faces(context, path, component, diagnostics)
                else:
                    converted = self._to_list(context, path, component, diagnostics)
                if converted is None:
                    return node
                result = dict(node)
                result[_POT_DECORATIONS] = converted
                changed += 1
                return result

            return _walk_maps(value, visit), changed, diagnostics

        return transform_json_files(context, self.id, transform)

    def _to_faces(
        self,
        context: MigrationContext,
        path: Path,
        component: Any,
        diagnostics: list[Diagnostic],
    ) -> dict[str, Any] | None:
        if not isinstance(component, list) or len(component) > len(_POT_FACES):
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="pot-decorations-shape-unknown",
                    message="Only the four-entry legacy pot_decorations list can be converted",
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
            return None
        if any(not isinstance(item, str) for item in component):
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="pot-decorations-entry-not-an-id",
                    message="Legacy pot_decorations entries must be plain item IDs",
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
            return None
        faces: dict[str, Any] = {}
        for index, face in enumerate(_POT_FACES):
            # A missing legacy entry means "brick", which in 26.3 is still an explicit
            # face item: leaving the key out would stop the pot from dropping the brick.
            item = component[index] if index < len(component) else _BRICK
            faces[face] = {"id": item}
        return faces

    def _to_list(
        self,
        context: MigrationContext,
        path: Path,
        component: Any,
        diagnostics: list[Diagnostic],
    ) -> list[str] | None:
        if not isinstance(component, dict):
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="pot-decorations-shape-unknown",
                    message="Only the 26.3 pot_decorations face map can be converted",
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
            return None
        unknown_faces = sorted(set(component) - set(_POT_FACES))
        if unknown_faces:
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="pot-decorations-unknown-face",
                    message="The face map contains keys that have no legacy equivalent",
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                    details={"faces": unknown_faces},
                )
            )
            return None
        entries: list[str] = []
        for face in _POT_FACES:
            if face not in component:
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.LOSSY,
                        code="pot-decorations-empty-face",
                        message=(
                            "An empty pot face drops nothing in 26.3; older releases always drop "
                            "a brick for an unspecified face"
                        ),
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                        details={"face": face},
                    )
                )
                entries.append(_BRICK)
                continue
            stack = component[face]
            if isinstance(stack, str):
                entries.append(stack)
                continue
            if not isinstance(stack, dict) or not isinstance(stack.get("id"), str):
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNKNOWN,
                        code="pot-decorations-face-not-an-item-stack",
                        message="A pot face must be an item stack with an id",
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                        details={"face": face},
                    )
                )
                return None
            count = stack.get("count", 1)
            if stack.get("components") or count != 1:
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNSUPPORTED,
                        code="pot-decorations-face-data-cannot-downgrade",
                        message="Older pot_decorations entries store item IDs only, not item stacks",
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                        details={"face": face},
                    )
                )
                return None
            entries.append(stack["id"])
        return entries


class BedRuleFieldsRule:
    """Rename the ``gameplay/bed_rule`` environment attribute field."""

    id = "environment-attributes.bed-rule-fields@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            diagnostics: list[Diagnostic] = []
            changed = 0

            def visit(node: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                rule = node.get(_BED_RULE)
                if not isinstance(rule, dict):
                    return node
                if upgrading:
                    if "explodes" not in rule:
                        return node
                    updated = dict(rule)
                    updated["destroy_on_use"] = updated.pop("explodes")
                else:
                    if rule.get("destroy_on_leave"):
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="bed-rule-destroy-on-leave-cannot-downgrade",
                                message="destroy_on_leave has no pre-26.3 equivalent",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                        return node
                    if "destroy_on_use" not in rule:
                        return node
                    updated = dict(rule)
                    updated["explodes"] = updated.pop("destroy_on_use")
                result = dict(node)
                result[_BED_RULE] = updated
                changed += 1
                return result

            return _walk_maps(value, visit), changed, diagnostics

        return transform_json_files(context, self.id, transform)


class TrimMaterialPaletteRule:
    """Rename the trim material asset field and refuse the resource-pack override."""

    id = "registry.trim-material-palette-id@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            relative = "/" + context.relative(path)
            if "/trim_material/" not in relative or not isinstance(value, dict):
                return value, 0, []
            diagnostics: list[Diagnostic] = []
            result = dict(value)
            changed = 0
            if upgrading:
                if "override_armor_assets" in result:
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="trim-material-overrides-cannot-upgrade",
                            message=(
                                "override_armor_assets moved to the resource pack equipment asset, "
                                "which DPCompat does not migrate"
                            ),
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                        )
                    )
                    return value, 0, diagnostics
                if "asset_name" in result:
                    result["palette_id"] = result.pop("asset_name")
                    changed += 1
            elif "palette_id" in result:
                result["asset_name"] = result.pop("palette_id")
                changed += 1
            return result, changed, diagnostics

        return transform_json_files(context, self.id, transform)


class LootSchemaKeysRule:
    """Move loot discriminators to ``type`` and conditions to ``condition``/``modifier``."""

    id = "loot.function-condition-and-pool-keys@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        convert = self._upgrade if upgrading else self._downgrade

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            if not _is_loot_resource(context, path):
                return value, 0, []
            diagnostics: list[Diagnostic] = []
            changed = 0
            relative = "/" + context.relative(path)
            try:
                if "/predicate/" in relative:
                    converted, count = convert(value, "condition", context, path, diagnostics)
                elif "/item_modifier/" in relative:
                    converted, count = convert(value, "function", context, path, diagnostics)
                else:
                    converted, count = convert(value, "table", context, path, diagnostics)
            except _UnsupportedLootShape as exc:
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNKNOWN,
                        code="loot-shape-unknown",
                        message=str(exc),
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                    )
                )
                return value, 0, diagnostics
            changed += count
            return converted, changed, diagnostics

        return transform_json_files(context, self.id, transform)

    # -- upgrade -----------------------------------------------------------------

    def _upgrade(
        self,
        value: Any,
        role: str,
        context: MigrationContext,
        path: Path,
        diagnostics: list[Diagnostic],
    ) -> tuple[Any, int]:
        if role == "table":
            return self._up_table(value, context, path, diagnostics)
        if role == "function":
            return self._up_function_slot(value, context, path, diagnostics)
        return self._up_condition(value, context, path, diagnostics)

    def _up_table(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot table must be an object")
        result = dict(value)
        changed = 0
        # A loot table may carry its own functions/modifier next to its pools.
        if "functions" in result:
            result["modifier"], count = self._up_function_list(result.pop("functions"), context, path, diagnostics)
            changed += count
        pools = result.get("pools")
        if pools is not None:
            if not isinstance(pools, list):
                raise _UnsupportedLootShape("loot table pools must be a list")
            converted = []
            for pool in pools:
                item, count = self._up_pool(pool, context, path, diagnostics)
                converted.append(item)
                changed += count
            result["pools"] = converted
        return result, changed

    def _up_pool(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot pool must be an object")
        result = dict(value)
        changed = 0
        if "conditions" in result:
            converted, count = self._up_condition_list(result.pop("conditions"), context, path, diagnostics)
            changed += count
            if converted is not None:
                result["condition"] = converted
            else:
                changed += 1
        if "functions" in result:
            result["modifier"], count = self._up_function_list(result.pop("functions"), context, path, diagnostics)
            changed += count
        entries = result.get("entries")
        if entries is not None:
            if not isinstance(entries, list):
                raise _UnsupportedLootShape("loot pool entries must be a list")
            converted_entries = []
            for entry in entries:
                item, count = self._up_entry(entry, context, path, diagnostics)
                converted_entries.append(item)
                changed += count
            result["entries"] = converted_entries
        return result, changed

    def _up_entry(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot entry must be an object")
        result = dict(value)
        changed = 0
        entry_type = _discriminator(result, "type")
        if "conditions" in result:
            converted, count = self._up_condition_list(result.pop("conditions"), context, path, diagnostics)
            changed += count
            if converted is not None:
                result["condition"] = converted
            else:
                changed += 1
        if "functions" in result:
            result["modifier"], count = self._up_function_list(result.pop("functions"), context, path, diagnostics)
            changed += count
        if entry_type in {"minecraft:tag", "tag"} and "name" in result:
            result["items"] = result.pop("name")
            changed += 1
        if entry_type in {"minecraft:loot_table", "loot_table"} and isinstance(result.get("value"), dict):
            # A loot_table entry may embed a whole loot table instead of referencing one.
            result["value"], count = self._up_table(result["value"], context, path, diagnostics)
            changed += count
        children = result.get("children")
        if children is not None:
            if not isinstance(children, list):
                raise _UnsupportedLootShape("loot entry children must be a list")
            converted_children = []
            for child in children:
                item, count = self._up_entry(child, context, path, diagnostics)
                converted_children.append(item)
                changed += count
            result["children"] = converted_children
        return result, changed

    def _up_condition_list(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, list):
            raise _UnsupportedLootShape("loot conditions must be a list")
        if not value:
            return None, 1
        if len(value) == 1:
            converted, count = self._up_condition(value[0], context, path, diagnostics)
            return converted, count + 1
        terms: list[Any] = []
        changed = 1
        for item in value:
            converted, count = self._up_condition(item, context, path, diagnostics)
            terms.append(converted)
            changed += count
        return {"type": "minecraft:all_of", "terms": terms}, changed

    def _up_condition(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if isinstance(value, str):
            return value, 0
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot condition must be an object")
        condition_id = _discriminator(value, "condition")
        if condition_id in _REMOVED_CONDITION_IDS:
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNSUPPORTED,
                    code="loot-condition-removed",
                    message=f"{condition_id} was removed in 26.3 and has no direct replacement",
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
        result = dict(value)
        changed = 0
        if "condition" in result:
            result["type"] = result.pop("condition")
            changed += 1
        for key in ("terms",):
            terms = result.get(key)
            if terms is None:
                continue
            if not isinstance(terms, list):
                raise _UnsupportedLootShape(f"{key} must be a list")
            converted = []
            for item in terms:
                item, count = self._up_condition(item, context, path, diagnostics)
                converted.append(item)
                changed += count
            result[key] = converted
        if "term" in result:
            converted, count = self._up_condition(result["term"], context, path, diagnostics)
            result["term"] = converted
            changed += count
        return result, changed

    def _up_function_slot(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if isinstance(value, list):
            return self._up_function_list(value, context, path, diagnostics)
        if isinstance(value, str):
            return value, 0
        return self._up_function(value, context, path, diagnostics)

    def _up_function_list(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, list):
            raise _UnsupportedLootShape("loot functions must be a list")
        converted = []
        changed = 0
        for item in value:
            entry, count = self._up_function(item, context, path, diagnostics)
            converted.append(entry)
            changed += count
        return converted, changed

    def _up_function(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot function must be an object")
        result = dict(value)
        changed = 0
        function_id = _discriminator(result, "function")
        if function_id in _REFERENCE_FUNCTION_IDS:
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNSUPPORTED,
                    code="loot-reference-removed",
                    message=(
                        "minecraft:reference was removed in 26.3; use the referenced id directly "
                        "instead of an automatic rewrite"
                    ),
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
        elif function_id in _BEHAVIOUR_CHANGED_FUNCTION_IDS:
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="loot-function-behaviour-changed",
                    message=(
                        "minecraft:exploration_map no longer changes the item type and drops the "
                        "map_color field in 26.3, so the pack needs author review"
                    ),
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
        if function_id in {"minecraft:set_loot_table", "set_loot_table"}:
            # ``type`` used to describe the block entity written into BlockEntityTag.id and
            # was removed in 26.3; it must be dropped before the discriminator takes the name.
            if result.pop("type", None) is not None:
                changed += 1
            if "name" in result:
                result["loot_table_id"] = result.pop("name")
                changed += 1
        if "function" in result:
            result["type"] = result.pop("function")
            changed += 1
        if "conditions" in result:
            converted, count = self._up_condition_list(result.pop("conditions"), context, path, diagnostics)
            changed += count
            if converted is not None:
                result["condition"] = converted
            else:
                changed += 1
        for key in ("on_pass", "on_fail"):
            if key in result:
                result[key], count = self._up_function_slot(result[key], context, path, diagnostics)
                changed += count
        if function_id in {"minecraft:sequence", "sequence"} and "functions" in result:
            result["functions"], count = self._up_function_list(result["functions"], context, path, diagnostics)
            changed += count
        # Inside a loot function, ``modifier`` is a nested function slot: the pass branch of
        # ``minecraft:filtered`` in older releases and of ``minecraft:modify_contents`` here.
        if "modifier" in result and isinstance(result["modifier"], dict | list):
            result["modifier"], count = self._up_function_slot(result["modifier"], context, path, diagnostics)
            changed += count
        if function_id in {"minecraft:set_contents", "set_contents"} and isinstance(result.get("entries"), list):
            converted_entries = []
            for entry in result["entries"]:
                item, count = self._up_entry(entry, context, path, diagnostics)
                converted_entries.append(item)
                changed += count
            result["entries"] = converted_entries
        return result, changed

    # -- downgrade ---------------------------------------------------------------

    def _downgrade(
        self,
        value: Any,
        role: str,
        context: MigrationContext,
        path: Path,
        diagnostics: list[Diagnostic],
    ) -> tuple[Any, int]:
        if role == "table":
            return self._down_table(value, context, path, diagnostics)
        if role == "function":
            return self._down_function_slot(value, context, path, diagnostics)
        return self._down_condition(value, context, path, diagnostics)

    def _down_table(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot table must be an object")
        result = dict(value)
        changed = 0
        if "modifier" in result:
            result["functions"], count = self._down_function_list(result.pop("modifier"), context, path, diagnostics)
            changed += count
        pools = result.get("pools")
        if pools is not None:
            if not isinstance(pools, list):
                raise _UnsupportedLootShape("loot table pools must be a list")
            converted = []
            for pool in pools:
                item, count = self._down_pool(pool, context, path, diagnostics)
                converted.append(item)
                changed += count
            result["pools"] = converted
        return result, changed

    def _down_pool(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot pool must be an object")
        result = dict(value)
        changed = 0
        if "condition" in result:
            result["conditions"], count = self._down_condition_list(result.pop("condition"), context, path, diagnostics)
            changed += count
        if "modifier" in result:
            result["functions"], count = self._down_function_list(result.pop("modifier"), context, path, diagnostics)
            changed += count
        entries = result.get("entries")
        if entries is not None:
            if not isinstance(entries, list):
                raise _UnsupportedLootShape("loot pool entries must be a list")
            converted_entries = []
            for entry in entries:
                item, count = self._down_entry(entry, context, path, diagnostics)
                converted_entries.append(item)
                changed += count
            result["entries"] = converted_entries
        return result, changed

    def _down_entry(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot entry must be an object")
        result = dict(value)
        changed = 0
        entry_type = _discriminator(result, "type")
        if "condition" in result:
            result["conditions"], count = self._down_condition_list(result.pop("condition"), context, path, diagnostics)
            changed += count
        if "modifier" in result:
            result["functions"], count = self._down_function_list(result.pop("modifier"), context, path, diagnostics)
            changed += count
        if entry_type in {"minecraft:tag", "tag"} and "items" in result:
            result["name"] = result.pop("items")
            changed += 1
        if entry_type in {"minecraft:loot_table", "loot_table"} and isinstance(result.get("value"), dict):
            result["value"], count = self._down_table(result["value"], context, path, diagnostics)
            changed += count
        if entry_type in {"minecraft:loot_table", "loot_table"}:
            if result.pop("expand", None) is not None:
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNSUPPORTED,
                        code="loot-table-entry-expand-cannot-downgrade",
                        message="The loot_table entry expand field was added in 26.3",
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                    )
                )
            value_field = result.get("value")
            if isinstance(value_field, list):
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNSUPPORTED,
                        code="loot-table-entry-list-cannot-downgrade",
                        message="Pre-26.3 loot_table entries accept a single loot table id",
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                    )
                )
        children = result.get("children")
        if children is not None:
            if not isinstance(children, list):
                raise _UnsupportedLootShape("loot entry children must be a list")
            converted_children = []
            for child in children:
                item, count = self._down_entry(child, context, path, diagnostics)
                converted_children.append(item)
                changed += count
            result["children"] = converted_children
        return result, changed

    def _down_condition_list(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[list[Any], int]:
        if isinstance(value, dict) and _discriminator(value, "type") in {"minecraft:all_of", "all_of"}:
            extra = set(value) - {"type", "terms"}
            terms = value.get("terms")
            if not extra and isinstance(terms, list):
                converted = []
                changed = 1
                for item in terms:
                    entry, count = self._down_condition(item, context, path, diagnostics)
                    converted.append(entry)
                    changed += count
                return converted, changed
        converted, count = self._down_condition(value, context, path, diagnostics)
        return [converted], count + 1

    def _down_condition(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if isinstance(value, str):
            return {"condition": "minecraft:reference", "name": value}, 1
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot condition must be an object or an id")
        result = dict(value)
        changed = 0
        if "type" in result:
            result["condition"] = result.pop("type")
            changed += 1
        terms = result.get("terms")
        if terms is not None:
            if not isinstance(terms, list):
                raise _UnsupportedLootShape("terms must be a list")
            converted = []
            for item in terms:
                entry, count = self._down_condition(item, context, path, diagnostics)
                converted.append(entry)
                changed += count
            result["terms"] = converted
        if "term" in result:
            result["term"], count = self._down_condition(result["term"], context, path, diagnostics)
            changed += count
        return result, changed

    def _down_function_slot(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if isinstance(value, list):
            return self._down_function_list(value, context, path, diagnostics)
        if isinstance(value, str):
            return {"function": "minecraft:reference", "name": value}, 1
        return self._down_function(value, context, path, diagnostics)

    def _down_function_list(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[list[Any], int]:
        if isinstance(value, dict):
            item, count = self._down_function(value, context, path, diagnostics)
            return [item], count
        if isinstance(value, str):
            return [{"function": "minecraft:reference", "name": value}], 1
        if not isinstance(value, list):
            raise _UnsupportedLootShape("loot functions must be an object, an id, or a list")
        converted = []
        changed = 0
        for item in value:
            entry, count = self._down_function_slot(item, context, path, diagnostics)
            converted.append(entry)
            changed += count
        return converted, changed

    def _down_function(
        self, value: Any, context: MigrationContext, path: Path, diagnostics: list[Diagnostic]
    ) -> tuple[Any, int]:
        if not isinstance(value, dict):
            raise _UnsupportedLootShape("A loot function must be an object")
        result = dict(value)
        changed = 0
        function_id = _discriminator(result, "type")
        if function_id in {"minecraft:set_loot_table", "set_loot_table"} and "loot_table_id" in result:
            result["name"] = result.pop("loot_table_id")
            changed += 1
        if "type" in result:
            result["function"] = result.pop("type")
            changed += 1
        if "condition" in result:
            result["conditions"], count = self._down_condition_list(result.pop("condition"), context, path, diagnostics)
            changed += count
        for key in ("on_pass", "on_fail"):
            if key in result:
                result[key], count = self._down_function_slot(result[key], context, path, diagnostics)
                changed += count
        if function_id in {"minecraft:sequence", "sequence"} and "functions" in result:
            result["functions"], count = self._down_function_list(result["functions"], context, path, diagnostics)
            changed += count
        # See the upgrade direction: ``modifier`` inside a function is always a nested slot.
        if "modifier" in result and isinstance(result["modifier"], dict | list):
            result["modifier"], count = self._down_function_slot(result["modifier"], context, path, diagnostics)
            changed += count
        if function_id in {"minecraft:set_contents", "set_contents"} and isinstance(result.get("entries"), list):
            converted_entries = []
            for entry in result["entries"]:
                item, count = self._down_entry(entry, context, path, diagnostics)
                converted_entries.append(item)
                changed += count
            result["entries"] = converted_entries
        return result, changed


class BlockEntitySherdsSnbtRule:
    """Convert the decorated-pot ``sherds`` field inside ``data merge block`` commands."""

    id = "command.block-entity-pot-sherds@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.mcfunction")):
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            output: list[str] = []
            local_changed = 0
            for line_number, line in enumerate(lines, start=1):
                body = line.rstrip("\r\n")
                suffix = line[len(body) :]
                if not body.strip() or body.lstrip().startswith("#"):
                    output.append(line)
                    continue
                parsed = parse_command_line(body)
                replacements: list[tuple[int, int, str]] = []
                for segment in iter_execute_segments(parsed):
                    values = tuple(token.value for token in segment)
                    # ``setblock`` has no NBT argument since 1.13, so only ``data merge
                    # block`` can carry block-entity data.  The position is three tokens,
                    # so the compound is the first brace-prefixed token after it.
                    if len(values) < 5 or values[:3] != ("data", "merge", "block"):
                        continue
                    index = next(
                        (i for i in range(4, len(values)) if values[i].startswith("{")),
                        None,
                    )
                    if index is None:
                        index = next((i for i in range(4, len(values)) if "$(" in values[i]), None)
                    if index is None:
                        continue
                    token = segment[index]
                    if "$(" in token.value and not macro_placeholders_are_quoted(token.value):
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="macro-block-nbt-needs-runtime-parse",
                                message=(
                                    "A macro controls block-entity NBT structure; only placeholders "
                                    "contained inside quoted scalar values can be statically migrated"
                                ),
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                            )
                        )
                        continue
                    try:
                        value = loads_snbt(token.value)
                    except SnbtError as exc:
                        diagnostics.append(
                            Diagnostic(
                                Severity.ERROR,
                                "block-snbt-parse-failed",
                                str(exc),
                                path=context.relative(path),
                                line=line_number,
                                compatibility=Compatibility.UNKNOWN,
                                rule_id=self.id,
                            )
                        )
                        continue
                    if not isinstance(value, dict) or "sherds" not in value:
                        continue
                    converted, local = _convert_sherds(
                        value["sherds"],
                        upgrading=upgrading,
                        context=context,
                        path=path,
                        rule_id=self.id,
                        diagnostics=diagnostics,
                    )
                    if converted is None:
                        continue
                    value["sherds"] = converted
                    replacements.append((token.start, token.end, dumps_snbt(value)))
                    local_changed += local
                output.append(parsed.replace_spans(replacements) + suffix)
            if local_changed:
                path.write_text("".join(output), encoding="utf-8")
                changed_files += 1
                changed_nodes += local_changed
        return RuleResult(
            MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes),
            diagnostics,
        )


class BlockEntitySherdsNbtRule:
    """Convert the decorated-pot ``sherds`` field inside structure block entities."""

    id = "structure.block-entity-pot-sherds@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.nbt")):
            relative = "/" + context.relative(path)
            if "/structure/" not in relative:
                continue
            try:
                document = nbt.load_path(path)
                root = nbt.compound(document.root)
                if root is None:
                    raise nbt.NbtError("Structure root is not a compound")
                blocks_tag = root.get("blocks")
                blocks = nbt.list_values(blocks_tag, nbt.TAG_COMPOUND) if blocks_tag else None
                if blocks is None:
                    continue
                local_changed = 0
                for entry_tag in blocks:
                    entry = nbt.compound(entry_tag)
                    nbt_tag = entry.get("nbt") if entry else None
                    block_entity = nbt.compound(nbt_tag) if nbt_tag else None
                    if block_entity is None:
                        continue
                    id_tag = block_entity.get("id")
                    if not id_tag or id_tag.type_id != nbt.TAG_STRING:
                        continue
                    if id_tag.value != "minecraft:decorated_pot":
                        continue
                    sherds_tag = block_entity.get("sherds")
                    if sherds_tag is None:
                        continue
                    converted, local = _convert_sherds(
                        sherds_tag,
                        upgrading=upgrading,
                        context=context,
                        path=path,
                        rule_id=self.id,
                        diagnostics=diagnostics,
                        nbt_tags=True,
                    )
                    if converted is None:
                        continue
                    block_entity["sherds"] = converted
                    local_changed += local
                if local_changed:
                    nbt.dump_path(path, document)
                    changed_files += 1
                    changed_nodes += local_changed
            except (OSError, nbt.NbtError, struct.error) as exc:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "structure-block-nbt-migration-failed",
                        str(exc),
                        path=context.relative(path),
                        compatibility=Compatibility.UNKNOWN,
                        rule_id=self.id,
                    )
                )
        return RuleResult(
            MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes),
            diagnostics,
        )


def _convert_sherds(
    value: Any,
    *,
    upgrading: bool,
    context: MigrationContext,
    path: Path,
    rule_id: str,
    diagnostics: list[Diagnostic],
    nbt_tags: bool = False,
) -> tuple[Any, int]:
    """Convert ``sherds`` between the legacy four-entry list and the 26.3 face map."""

    def item_id(entry: Any) -> str | None:
        """Return the bare item id of one face entry, or None when it carries data."""

        if nbt_tags:
            if not isinstance(entry, nbt.NbtTag):
                return None
            if entry.type_id == nbt.TAG_STRING and isinstance(entry.value, str):
                return entry.value
            if entry.type_id != nbt.TAG_COMPOUND:
                return None
            payload = nbt.compound(entry)
            if payload is None or "components" in payload:
                return None
            count_tag = payload.get("count")
            if count_tag is not None and count_tag.type_id == nbt.TAG_INT and count_tag.value != 1:
                return None
            id_tag = payload.get("id")
            return id_tag.value if id_tag and id_tag.type_id == nbt.TAG_STRING else None
        if isinstance(entry, str):
            return entry
        if not isinstance(entry, dict) or entry.get("components"):
            return None
        entry_id = entry.get("id")
        if entry.get("count", 1) != 1 or not isinstance(entry_id, str):
            return None
        return entry_id

    def make_face(item: str) -> Any:
        if nbt_tags:
            return nbt.NbtTag(
                nbt.TAG_COMPOUND,
                {"id": nbt.NbtTag(nbt.TAG_STRING, item)},
            )
        return {"id": item}

    def is_sequence(candidate: Any) -> bool:
        if nbt_tags:
            return isinstance(candidate, nbt.NbtTag) and candidate.type_id == nbt.TAG_LIST
        return isinstance(candidate, list)

    def sequence_values(candidate: Any) -> list[Any] | None:
        if nbt_tags:
            return nbt.list_values(candidate)
        return list(candidate)

    def is_mapping(candidate: Any) -> bool:
        if nbt_tags:
            return isinstance(candidate, nbt.NbtTag) and candidate.type_id == nbt.TAG_COMPOUND
        return isinstance(candidate, dict)

    def mapping_items(candidate: Any) -> dict[str, Any] | None:
        if nbt_tags:
            return nbt.compound(candidate)
        return dict(candidate)

    def report(code: str, message: str, compatibility: Compatibility, **details: Any) -> None:
        diagnostics.append(
            policy_diagnostic(
                context,
                compatibility=compatibility,
                code=code,
                message=message,
                path=context.relative(path),
                line=None,
                rule_id=rule_id,
                details=details,
            )
        )

    if upgrading:
        if not is_sequence(value):
            report(
                "pot-sherds-shape-unknown",
                "Only the legacy four-entry sherds list can be converted",
                Compatibility.UNKNOWN,
            )
            return None, 0
        entries = sequence_values(value) or []
        if len(entries) > len(_POT_FACES):
            report(
                "pot-sherds-shape-unknown",
                "The legacy sherds list has more than four entries",
                Compatibility.UNKNOWN,
            )
            return None, 0
        ids: list[str | None] = [item_id(entry) for entry in entries]
        if any(value is None for value in ids):
            report(
                "pot-sherds-entry-not-an-id",
                "Legacy sherds entries must be plain item IDs",
                Compatibility.UNKNOWN,
            )
            return None, 0
        if nbt_tags:
            faces: dict[str, nbt.NbtTag] = {}
            for index, face in enumerate(_POT_FACES):
                item = ids[index] if index < len(ids) else _BRICK
                faces[face] = make_face(item or _BRICK)
            return nbt.NbtTag(nbt.TAG_COMPOUND, faces), 1
        json_faces: dict[str, Any] = {}
        for index, face in enumerate(_POT_FACES):
            item = ids[index] if index < len(ids) else _BRICK
            json_faces[face] = make_face(item or _BRICK)
        return json_faces, 1

    if not is_mapping(value):
        report(
            "pot-sherds-shape-unknown",
            "Only the 26.3 sherds face map can be converted",
            Compatibility.UNKNOWN,
        )
        return None, 0
    faces_map = mapping_items(value)
    if faces_map is None:
        report("pot-sherds-shape-unknown", "The sherds face map is not readable", Compatibility.UNKNOWN)
        return None, 0
    unknown_faces = sorted(set(faces_map) - set(_POT_FACES))
    if unknown_faces:
        report(
            "pot-sherds-unknown-face",
            "The face map contains keys that have no legacy equivalent",
            Compatibility.UNKNOWN,
            faces=unknown_faces,
        )
        return None, 0
    ids = []
    for face in _POT_FACES:
        if face not in faces_map:
            report(
                "pot-sherds-empty-face",
                "An empty pot face drops nothing in 26.3; older releases always drop a brick",
                Compatibility.LOSSY,
                face=face,
            )
            ids.append(_BRICK)
            continue
        item = item_id(faces_map[face])
        if item is None:
            report(
                "pot-sherds-face-data-cannot-downgrade",
                "Older sherds entries store item IDs only, not item stacks",
                Compatibility.UNSUPPORTED,
                face=face,
            )
            return None, 0
        ids.append(item)
    if nbt_tags:
        return nbt.NbtTag(nbt.TAG_LIST, nbt.NbtList(nbt.TAG_STRING, [nbt.NbtTag(nbt.TAG_STRING, i) for i in ids])), 1
    return list(ids), 1


class NumberProviderSumRule:
    """Rewrite the 26.3 ``sum`` number provider and refuse the registry split.

    26.1 added ``minecraft:sum`` with a ``summands`` field; 26.3 replaced the
    ``minecraft:number_provider`` registry with ``context_int_provider`` and
    ``context_float_provider`` and its released ``minecraft:add`` provider reads ``inputs``.
    The rewrite is gated on the provider-specific field, because ``add`` is also a density
    function and a level-based value type, both of which use different fields.
    """

    id = "number-provider.sum-to-add@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            changed = 0

            def visit(node: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                provider_type = _discriminator(node, "type")
                if upgrading:
                    if provider_type != "minecraft:sum" or "summands" not in node:
                        return node
                    result = dict(node)
                    result["type"] = "minecraft:add"
                    result["inputs"] = result.pop("summands")
                else:
                    if provider_type != "minecraft:add" or "inputs" not in node:
                        return node
                    result = dict(node)
                    result["type"] = "minecraft:sum"
                    result["summands"] = result.pop("inputs")
                changed += 1
                return result

            return _walk_maps(value, visit), changed, []

        result = transform_json_files(context, self.id, transform)
        notes: tuple[str, ...] = ()
        if result.record.changed_files:
            notes = (
                "26.3 splits number providers into integer and float registries, so a fractional "
                "operand in an integer context is evaluated with integer arithmetic",
            )
        record = MigrationRecord(
            self.id,
            Compatibility.LOSSLESS,
            result.record.changed_files,
            result.record.changed_nodes,
            notes=notes,
        )
        return RuleResult(record, [*result.diagnostics, *self._registry_split(context, upgrading)])

    def _registry_split(self, context: MigrationContext, upgrading: bool) -> list[Diagnostic]:
        """Refuse a pack that still ships the pre-26.3 number-provider registry."""

        if not upgrading:
            return []
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.json")):
            relative = "/" + context.relative(path)
            if "/number_provider/" not in relative:
                continue
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNSUPPORTED,
                    code="number-provider-registry-split",
                    message=(
                        "26.3 replaced the number_provider registry with context_int_provider and "
                        "context_float_provider; DPCompat cannot decide which one an entry belongs to, "
                        "so this target needs an author fallback"
                    ),
                    path=context.relative(path),
                    line=None,
                    rule_id=self.id,
                )
            )
        return diagnostics


class _UnsupportedLootShape(Exception):
    """Raised when a loot resource does not match either documented schema."""


class WorldgenSchemaRule:
    """Refuse the 26.3 world-generation rewrite instead of approximating it."""

    id = "worldgen.registry-and-config@121.0"
    boundary = BOUNDARY

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        changed_files = 0
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.json")):
            relative = "/" + context.relative(path)
            if "/worldgen/" not in relative:
                continue
            legacy = "/worldgen/configured_feature/" in relative or "/worldgen/configured_carver/" in relative
            modern = any(
                marker in relative
                for marker in (
                    "/worldgen/feature/",
                    "/worldgen/carver/",
                    "/worldgen/block_state_provider/",
                    "/worldgen/material_rule/",
                    "/worldgen/material_condition/",
                )
            )
            if (upgrading and legacy) or (not upgrading and modern):
                changed_files += 1
                diagnostics.append(
                    policy_diagnostic(
                        context,
                        compatibility=Compatibility.UNSUPPORTED,
                        code="worldgen-schema-rewrite-required",
                        message=(
                            "26.3 moved the feature and carver registries and inlined their config, and "
                            "also switched density functions to single precision; world generation is "
                            "not equivalent, so this target needs an author fallback"
                        ),
                        path=context.relative(path),
                        line=None,
                        rule_id=self.id,
                    )
                )
        if changed_files == 0:
            return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0, 0))
        return RuleResult(
            MigrationRecord(self.id, Compatibility.UNSUPPORTED, changed_files=0, changed_nodes=changed_files),
            diagnostics,
        )
