"""Migrate reviewed recipe and time-check schema changes.

The 26.x recipe changes are implemented as narrow structural subsets.  Rules refuse ambiguous
forms instead of guessing how a newer recipe should behave in an older release.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Compatibility, Diagnostic, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic, transform_json_files

_COOKING_TYPES = {
    "minecraft:smelting",
    "minecraft:blasting",
    "minecraft:smoking",
    "minecraft:campfire_cooking",
}
_NO_BOOK_TYPES = {
    "minecraft:stonecutting",
    "minecraft:smithing_transform",
    "minecraft:smithing_trim",
}
_SHOW_NOTIFICATION_NEW = {
    "minecraft:crafting_shapeless",
    "minecraft:crafting_transmute",
    *_COOKING_TYPES,
    *_NO_BOOK_TYPES,
}
_NEW_RECIPE_TYPES = {
    "minecraft:crafting_dye",
    "minecraft:crafting_imbue",
}


class Recipe26Rule:
    """Migrate the safely reversible subset of 26.1 recipe schema changes."""

    id = "recipe.syntax-and-types@101.1"
    boundary = PackFormat(101, 1)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            relative = "/" + context.relative(path)
            if "/recipe/" not in relative or not isinstance(value, dict):
                return value, 0, []
            result = dict(value)
            recipe_type = result.get("type")
            diagnostics: list[Diagnostic] = []
            changed = 0

            if upgrading:
                if recipe_type in _NO_BOOK_TYPES and "group" in result:
                    result.pop("group")
                    changed += 1
                if recipe_type == "minecraft:crafting_special_mapcloning":
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="mapcloning-needs-explicit-transmute-recipe",
                            message=(
                                "26.1 removed crafting_special_mapcloning. Its transmute replacement "
                                "needs explicit ingredients/result and cannot be inferred from an empty special recipe"
                            ),
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                        )
                    )
            else:
                if recipe_type in _NEW_RECIPE_TYPES:
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="new-recipe-type-cannot-downgrade",
                            message=f"{recipe_type} has no general pre-26.1 data-driven equivalent",
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                        )
                    )
                show = result.get("show_notification")
                if recipe_type in _SHOW_NOTIFICATION_NEW and recipe_type != "minecraft:crafting_shaped":
                    if show is True:
                        result.pop("show_notification")
                        changed += 1
                    elif show is False:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.LOSSY,
                                code="show-notification-cannot-downgrade",
                                message=("This recipe type cannot suppress unlock notifications before 26.1"),
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                if recipe_type in _COOKING_TYPES and isinstance(result.get("result"), dict):
                    recipe_result = result["result"]
                    if (
                        set(recipe_result).issubset({"id", "count"})
                        and recipe_result.get("count", 1) == 1
                        and isinstance(recipe_result.get("id"), str)
                    ):
                        result["result"] = recipe_result["id"]
                        changed += 1
                    elif recipe_result.get("count", 1) != 1:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="cooking-result-count-cannot-downgrade",
                                message="Cooking result counts other than one are unavailable before 26.1",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                    else:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="cooking-result-fields-cannot-downgrade",
                                message=(
                                    "Cooking result objects with fields beyond id/count cannot be "
                                    "represented before 26.1"
                                ),
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                                details={"fields": sorted(set(recipe_result) - {"id", "count"})},
                            )
                        )
                if recipe_type == "minecraft:crafting_transmute":
                    material_count = result.get("material_count")
                    add_count = result.get("add_material_count_to_result")
                    if material_count in (None, 1, {"min": 1, "max": 1}, [1, 1]):
                        if "material_count" in result:
                            result.pop("material_count")
                            changed += 1
                    else:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="transmute-material-count-cannot-downgrade",
                                message="Multi-material crafting_transmute recipes require 26.1",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                    if add_count is False:
                        result.pop("add_material_count_to_result", None)
                        changed += 1
                    elif add_count is True:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="transmute-add-count-cannot-downgrade",
                                message="add_material_count_to_result requires 26.1",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
            return result, changed, diagnostics

        return transform_json_files(context, self.id, transform)


class TimeCheckClockRule:
    """Add or remove the default clock for supported time-check predicates."""

    id = "predicate.time-check-clock@101.1"
    boundary = PackFormat(101, 1)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            diagnostics: list[Diagnostic] = []
            changed = 0

            def walk(node: Any) -> Any:
                nonlocal changed
                if isinstance(node, list):
                    return [walk(item) for item in node]
                if not isinstance(node, dict):
                    return node
                result = {key: walk(item) for key, item in node.items()}
                if result.get("condition") not in {"minecraft:time_check", "time_check"}:
                    return result
                if upgrading and "clock" not in result:
                    result["clock"] = "minecraft:overworld"
                    changed += 1
                elif not upgrading and "clock" in result:
                    if result["clock"] in {"minecraft:overworld", "overworld"}:
                        result.pop("clock")
                        changed += 1
                    else:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="time-check-custom-clock-cannot-downgrade",
                                message="A custom world clock has no pre-26.1 time_check equivalent",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                                details={"clock": result["clock"]},
                            )
                        )
                return result

            return walk(value), changed, diagnostics

        return transform_json_files(context, self.id, transform)
