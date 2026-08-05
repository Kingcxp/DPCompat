"""Normalize all JSON resources when a target crosses the strict-JSON boundary.

The rule preserves data while removing accepted legacy syntax.  Duplicate keys remain hard
errors because their intended value cannot be inferred safely.
"""

from __future__ import annotations

from ..jsonutil import JsonNormalizationError, dump_path, load_path
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat, Severity
from .base import MigrationContext, RuleResult


class StrictJsonRule:
    """Normalize JSON when a conversion crosses the format-80 strict boundary."""

    id = "json.strict-normalization@80"

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return target >= PackFormat(80)

    def apply(self, context: MigrationContext) -> RuleResult:
        changed_files = 0
        diagnostics: list[Diagnostic] = []
        paths = [context.root / "pack.mcmeta", *sorted((context.root / "data").rglob("*.json"))]
        for path in paths:
            if not path.is_file():
                continue
            try:
                before = path.read_text(encoding="utf-8")
                value = load_path(path)
                dump_path(path, value)
                if path.read_text(encoding="utf-8") != before:
                    changed_files += 1
            except (OSError, JsonNormalizationError) as exc:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "json-normalization-failed",
                        str(exc),
                        path=context.relative(path),
                        compatibility=Compatibility.UNSUPPORTED,
                        rule_id=self.id,
                    )
                )
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files), diagnostics)
