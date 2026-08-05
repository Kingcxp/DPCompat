"""Public extension API for declarative and Python migration rules."""

from .declarative import DeclarativeMigrationRule, load_declarative_rule
from .registry import RuleRegistry, create_rule_registry
from .schema import DeclarativeRuleSpec, RuleInfo

__all__ = [
    "DeclarativeMigrationRule",
    "DeclarativeRuleSpec",
    "RuleInfo",
    "RuleRegistry",
    "create_rule_registry",
    "load_declarative_rule",
]
