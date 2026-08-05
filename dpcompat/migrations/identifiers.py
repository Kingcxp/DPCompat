"""Apply reviewed registry identifier renames across text and JSON resources.

Identifier rules are context-bounded and reversible only where the old and new identifiers are
known aliases.  They are not a general search-and-replace facility for namespace strings.
"""

from __future__ import annotations

from typing import Any

from ..commands import parse_command_line
from ..jsonutil import JsonNormalizationError, dump_path, load_path
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from .base import MigrationContext, RuleResult, crosses


def _replace_exact_json(value: Any, old: str, new: str) -> tuple[Any, int]:
    if isinstance(value, str):
        return (new, 1) if value == old else (value, 0)
    if isinstance(value, list):
        total = 0
        list_output: list[Any] = []
        for item in value:
            migrated, changed = _replace_exact_json(item, old, new)
            list_output.append(migrated)
            total += changed
        return list_output, total
    if isinstance(value, dict):
        total = 0
        dict_output: dict[str, Any] = {}
        for key, item in value.items():
            migrated, changed = _replace_exact_json(item, old, new)
            # Object keys can be user-defined maps. Only exact scalar values are treated as
            # resource-location candidates; renaming a key would require its owning schema.
            dict_output[key] = migrated
            total += changed
        return dict_output, total
    return value, 0


def _replace_command_token(token: str, old: str, new: str) -> tuple[str, int]:
    """Replace complete resource-location atoms without touching arbitrary substrings."""
    delimiters = set("[]{}(),:=!| \\t\\r\\n'\"")
    output: list[str] = []
    index = 0
    changed = 0
    while index < len(token):
        if token.startswith(old, index):
            left_ok = index == 0 or token[index - 1] in delimiters
            end = index + len(old)
            right_ok = end == len(token) or token[end] in delimiters
            if left_ok and right_ok:
                output.append(new)
                index = end
                changed += 1
                continue
        output.append(token[index])
        index += 1
    return "".join(output), changed


class ChainRenameRule:
    """Rewrite the reviewed chain/iron_chain identifier boundary at format 88."""

    id = "identifier.chain-to-iron-chain@88"
    boundary = PackFormat(88)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        old = "minecraft:chain" if upgrading else "minecraft:iron_chain"
        new = "minecraft:iron_chain" if upgrading else "minecraft:chain"
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []

        for path in sorted((context.root / "data").rglob("*")):
            if not path.is_file():
                continue
            if path.suffix == ".json":
                try:
                    value = load_path(path)
                    migrated, changed = _replace_exact_json(value, old, new)
                    if changed:
                        dump_path(path, migrated)
                        changed_files += 1
                        changed_nodes += changed
                except (OSError, JsonNormalizationError) as exc:
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "identifier-migration-failed",
                            str(exc),
                            path=context.relative(path),
                            compatibility=Compatibility.UNSUPPORTED,
                            rule_id=self.id,
                        )
                    )
            elif path.suffix == ".mcfunction":
                try:
                    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                except UnicodeDecodeError:
                    continue
                out: list[str] = []
                local_changed = 0
                for line in lines:
                    stripped = line.lstrip()
                    if not stripped or stripped.startswith("#"):
                        out.append(line)
                        continue
                    parsed = parse_command_line(line.rstrip("\r\n"))
                    replacements: list[tuple[int, int, str]] = []
                    for token in parsed.tokens:
                        replacement, count = _replace_command_token(token.value, old, new)
                        if count:
                            replacements.append((token.start, token.end, replacement))
                            local_changed += count
                    suffix = line[len(line.rstrip("\r\n")) :]
                    out.append(parsed.replace_spans(replacements) + suffix)
                if local_changed:
                    path.write_text("".join(out), encoding="utf-8")
                    changed_files += 1
                    changed_nodes += local_changed

        return RuleResult(
            MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes),
            diagnostics,
        )
