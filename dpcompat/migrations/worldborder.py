"""Preserve world-border time units while exposing the format-94.1 semantic break."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..commands import iter_execute_segments, parse_command_line
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic

_DURATION = re.compile(r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+))(?P<unit>[sd]?)$")


def _plain_decimal(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


class WorldBorderTimeRule:
    """Convert explicit units and flag the real-time to game-tick behavior change."""

    id = "command.worldborder-tick-time@94.1"
    boundary = PackFormat(94, 1)

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
                parsed = parse_command_line(body)
                replacements: list[tuple[int, int, str]] = []
                for segment in iter_execute_segments(parsed):
                    values = tuple(token.value for token in segment)
                    if len(values) != 4 or values[:2] not in {("worldborder", "add"), ("worldborder", "set")}:
                        continue
                    token = segment[3]
                    if parsed.macro or "$(" in token.value:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="macro-worldborder-time-cannot-migrate",
                                message="A macro-generated worldborder duration cannot be statically migrated",
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                            )
                        )
                        continue
                    match = _DURATION.fullmatch(token.value)
                    if match is None:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="worldborder-time-unrecognized",
                                message=f"Unrecognized worldborder duration {token.value!r}",
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                            )
                        )
                        continue
                    number = match.group("number")
                    unit = match.group("unit")
                    # Old releases never had unit suffixes, so a suffixed duration is
                    # already written in the target syntax: skip it and keep the rule
                    # idempotent.  A bare number on downgrade is NOT a no-op, because
                    # the new syntax interprets bare numbers as ticks.
                    if upgrading and unit:
                        continue
                    replacement: str | None = None
                    if upgrading:
                        replacement = number + "s"
                    elif unit == "s":
                        replacement = number
                    elif unit == "d":
                        try:
                            replacement = _plain_decimal(Decimal(number) * Decimal(1200))
                        except InvalidOperation:
                            replacement = None
                    elif not unit:
                        try:
                            seconds = Decimal(number) / Decimal(20)
                            if seconds == seconds.to_integral_value():
                                replacement = _plain_decimal(seconds)
                        except InvalidOperation:
                            replacement = None
                    if replacement is not None and replacement != token.value:
                        replacements.append((token.start, token.end, replacement))
                        local_changed += 1
                    if replacement is None:
                        compatibility = Compatibility.LOSSY
                        message = "This tick duration cannot be expressed as whole legacy seconds"
                    else:
                        compatibility = Compatibility.UNKNOWN
                        message = (
                            "World-border interpolation changed from real time to game ticks; "
                            "the syntax is migrated, but pause and /tick behavior cannot be preserved"
                        )
                    diagnostics.append(
                        policy_diagnostic(
                            context,
                            compatibility=compatibility,
                            code="worldborder-time-semantics-changed",
                            message=message,
                            path=context.relative(path),
                            line=line_number,
                            rule_id=self.id,
                            details={"duration": token.value, "replacement": replacement},
                        )
                    )
                output.append(parsed.replace_spans(replacements) + suffix)
            if local_changed:
                path.write_text("".join(output), encoding="utf-8")
                changed_files += 1
                changed_nodes += local_changed
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes), diagnostics)
