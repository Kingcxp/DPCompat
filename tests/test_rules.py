"""Validation tests for strict extension-rule schemas and discovery.

The file grows with the rule registry: built-in sources first, then the
declarative schema, then execution, then Python/entry-point discovery.
"""

from __future__ import annotations

import json
from pathlib import Path

from dpcompat.migrations import BUILTIN_RULES
from dpcompat.migrations.base import MigrationContext
from dpcompat.migrations.sources import BUILTIN_RULE_SOURCES
from dpcompat.models import BuildPolicy, PackFormat
from dpcompat.rules import load_declarative_rule
from dpcompat.rules.schema import DeclarativeRuleSpec

import pytest
from pydantic import ValidationError


def _spec() -> dict[str, object]:
    return {
        "schema": 1,
        "id": "example.rename-field@71",
        "description": "Test-only exact JSON key rename",
        "boundary": [71, 0],
        "compatibility": "lossless",
        "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5"],
        "upgrade": [
            {
                "type": "json_rename_key",
                "include": ["data/**/recipe/*.json"],
                "old_key": "legacy",
                "new_key": "modern",
            }
        ],
        "downgrade": [
            {
                "type": "json_rename_key",
                "include": ["data/**/recipe/*.json"],
                "old_key": "modern",
                "new_key": "legacy",
            }
        ],
    }


def test_every_builtin_rule_has_a_primary_source() -> None:
    assert BUILTIN_RULES
    assert set(BUILTIN_RULE_SOURCES) == {rule.id for rule in BUILTIN_RULES}
    for rule_id, sources in BUILTIN_RULE_SOURCES.items():
        assert sources, rule_id
        assert all(source.startswith("https://") for source in sources), rule_id


def test_lossless_declarative_rule_requires_both_directions() -> None:
    raw = _spec()
    raw["downgrade"] = []
    with pytest.raises(ValidationError, match="both directions"):
        DeclarativeRuleSpec.model_validate(raw)


def test_declarative_schema_rejects_unknown_fields_and_unsafe_globs() -> None:
    raw = _spec()
    raw["surprise"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeclarativeRuleSpec.model_validate(raw)

    raw = _spec()
    upgrade = raw["upgrade"]
    assert isinstance(upgrade, list)
    upgrade[0]["include"] = ["../outside/*.json"]
    with pytest.raises(ValidationError, match="stay inside"):
        DeclarativeRuleSpec.model_validate(raw)


def test_declarative_rule_applies_only_to_included_json(tmp_path: Path) -> None:
    rule_file = tmp_path / "rule.json"
    rule_file.write_text(json.dumps(_spec()), encoding="utf-8")
    rule = load_declarative_rule(rule_file)
    recipe = tmp_path / "pack/data/demo/recipe/test.json"
    unrelated = tmp_path / "pack/data/demo/loot_table/test.json"
    recipe.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    recipe.write_text('{"legacy":1}\n', encoding="utf-8")
    unrelated.write_text('{"legacy":1}\n', encoding="utf-8")

    result = rule.apply(MigrationContext(tmp_path / "pack", PackFormat(61), PackFormat(71), BuildPolicy()))

    assert result.record.changed_files == 1
    assert json.loads(recipe.read_text(encoding="utf-8")) == {"modern": 1}
    assert json.loads(unrelated.read_text(encoding="utf-8")) == {"legacy": 1}


def test_declarative_key_conflict_fails_without_overwriting(tmp_path: Path) -> None:
    rule_file = tmp_path / "rule.json"
    rule_file.write_text(json.dumps(_spec()), encoding="utf-8")
    rule = load_declarative_rule(rule_file)
    recipe = tmp_path / "pack/data/demo/recipe/test.json"
    recipe.parent.mkdir(parents=True)
    recipe.write_text('{"legacy":1,"modern":2}\n', encoding="utf-8")

    result = rule.apply(MigrationContext(tmp_path / "pack", PackFormat(61), PackFormat(71), BuildPolicy()))

    assert {item.code for item in result.diagnostics} == {"declarative-key-conflict"}
    assert json.loads(recipe.read_text(encoding="utf-8")) == {"legacy": 1, "modern": 2}
