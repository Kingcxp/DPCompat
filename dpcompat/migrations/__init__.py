"""Register the ordered default migration rule set.

Ordering is part of the compiler contract: structural parsing and schema migrations must run
before broad identifier rewrites when one transformation changes the context required by the
next.  New rules should document and test any ordering dependency.
"""

from __future__ import annotations

from .base import MigrationContext, MigrationRule, RuleResult
from .commands import HorseSaddleSlotRule, SpawnRotationRule
from .entities import EntitySnbtRule
from .gamerules import GameRuleRegistryRule
from .identifiers import ChainRenameRule
from .items import ItemTooltipComponentsRule
from .recipes import Recipe26Rule, TimeCheckClockRule
from .resources import FilteredLootRule, TestEnvironmentClockRule, TimelineClockRule
from .strict_json import StrictJsonRule
from .structures import StructureEntityNbtRule
from .text import TextComponentRule
from .worldborder import WorldBorderTimeRule

BUILTIN_RULES: tuple[MigrationRule, ...] = (
    TextComponentRule(),
    ItemTooltipComponentsRule(),
    EntitySnbtRule(),
    StructureEntityNbtRule(),
    HorseSaddleSlotRule(),
    ChainRenameRule(),
    SpawnRotationRule(),
    GameRuleRegistryRule(),
    WorldBorderTimeRule(),
    FilteredLootRule(),
    TimelineClockRule(),
    TimeCheckClockRule(),
    Recipe26Rule(),
    TestEnvironmentClockRule(),
    StrictJsonRule(),
)

# Kept as a compatibility alias for callers written against DPCompat 0.2.x. New code should
# construct a RuleRegistry so project files and installed extensions participate.
DEFAULT_RULES = BUILTIN_RULES

__all__ = [
    "BUILTIN_RULES",
    "DEFAULT_RULES",
    "MigrationContext",
    "MigrationRule",
    "RuleResult",
]
