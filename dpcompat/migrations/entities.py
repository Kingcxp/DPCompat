"""Migrate entity NBT embedded in supported command contexts.

The rule recognizes summon and selected data-merge commands, parses only the entity payload,
and delegates field semantics to :mod:`dpcompat.entity_data`.  It deliberately avoids arbitrary
``data storage`` compounds where identical key names may be user-defined.
"""

from __future__ import annotations

from ..commands import CommandToken, iter_execute_segments, macro_placeholders_are_quoted, parse_command_line
from ..entity_data import downgrade_entity_nbt, upgrade_entity_nbt
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from ..snbt import SnbtError
from ..snbt import dumps as dumps_snbt
from ..snbt import loads as loads_snbt
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic


class EntitySnbtRule:
    """Migrate entity NBT only in commands whose argument context is unambiguous."""

    id = "entity-nbt-equipment-and-fields@71"
    boundary = PackFormat(71)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def _locate(self, segment: tuple[CommandToken, ...]) -> tuple[str, int] | None:
        values = tuple(token.value for token in segment)
        if not values:
            return None
        if values[0] == "summon" and len(values) >= 3:
            # summon <entity> [pos] [nbt]; an NBT argument always starts with a compound.
            # A macro placeholder is also a candidate: it can generate the compound at
            # runtime, so the caller must fail closed instead of silently skipping it.
            for index in range(2, len(values)):
                if values[index].startswith("{") or "$(" in values[index]:
                    return values[1], index
        if len(values) >= 5 and values[:3] == ("data", "merge", "entity"):
            return "", 4
        return None

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        transform = upgrade_entity_nbt if upgrading else downgrade_entity_nbt
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
                    located = self._locate(segment)
                    if located is None:
                        continue
                    entity_id, index = located
                    token = segment[index]
                    if "$(" in token.value and not macro_placeholders_are_quoted(token.value):
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="macro-entity-nbt-needs-runtime-parse",
                                message=(
                                    "A macro controls entity-NBT structure; only placeholders contained "
                                    "inside quoted scalar values can be statically migrated"
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
                                "entity-snbt-parse-failed",
                                str(exc),
                                path=context.relative(path),
                                line=line_number,
                                compatibility=Compatibility.UNKNOWN,
                                rule_id=self.id,
                            )
                        )
                        continue
                    if not isinstance(value, dict):
                        diagnostics.append(
                            Diagnostic(
                                Severity.ERROR,
                                "entity-snbt-not-compound",
                                "Entity NBT must be an SNBT compound",
                                path=context.relative(path),
                                line=line_number,
                                compatibility=Compatibility.UNKNOWN,
                                rule_id=self.id,
                            )
                        )
                        continue
                    result = transform(entity_id, value)
                    if result.changed:
                        replacements.append((token.start, token.end, dumps_snbt(result.value)))
                        local_changed += result.changed
                    for warning in result.warnings:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.LOSSY,
                                code="entity-nbt-lossy-conversion",
                                message=warning,
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                            )
                        )
                    for unknown in result.unknowns:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="entity-text-component-unknown",
                                message=unknown,
                                path=context.relative(path),
                                line=line_number,
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
