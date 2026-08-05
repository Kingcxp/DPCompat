"""Validation tests for strict extension-rule schemas and discovery.

The file grows with the rule registry: built-in sources first, then the
declarative schema, then execution, then Python/entry-point discovery.
"""

from __future__ import annotations

from dpcompat.migrations import BUILTIN_RULES
from dpcompat.migrations.sources import BUILTIN_RULE_SOURCES
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
