"""Rule-level regression tests for the researched 1.21.11 command boundaries.

The rules are exercised through :class:`MigrationContext` directly so each test
points at one rule; end-to-end build behavior is covered by ``test_build.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from typing import Any

from dpcompat.migrations.base import MigrationContext
from dpcompat.migrations.gamerules import GameRuleRegistryRule
from dpcompat.models import BuildPolicy, PackFormat, Severity

from helpers import make_pack, write


def _run_gamerules(root: Path, source: Any, target: Any, *, policy: BuildPolicy | None = None) -> list:
    rule = GameRuleRegistryRule()
    result = rule.apply(
        MigrationContext(root, PackFormat.parse(source), PackFormat.parse(target), policy or BuildPolicy())
    )
    return result.diagnostics


def test_gamerule_names_and_inverted_assignments_are_migrated() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = make_pack(Path(temp_dir) / "pack", [88, 0])
        write(
            root,
            "data/demo/function/test.mcfunction",
            "gamerule doDaylightCycle false\ngamerule disableRaids true\n",
        )
        diagnostics = _run_gamerules(root, 88, 94.1)
        assert [item.severity for item in diagnostics] == []
        text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
        assert "gamerule minecraft:advance_time false" in text
        assert "gamerule minecraft:raids false" in text  # inverted assignment


def test_replaced_fire_gamerules_fail_closed_in_both_directions() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = make_pack(Path(temp_dir) / "pack", [88, 0])
        write(root, "data/demo/function/test.mcfunction", "gamerule doFireTick false\n")
        diagnostics = _run_gamerules(root, 88, 94.1)
        assert {item.code for item in diagnostics} == {"fire-gamerules-replaced"}
        assert all(item.severity == Severity.ERROR for item in diagnostics)

        modern = make_pack(Path(temp_dir) / "modern", [94, 1])
        write(modern, "data/demo/function/test.mcfunction", "gamerule minecraft:fire_spread_radius_around_player 3\n")
        diagnostics = _run_gamerules(modern, 94.1, 88)
        assert {item.code for item in diagnostics} == {"fire-gamerules-replaced"}
        text = (modern / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
        assert "fire_spread_radius_around_player" in text  # never rewritten to a fake legacy name


def test_gamerule_queries_of_inverted_rules_are_unsupported() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = make_pack(Path(temp_dir) / "pack", [88, 0])
        write(root, "data/demo/function/test.mcfunction", "gamerule disableRaids\n")
        diagnostics = _run_gamerules(root, 88, 94.1)
        assert {item.code for item in diagnostics} == {"inverted-gamerule-query-cannot-migrate"}


def test_macro_gamerules_cannot_be_statically_migrated() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = make_pack(Path(temp_dir) / "pack", [88, 0])
        write(root, "data/demo/function/test.mcfunction", "$gamerule $(rule) true\n")
        diagnostics = _run_gamerules(root, 88, 94.1)
        assert {item.code for item in diagnostics} == {"macro-gamerule-cannot-migrate"}


def test_gamerule_downgrade_restores_camel_case_names() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = make_pack(Path(temp_dir) / "pack", [94, 1])
        write(root, "data/demo/function/test.mcfunction", "gamerule minecraft:advance_time true\n")
        _run_gamerules(root, 94.1, 88)
        text = (root / "data/demo/function/test.mcfunction").read_text(encoding="utf-8")
        assert "gamerule doDaylightCycle true" in text
