"""Migrate the supported subset of item-component schema changes.

Item components contain many component-specific sublanguages.  This module transforms only
structures whose context and reversible mapping are known; command component patches outside
that subset remain scanner diagnostics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Compatibility, Diagnostic, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic, transform_json_files

_SIMPLIFIED_COMPONENTS = {
    "minecraft:attribute_modifiers": "modifiers",
    "minecraft:dyed_color": "rgb",
    "minecraft:can_place_on": "predicates",
    "minecraft:can_break": "predicates",
    "minecraft:enchantments": "levels",
    "minecraft:stored_enchantments": "levels",
}


def _is_component_map(value: dict[str, Any]) -> bool:
    return bool(value) and sum(1 for key in value if ":" in key) >= max(1, len(value) // 2)


def _upgrade_map(
    context: MigrationContext, value: dict[str, Any], path: Path, rule_id: str
) -> tuple[dict[str, Any], int, list[Diagnostic]]:
    result = dict(value)
    changed = 0
    diagnostics: list[Diagnostic] = []
    tooltip = result.get("minecraft:tooltip_display")
    tooltip_result = dict(tooltip) if isinstance(tooltip, dict) else {}
    hidden = tooltip_result.get("hidden_components")
    hidden_components = list(hidden) if isinstance(hidden, list) else []

    if "minecraft:hide_tooltip" in result:
        result.pop("minecraft:hide_tooltip")
        tooltip_result["hide_tooltip"] = True
        changed += 1

    if "minecraft:hide_additional_tooltip" in result:
        # hide_additional_tooltip was removed in 1.21.5.  Its replacement lists every
        # tooltip-contributing component in tooltip_display.hidden_components, which
        # depends on the co-present components and cannot be inferred safely.
        diagnostics.append(
            policy_diagnostic(
                context,
                compatibility=Compatibility.UNKNOWN,
                code="hide-additional-tooltip-cannot-upgrade",
                message=(
                    "minecraft:hide_additional_tooltip was removed in 1.21.5; list the affected "
                    "components explicitly in tooltip_display.hidden_components instead"
                ),
                path=context.relative(path),
                line=None,
                rule_id=rule_id,
            )
        )

    for component_id, inner_key in _SIMPLIFIED_COMPONENTS.items():
        component = result.get(component_id)
        if not isinstance(component, dict):
            continue
        component = dict(component)
        show = component.pop("show_in_tooltip", None)
        if show is False and component_id not in hidden_components:
            hidden_components.append(component_id)
        if show is not None:
            changed += 1
        if inner_key in component and set(component).issubset({inner_key}):
            result[component_id] = component[inner_key]
            changed += 1
        else:
            result[component_id] = component

    # Other components also lost their local show_in_tooltip flag.
    for component_id, component in list(result.items()):
        if component_id in _SIMPLIFIED_COMPONENTS or component_id == "minecraft:tooltip_display":
            continue
        if isinstance(component, dict) and "show_in_tooltip" in component:
            component = dict(component)
            show = component.pop("show_in_tooltip")
            if show is False and component_id not in hidden_components:
                hidden_components.append(component_id)
            result[component_id] = component
            changed += 1

    if hidden_components:
        tooltip_result["hidden_components"] = hidden_components
    if tooltip_result and result.get("minecraft:tooltip_display") != tooltip_result:
        result["minecraft:tooltip_display"] = tooltip_result
        changed += 1
    return result, changed, diagnostics


def _downgrade_map(
    context: MigrationContext, value: dict[str, Any], path: Path, rule_id: str
) -> tuple[dict[str, Any], int, list[Diagnostic]]:
    result = dict(value)
    changed = 0
    diagnostics: list[Diagnostic] = []
    tooltip = result.pop("minecraft:tooltip_display", None)
    hidden_components: set[str] = set()
    if isinstance(tooltip, dict):
        if tooltip.get("hide_tooltip") is True:
            result["minecraft:hide_tooltip"] = {}
            changed += 1
        hidden = tooltip.get("hidden_components")
        if isinstance(hidden, list):
            hidden_components = {item for item in hidden if isinstance(item, str)}
        unknown = set(tooltip) - {"hide_tooltip", "hidden_components"}
        if unknown:
            diagnostics.append(
                policy_diagnostic(
                    context,
                    compatibility=Compatibility.UNKNOWN,
                    code="tooltip-display-unknown-fields",
                    message="Unknown tooltip_display fields cannot be mapped to 1.21.4",
                    path=context.relative(path),
                    line=None,
                    rule_id=rule_id,
                    details={"fields": sorted(unknown)},
                )
            )
        changed += 1

    for component_id, inner_key in _SIMPLIFIED_COMPONENTS.items():
        if component_id not in result:
            continue
        component = result[component_id]
        if isinstance(component, dict) and inner_key in component and "show_in_tooltip" in component:
            continue
        wrapped = {inner_key: component}
        if component_id in hidden_components:
            wrapped["show_in_tooltip"] = False
            hidden_components.remove(component_id)
        result[component_id] = wrapped
        changed += 1

    for component_id in list(hidden_components):
        component = result.get(component_id)
        if isinstance(component, dict):
            component = dict(component)
            component["show_in_tooltip"] = False
            result[component_id] = component
            hidden_components.remove(component_id)
            changed += 1

    if hidden_components:
        diagnostics.append(
            policy_diagnostic(
                context,
                compatibility=Compatibility.LOSSY,
                code="hidden-component-cannot-downgrade",
                message="Some hidden components use scalar forms that cannot carry show_in_tooltip",
                path=context.relative(path),
                line=None,
                rule_id=rule_id,
                details={"components": sorted(hidden_components)},
            )
        )
    return result, changed, diagnostics


class ItemTooltipComponentsRule:
    """Consolidate known tooltip flags into the format-71 display component."""

    id = "item-components.tooltip-display-and-simplification@71"
    boundary = PackFormat(71)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            diagnostics: list[Diagnostic] = []
            changed = 0

            def walk(node: Any, parent_key: str | None = None) -> Any:
                nonlocal changed
                if isinstance(node, list):
                    return [walk(item, parent_key) for item in node]
                if not isinstance(node, dict):
                    return node
                result = {key: walk(item, key) for key, item in node.items()}
                if parent_key == "components" or _is_component_map(result):
                    if upgrading:
                        result, count, local = _upgrade_map(context, result, path, self.id)
                        changed += count
                        diagnostics.extend(local)
                    else:
                        result, count, local = _downgrade_map(context, result, path, self.id)
                        changed += count
                        diagnostics.extend(local)
                return result

            return walk(value), changed, diagnostics

        return transform_json_files(context, self.id, transform)
