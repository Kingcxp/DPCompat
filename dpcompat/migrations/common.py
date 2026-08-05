"""Utility functions shared by JSON-oriented migration rules.

These helpers centralize traversal, writeback, and policy diagnostics so individual rules can
focus on one Mojang schema boundary and use consistent error reporting.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..jsonutil import JsonNormalizationError, dump_path, load_path
from ..models import Compatibility, Diagnostic, MigrationRecord, Severity
from .base import MigrationContext, RuleResult

JsonTransformer = Callable[[Any, Path], tuple[Any, int, list[Diagnostic]]]


def transform_json_files(
    context: MigrationContext,
    rule_id: str,
    transformer: JsonTransformer,
    *,
    compatibility: Compatibility = Compatibility.LOSSLESS,
    include_mcmeta: bool = False,
) -> RuleResult:
    """Apply a typed JSON transform to matching resources and write changed files."""

    paths = sorted((context.root / "data").rglob("*.json"))
    if include_mcmeta:
        paths.insert(0, context.root / "pack.mcmeta")
    changed_files = 0
    changed_nodes = 0
    diagnostics: list[Diagnostic] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            value = load_path(path)
            migrated, changed, local = transformer(value, path)
            diagnostics.extend(local)
            if changed:
                dump_path(path, migrated)
                changed_files += 1
                changed_nodes += changed
        except (OSError, JsonNormalizationError) as exc:
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "json-migration-failed",
                    str(exc),
                    path=context.relative(path),
                    compatibility=Compatibility.UNSUPPORTED,
                    rule_id=rule_id,
                )
            )
    return RuleResult(MigrationRecord(rule_id, compatibility, changed_files, changed_nodes), diagnostics)


def policy_diagnostic(
    context: MigrationContext,
    *,
    compatibility: Compatibility,
    code: str,
    message: str,
    path: str | None,
    line: int | None,
    rule_id: str,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    """Create a diagnostic whose severity reflects the active build policy."""

    permitted = context.policy.permits(compatibility)
    severity = Severity.WARNING if permitted else Severity.ERROR
    return Diagnostic(
        severity,
        code,
        message,
        path=path,
        line=line,
        compatibility=compatibility,
        rule_id=rule_id,
        details=details or {},
    )
