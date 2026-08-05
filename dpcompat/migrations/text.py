"""Migrate text-component event schemas in known JSON, SNBT, and command positions.

Text-looking dictionaries are not automatically text components.  Call-site context is part of
the safety proof, especially for custom storage data and macro-expanded command arguments.
"""

from __future__ import annotations

import json
from typing import Any

from ..commands import iter_execute_segments, macro_placeholders_are_quoted, parse_command_line
from ..jsonutil import JsonNormalizationError, dump_path, load_path, loads_lenient
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from ..snbt import (
    SnbtError,
    to_json_compatible,
)
from ..snbt import (
    dumps as dumps_snbt,
)
from ..snbt import (
    loads as loads_snbt,
)
from ..text_components import (
    TextComponentMigrationError,
    downgrade_component,
    upgrade_component,
)
from .base import MigrationContext, RuleResult, crosses

_BOUNDARY = PackFormat(71)
_TEXT_KEYS = {
    "display_name",
    "name",
    "title",
    "subtitle",
    "description",
    "message",
    "prompt",
    "label",
    "text",
}


def _looks_like_component(value: Any) -> bool:
    if isinstance(value, str):
        return False
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, (str, dict, list)) for item in value)
    if not isinstance(value, dict):
        return False
    component_markers = {
        "text",
        "translate",
        "score",
        "selector",
        "keybind",
        "nbt",
        "extra",
        "clickEvent",
        "hoverEvent",
        "click_event",
        "hover_event",
    }
    return bool(component_markers.intersection(value))


def _walk(value: Any, upgrading: bool, key: str | None = None) -> tuple[Any, int]:
    transform = upgrade_component if upgrading else downgrade_component
    if _looks_like_component(value) and (
        key in _TEXT_KEYS
        or (isinstance(value, dict) and ({"clickEvent", "hoverEvent", "click_event", "hover_event"} & value.keys()))
    ):
        migrated = transform(value)
        return migrated, int(migrated != value)
    if isinstance(value, list):
        total = 0
        list_output: list[Any] = []
        for item in value:
            migrated, changed = _walk(item, upgrading, key)
            list_output.append(migrated)
            total += changed
        return list_output, total
    if isinstance(value, dict):
        total = 0
        dict_output: dict[str, Any] = {}
        for child_key, item in value.items():
            migrated, changed = _walk(item, upgrading, child_key)
            dict_output[child_key] = migrated
            total += changed
        return dict_output, total
    return value, 0


def _component_token_index(values: tuple[str, ...]) -> int | None:
    if not values:
        return None
    head = values[0]
    if head == "tellraw" and len(values) >= 3:
        return 2
    if head == "title" and len(values) >= 4 and values[2] in {"title", "subtitle", "actionbar"}:
        return 3
    if len(values) >= 5 and head == "bossbar" and values[1] == "set" and values[3] == "name":
        return 4
    if (
        head == "team"
        and len(values) >= 5
        and values[1] == "modify"
        and values[3] in {"displayName", "prefix", "suffix"}
    ):
        return 4
    return None


def _parse_component_token(raw: str) -> tuple[Any, str]:
    try:
        return loads_snbt(raw), "snbt"
    except SnbtError:
        return loads_lenient(raw), "json"


class TextComponentRule:
    """Migrate text event objects only in established component contexts."""

    id = "text-component.events-and-inline-snbt@71"

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, _BOUNDARY)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < _BOUNDARY <= context.target
        transform = upgrade_component if upgrading else downgrade_component
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []

        for path in sorted((context.root / "data").rglob("*.json")):
            try:
                value = load_path(path)
                migrated, changed = _walk(value, upgrading)
                if changed:
                    dump_path(path, migrated)
                    changed_files += 1
                    changed_nodes += changed
            except (OSError, JsonNormalizationError, TextComponentMigrationError) as exc:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "text-component-json-failed",
                        str(exc),
                        path=context.relative(path),
                        compatibility=Compatibility.UNSUPPORTED,
                        rule_id=self.id,
                    )
                )

        for path in sorted((context.root / "data").rglob("*.mcfunction")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                continue
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
                    index = _component_token_index(values)
                    if index is None or index >= len(segment):
                        continue
                    token = segment[index]
                    if "$(" in token.value and not macro_placeholders_are_quoted(token.value):
                        diagnostics.append(
                            Diagnostic(
                                Severity.ERROR,
                                "macro-component-needs-runtime-parse",
                                (
                                    "A macro controls the structure of a text component; only placeholders "
                                    "contained inside quoted scalar values can be statically migrated"
                                ),
                                path=context.relative(path),
                                line=line_number,
                                compatibility=Compatibility.UNKNOWN,
                                rule_id=self.id,
                            )
                        )
                        continue
                    try:
                        value, _syntax = _parse_component_token(token.value)
                        migrated = transform(value)
                        if migrated != value:
                            replacement = (
                                dumps_snbt(migrated)
                                if upgrading
                                else json.dumps(
                                    to_json_compatible(migrated),
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                            )
                            replacements.append((token.start, token.end, replacement))
                            local_changed += 1
                    except (SnbtError, JsonNormalizationError, TextComponentMigrationError) as exc:
                        diagnostics.append(
                            Diagnostic(
                                Severity.ERROR,
                                "text-component-command-parse-failed",
                                str(exc),
                                path=context.relative(path),
                                line=line_number,
                                compatibility=Compatibility.UNKNOWN,
                                rule_id=self.id,
                            )
                        )
                output.append(parsed.replace_spans(replacements) + suffix)
            if local_changed:
                path.write_text("".join(output), encoding="utf-8")
                changed_files += 1
                changed_nodes += local_changed

        return RuleResult(
            MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes),
            diagnostics,
        )
