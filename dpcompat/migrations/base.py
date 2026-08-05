"""Shared protocol and context objects for direction-aware migrations.

A rule is selected by crossing a formal pack-format boundary, not by comparing game-version
strings.  Each application records its compatibility class and changed-node counts so policy
and reports can distinguish safe rewrites from emulation or loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..models import BuildPolicy, Diagnostic, MigrationRecord, PackFormat


@dataclass(slots=True)
class MigrationContext:
    """Immutable inputs exposed to one rule application."""

    root: Path
    source: PackFormat
    target: PackFormat
    policy: BuildPolicy

    @property
    def upgrading(self) -> bool:
        return self.source < self.target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


@dataclass(slots=True)
class RuleResult:
    """One rule's audit record and any diagnostics it produced."""

    record: MigrationRecord
    diagnostics: list[Diagnostic] = field(default_factory=list)


class MigrationRule(Protocol):
    """Structural protocol implemented by every direction-aware rule."""

    id: str

    def applies(self, source: PackFormat, target: PackFormat) -> bool: ...

    def apply(self, context: MigrationContext) -> RuleResult: ...


def crosses(source: PackFormat, target: PackFormat, boundary: PackFormat) -> bool:
    """Return whether conversion moves from one side of ``boundary`` to the other."""

    return (source < boundary <= target) or (target < boundary <= source)
