"""Validation tests for strict extension-rule schemas and discovery.

The file grows with the rule registry: built-in sources first, then the
declarative schema, then execution, then Python/entry-point discovery.
"""

from __future__ import annotations

from dpcompat.migrations import BUILTIN_RULES
from dpcompat.migrations.sources import BUILTIN_RULE_SOURCES


def test_every_builtin_rule_has_a_primary_source() -> None:
    assert BUILTIN_RULES
    assert set(BUILTIN_RULE_SOURCES) == {rule.id for rule in BUILTIN_RULES}
    for rule_id, sources in BUILTIN_RULE_SOURCES.items():
        assert sources, rule_id
        assert all(source.startswith("https://") for source in sources), rule_id
