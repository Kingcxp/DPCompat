"""Execute the intentionally small, context-scoped declarative rule language."""

from __future__ import annotations

import fnmatch
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..jsonutil import JsonNormalizationError, dump_path, load_path
from ..migrations.base import MigrationContext, RuleResult, crosses
from ..models import Compatibility, Diagnostic, MigrationRecord, Severity
from .schema import (
    DeclarativeOperation,
    DeclarativeRuleSpec,
    JsonExactValueOperation,
    JsonRenameKeyOperation,
)


def load_declarative_rule(path: Path) -> DeclarativeMigrationRule:
    """Load one strict JSON rule file and retain its source path for auditing."""

    path = path.expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        spec = DeclarativeRuleSpec.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid declarative rule {path}: {exc}") from exc
    return DeclarativeMigrationRule(spec, source_path=path)


class DeclarativeMigrationRule:
    """Migration-rule adapter around a validated :class:`DeclarativeRuleSpec`."""

    def __init__(self, spec: DeclarativeRuleSpec, *, source_path: Path) -> None:
        self.spec = spec
        self.source_path = source_path
        self.id = spec.id
        self.boundary = spec.boundary
        self.priority = spec.priority
        self.official_sources = spec.official_sources

    def applies(self, source: Any, target: Any) -> bool:
        return crosses(source, target, self.boundary)

    @staticmethod
    def _matches(relative: str, operation: DeclarativeOperation) -> bool:
        return any(fnmatch.fnmatchcase(relative, pattern) for pattern in operation.include)

    @staticmethod
    def _apply_operation(
        node: Any,
        operation: DeclarativeOperation,
        *,
        parent_key: str | None,
        conflicts: list[str],
    ) -> tuple[Any, int]:
        changed = 0
        in_scope = not operation.within_keys or parent_key in operation.within_keys
        if isinstance(operation, JsonExactValueOperation) and in_scope and node == operation.old:
            return deepcopy(operation.new), 1
        if isinstance(node, list):
            list_output: list[Any] = []
            for item in node:
                migrated, count = DeclarativeMigrationRule._apply_operation(
                    item,
                    operation,
                    parent_key=parent_key,
                    conflicts=conflicts,
                )
                list_output.append(migrated)
                changed += count
            return list_output, changed
        if not isinstance(node, dict):
            return node, 0

        dict_output: dict[str, Any] = {}
        for key, value in node.items():
            migrated, count = DeclarativeMigrationRule._apply_operation(
                value,
                operation,
                parent_key=key,
                conflicts=conflicts,
            )
            dict_output[key] = migrated
            changed += count

        if isinstance(operation, JsonRenameKeyOperation) and in_scope and operation.old_key in dict_output:
            if operation.new_key in dict_output:
                conflicts.append(f"cannot rename {operation.old_key!r}: {operation.new_key!r} already exists")
            else:
                dict_output[operation.new_key] = dict_output.pop(operation.old_key)
                changed += 1
        return dict_output, changed

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        operations = self.spec.upgrade if upgrading else self.spec.downgrade
        if not operations:
            diagnostic = Diagnostic(
                Severity.ERROR,
                "declarative-direction-missing",
                f"Rule {self.id} has no {'upgrade' if upgrading else 'downgrade'} implementation",
                compatibility=Compatibility.UNSUPPORTED,
                rule_id=self.id,
                details={"rule_file": str(self.source_path)},
            )
            return RuleResult(MigrationRecord(self.id, Compatibility.UNSUPPORTED, 0), [diagnostic])

        changed_files: set[Path] = set()
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []
        candidates = sorted((context.root / "data").rglob("*.json"))
        for path in candidates:
            relative = context.relative(path)
            matching = [operation for operation in operations if self._matches(relative, operation)]
            if not matching:
                continue
            try:
                value = load_path(path)
                migrated = value
                local_changes = 0
                conflicts: list[str] = []
                for operation in matching:
                    migrated, count = self._apply_operation(
                        migrated,
                        operation,
                        parent_key=None,
                        conflicts=conflicts,
                    )
                    local_changes += count
                if conflicts:
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "declarative-key-conflict",
                            "; ".join(conflicts),
                            path=relative,
                            compatibility=Compatibility.UNSUPPORTED,
                            rule_id=self.id,
                        )
                    )
                    continue
                if local_changes:
                    dump_path(path, migrated)
                    changed_files.add(path)
                    changed_nodes += local_changes
            except (OSError, JsonNormalizationError) as exc:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "declarative-json-failed",
                        str(exc),
                        path=relative,
                        compatibility=Compatibility.UNKNOWN,
                        rule_id=self.id,
                    )
                )

        return RuleResult(
            MigrationRecord(self.id, self.spec.compatibility, len(changed_files), changed_nodes),
            diagnostics,
        )
