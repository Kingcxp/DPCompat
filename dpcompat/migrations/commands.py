"""Migrate selected command grammars whose semantics changed by format.

Only command forms with an explicit parser in this module are rewritten.  Unknown layouts,
macros, or extra parameters produce diagnostics rather than a best-effort textual replacement.
"""

from __future__ import annotations

from ..commands import is_zero_rotation, iter_execute_segments, parse_command_line
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic


class SpawnRotationRule:
    """Migrate the yaw/pitch command grammar introduced at format 88."""

    id = "command.spawn-rotation-pitch@88"
    boundary = PackFormat(88)

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
                    if not values:
                        continue
                    command = values[0]
                    if command == "spawnpoint":
                        # old: spawnpoint <targets> <x> <y> <z> <angle>
                        # new: spawnpoint <targets> <x> <y> <z> <yaw> <pitch>
                        if upgrading and len(segment) == 6:
                            token = segment[-1]
                            replacements.append((token.end, token.end, " 0"))
                            local_changed += 1
                        elif not upgrading and len(segment) == 7:
                            pitch = segment[-1]
                            if is_zero_rotation(pitch.value):
                                replacements.append((segment[-2].end, pitch.end, ""))
                                local_changed += 1
                            else:
                                diagnostics.append(
                                    policy_diagnostic(
                                        context,
                                        compatibility=Compatibility.LOSSY,
                                        code="spawnpoint-pitch-cannot-downgrade",
                                        message="The target has no pitch argument; non-zero pitch would be lost",
                                        path=context.relative(path),
                                        line=line_number,
                                        rule_id=self.id,
                                        details={"pitch": pitch.value},
                                    )
                                )
                    elif command == "setworldspawn":
                        # old full form: x y z angle; new full form: x y z yaw pitch
                        if upgrading and len(segment) == 5:
                            token = segment[-1]
                            replacements.append((token.end, token.end, " 0"))
                            local_changed += 1
                        elif not upgrading and len(segment) == 6:
                            pitch = segment[-1]
                            if is_zero_rotation(pitch.value):
                                replacements.append((segment[-2].end, pitch.end, ""))
                                local_changed += 1
                            else:
                                diagnostics.append(
                                    policy_diagnostic(
                                        context,
                                        compatibility=Compatibility.LOSSY,
                                        code="setworldspawn-pitch-cannot-downgrade",
                                        message="The target has no pitch argument; non-zero pitch would be lost",
                                        path=context.relative(path),
                                        line=line_number,
                                        rule_id=self.id,
                                        details={"pitch": pitch.value},
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


class HorseSaddleSlotRule:
    """Rename the horse saddle item slot only inside ``item`` commands.

    The 1.21.5 rename applies to the item slot name used by ``item replace`` and
    ``item modify``.  A complete ``horse.saddle`` token in a ``data`` command is an
    NBT path, which moved to ``equipment.saddle`` instead; rewriting it to
    ``saddle`` would corrupt that command.
    """

    id = "command.slot-horse-saddle-to-saddle@71"
    boundary = PackFormat(71)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        old, new = ("horse.saddle", "saddle") if upgrading else ("saddle", "horse.saddle")
        changed_files = 0
        changed_nodes = 0
        for path in sorted((context.root / "data").rglob("*.mcfunction")):
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            output: list[str] = []
            local = 0
            for line in lines:
                body = line.rstrip("\r\n")
                suffix = line[len(body) :]
                if not body.strip() or body.lstrip().startswith("#"):
                    output.append(line)
                    continue
                parsed = parse_command_line(body)
                replacements: list[tuple[int, int, str]] = []
                for segment in iter_execute_segments(parsed):
                    values = tuple(token.value for token in segment)
                    if not values or values[0] != "item":
                        continue
                    for token in segment:
                        if token.value == old:
                            replacements.append((token.start, token.end, new))
                            local += 1
                output.append(parsed.replace_spans(replacements) + suffix)
            if local:
                path.write_text("".join(output), encoding="utf-8")
                changed_files += 1
                changed_nodes += local
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes))
