"""Migrate selected loot, timeline, and test-environment resource schemas.

Each rule owns a single format boundary and states the conditions under which its reverse
transformation is lossless.  New-only behavior such as a non-empty ``on_fail`` branch blocks
unsafe downgrade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Compatibility, Diagnostic, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic, transform_json_files


class FilteredLootRule:
    """Migrate filtered loot modifier branches across format 94.1."""

    id = "loot.filtered-on-pass-on-fail@94.1"
    boundary = PackFormat(94, 1)

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
                function_id = result.get("function")
                is_filtered = function_id in {"minecraft:filtered", "filtered"}
                if not is_filtered:
                    return result
                if upgrading and "modifier" in result and "on_pass" not in result:
                    result["on_pass"] = result.pop("modifier")
                    changed += 1
                elif not upgrading:
                    if "on_fail" in result:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="filtered-on-fail-cannot-downgrade",
                                message="Older filtered item modifiers have no on_fail branch",
                                path=context.relative(path),
                                line=None,
                                rule_id=self.id,
                            )
                        )
                    if "on_pass" in result and "modifier" not in result:
                        result["modifier"] = result.pop("on_pass")
                        changed += 1
                return result

            return walk(value), changed, diagnostics

        return transform_json_files(context, self.id, transform)


class TimelineClockRule:
    """Migrate timeline clock defaults across format 101.1."""

    id = "timeline.clock-and-time-markers@101.1"
    boundary = PackFormat(101, 1)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            if "/timeline/" not in "/" + context.relative(path):
                return value, 0, []
            if not isinstance(value, dict):
                return value, 0, []
            result = dict(value)
            diagnostics: list[Diagnostic] = []
            changed = 0
            if upgrading:
                if "clock" not in result:
                    result["clock"] = "minecraft:overworld"
                    changed += 1
            else:
                if result.get("time_markers"):
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="timeline-time-markers-cannot-downgrade",
                            message="Timeline time_markers do not exist before 26.1",
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                        )
                    )
                clock = result.get("clock")
                if clock in {None, "minecraft:overworld", "overworld"}:
                    if "clock" in result:
                        result.pop("clock")
                        changed += 1
                else:
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="timeline-custom-clock-cannot-downgrade",
                            message="A custom timeline clock has no pre-26.1 equivalent",
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                            details={"clock": clock},
                        )
                    )
            return result, changed, diagnostics

        return transform_json_files(context, self.id, transform)


class TestEnvironmentClockRule:
    """Migrate test-environment clock defaults across format 101.1."""

    id = "test-environment.time-of-day-to-clock-time@101.1"
    boundary = PackFormat(101, 1)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target

        def transform(value: Any, path: Path) -> tuple[Any, int, list[Diagnostic]]:
            relative = "/" + context.relative(path)
            if "/test_environment/" not in relative or not isinstance(value, dict):
                return value, 0, []
            result = dict(value)
            changed = 0
            diagnostics: list[Diagnostic] = []
            if upgrading and "time_of_day" in result and "clock_time" not in result:
                result["clock_time"] = {
                    "clock": "minecraft:overworld",
                    "time": result.pop("time_of_day"),
                }
                changed += 1
            elif not upgrading and "clock_time" in result:
                clock_time = result["clock_time"]
                if (
                    isinstance(clock_time, dict)
                    and clock_time.get("clock") in {None, "minecraft:overworld", "overworld"}
                    and "time" in clock_time
                ):
                    result["time_of_day"] = clock_time["time"]
                    result.pop("clock_time")
                    changed += 1
                else:
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=Compatibility.UNSUPPORTED,
                            code="test-environment-clock-cannot-downgrade",
                            message="Only the overworld clock can be represented by the old time_of_day field",
                            path=context.relative(path),
                            line=None,
                            rule_id=self.id,
                        )
                    )
            return result, changed, diagnostics

        return transform_json_files(context, self.id, transform)
