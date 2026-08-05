"""Discover, validate, and order built-in and third-party migration rules."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import re
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from ..migrations.base import MigrationRule
from ..models import PackFormat
from .declarative import load_declarative_rule
from .schema import RuleInfo

logger = logging.getLogger(__name__)

_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9._@-]*$")
ENTRY_POINT_GROUP = "dpcompat.rules"


class RuleRegistry:
    """Ordered registry that rejects duplicate or malformed plug-in rules."""

    def __init__(self) -> None:
        self._rules: dict[str, MigrationRule] = {}
        self._info: dict[str, RuleInfo] = {}
        self._sequence = 0

    def register(
        self,
        rule: MigrationRule,
        *,
        origin: str,
        priority: int | None = None,
        official_sources: tuple[str, ...] | None = None,
    ) -> None:
        rule_id = getattr(rule, "id", None)
        if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
            raise ValueError(f"Rule from {origin} has an invalid id: {rule_id!r}")
        if rule_id in self._rules:
            previous = self._info[rule_id].origin
            raise ValueError(f"Duplicate rule id {rule_id!r} from {previous} and {origin}")
        if not callable(getattr(rule, "applies", None)) or not callable(getattr(rule, "apply", None)):
            raise ValueError(f"Rule {rule_id!r} from {origin} does not implement applies/apply")
        boundary_raw = getattr(rule, "boundary", None)
        boundary = PackFormat.parse(boundary_raw) if boundary_raw is not None else None
        resolved_priority = priority if priority is not None else int(getattr(rule, "priority", self._sequence))
        sources = official_sources or tuple(str(item) for item in getattr(rule, "official_sources", ()))
        if not sources:
            raise ValueError(f"Rule {rule_id!r} from {origin} must declare at least one primary source")
        info = RuleInfo.model_validate(
            {
                "id": rule_id,
                "boundary": boundary,
                "origin": origin,
                "priority": resolved_priority,
                "official_sources": sources,
            }
        )
        self._rules[rule_id] = rule
        self._info[rule_id] = info
        self._sequence += 1
        logger.debug("Registered migration rule %s from %s", rule_id, origin)

    def register_many(self, rules: Iterable[MigrationRule], *, origin: str) -> None:
        for rule in rules:
            self.register(rule, origin=origin)

    @staticmethod
    def _rules_from_object(value: Any, *, origin: str) -> tuple[MigrationRule, ...]:
        if callable(value) and not hasattr(value, "apply"):
            value = value()
        if hasattr(value, "apply") and hasattr(value, "applies"):
            # The provider is duck-typed; the hasattr checks above are the runtime
            # protocol test that static checkers cannot narrow through.
            return (cast(MigrationRule, value),)
        if isinstance(value, Iterable) and not isinstance(value, str | bytes | dict):
            rules = tuple(value)
            if all(hasattr(rule, "apply") and hasattr(rule, "applies") for rule in rules):
                return rules
        raise ValueError(f"{origin} must expose a rule, an iterable of rules, or a provider")

    def load_module(self, module_name: str) -> None:
        module = importlib.import_module(module_name)
        value = getattr(module, "dpcompat_rules", None)
        if value is None:
            value = getattr(module, "RULES", None)
        if value is None:
            raise ValueError(f"Rule module {module_name!r} must expose dpcompat_rules() or RULES")
        self.register_many(self._rules_from_object(value, origin=module_name), origin=module_name)

    def load_module_file(
        self,
        path: Path,
        *,
        origin: str | None = None,
        default_sources: tuple[str, ...] = (),
    ) -> None:
        """Load a Python rule module directly from a file path (plugin files).

        ``default_sources`` are attached to rules that do not declare their own
        ``official_sources``, so plugin files can centralize provenance.
        """

        path = path.expanduser().resolve()
        module_name = "dpcompat_plugin_" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load rule module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = getattr(module, "dpcompat_rules", None)
        if value is None:
            value = getattr(module, "RULES", None)
        if value is None:
            raise ValueError(f"Rule module {path} must expose dpcompat_rules() or RULES")
        resolved_origin = origin or f"file:{path}"
        for rule in self._rules_from_object(value, origin=resolved_origin):
            # The provider is duck-typed; inject plugin-level sources when the rule
            # does not declare its own so provenance stays mandatory.
            rule_any = cast(Any, rule)
            if not getattr(rule_any, "official_sources", ()) and default_sources:
                rule_any.official_sources = default_sources
            self.register(rule, origin=resolved_origin)

    def load_entry_points(self) -> None:
        for point in entry_points(group=ENTRY_POINT_GROUP):
            origin = f"entry-point:{point.name}"
            self.register_many(self._rules_from_object(point.load(), origin=origin), origin=origin)

    def load_file(self, path: Path) -> None:
        self.register(load_declarative_rule(path), origin=f"file:{path.resolve()}")

    def rules(self) -> tuple[MigrationRule, ...]:
        ordered = sorted(
            self._rules,
            key=lambda rule_id: (self._info[rule_id].priority, rule_id),
        )
        return tuple(self._rules[rule_id] for rule_id in ordered)

    def info(self) -> tuple[RuleInfo, ...]:
        by_id = {item.id: item for item in self._info.values()}
        return tuple(by_id[rule.id] for rule in self.rules())


def create_rule_registry(
    *,
    modules: Iterable[str] = (),
    files: Iterable[Path] = (),
    load_entry_points: bool = True,
    enabled_rule_ids: frozenset[str] | None = None,
) -> RuleRegistry:
    """Build the effective registry from built-ins and opt-in extensions.

    ``enabled_rule_ids`` restricts built-in rules to the given set; it is how the
    plugin store implements per-plugin enable/disable while preserving order.
    """

    from ..migrations import BUILTIN_RULES
    from ..migrations.sources import BUILTIN_RULE_SOURCES

    registry = RuleRegistry()
    for priority, rule in enumerate(BUILTIN_RULES, start=100):
        if enabled_rule_ids is not None and rule.id not in enabled_rule_ids:
            continue
        try:
            sources = BUILTIN_RULE_SOURCES[rule.id]
        except KeyError as exc:
            raise ValueError(f"Built-in rule {rule.id!r} is missing a primary source") from exc
        registry.register(rule, origin="builtin", priority=priority, official_sources=sources)
    if load_entry_points:
        registry.load_entry_points()
    for module_name in modules:
        registry.load_module(module_name)
    for path in files:
        registry.load_file(path)
    return registry
