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
from dpcompat.rules import RuleRegistry, create_rule_registry, load_declarative_rule
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


def test_project_module_rules_are_discovered_and_duplicate_ids_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "example_rules.py"
    module.write_text(
        "from dpcompat.migrations.strict_json import StrictJsonRule\n"
        "rule = StrictJsonRule()\n"
        "rule.official_sources = "
        "('https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-6',)\n"
        "RULES = (rule,)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = RuleRegistry()
    registry.load_module("example_rules")
    assert registry.info()[0].id == "json.strict-normalization@80"
    with pytest.raises(ValueError, match="Duplicate rule id"):
        registry.load_module("example_rules")


def test_registry_rejects_rules_without_primary_sources() -> None:
    from dpcompat.migrations.strict_json import StrictJsonRule

    registry = RuleRegistry()
    with pytest.raises(ValueError, match="at least one primary source"):
        registry.register(StrictJsonRule(), origin="unsourced")


def test_builtin_registry_orders_rules_and_is_duplicate_free() -> None:
    registry = create_rule_registry(load_entry_points=False)
    info = registry.info()
    assert info
    priorities = [item.priority for item in info]
    assert priorities == sorted(priorities)
    assert len({item.id for item in info}) == len(info)
    assert all(item.official_sources for item in info)
