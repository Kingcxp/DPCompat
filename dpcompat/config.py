"""Load and validate the optional ``dpcompat.toml`` project configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import BuildPolicy, PackFormat


class RuleSettings(BaseModel):
    """Opt-in extension sources used to add project or installed plug-in rules."""

    model_config = ConfigDict(extra="forbid")

    modules: list[str] = Field(default_factory=list)
    files: list[Path] = Field(default_factory=list)
    load_entry_points: bool = True

    @field_validator("modules")
    @classmethod
    def validate_modules(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("[rules].modules contains duplicates")
        if any(not item.strip() or item != item.strip() for item in value):
            raise ValueError("Rule module names must be non-empty and trimmed")
        return value


class ProjectConfig(BaseModel):
    """Validated build settings loaded from a project TOML file."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    targets: list[str] = Field(default_factory=list)
    universal: bool = True
    output_name: str = Field(default="datapack", min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    source_format: PackFormat | None = None
    policy: BuildPolicy = Field(default_factory=BuildPolicy)
    fallbacks: dict[str, Path] = Field(default_factory=dict)
    clean_output: bool = True
    rules: RuleSettings = Field(default_factory=RuleSettings)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("[build].targets contains duplicates")
        return value

    @model_validator(mode="after")
    def validate_fallback_keys(self) -> Self:
        if any(not key.strip() or key != key.strip() for key in self.fallbacks):
            raise ValueError("Fallback target keys must be non-empty and trimmed")
        return self


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")
    return value


def _reject_unknown(section: str, values: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(values) - allowed
    if unknown:
        rendered = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown [{section}] key(s): {rendered}")


def load_config(path: Path) -> ProjectConfig:
    """Load TOML and resolve fallback/rule paths relative to the configuration file."""

    path = path.expanduser().resolve()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    unknown_sections = set(raw) - {"build", "policy", "fallbacks", "rules"}
    if unknown_sections:
        raise ValueError(f"Unknown configuration section(s): {', '.join(sorted(unknown_sections))}")

    build = _section(raw, "build")
    policy = _section(raw, "policy")
    fallbacks_raw = _section(raw, "fallbacks")
    rules_raw = _section(raw, "rules")

    _reject_unknown(
        "build",
        build,
        {"targets", "universal", "output_name", "source_format", "clean_output"},
    )
    _reject_unknown(
        "policy",
        policy,
        {"allow_emulated", "allow_lossy", "allow_unknown", "fail_on_warnings"},
    )
    _reject_unknown("rules", rules_raw, {"modules", "files", "load_entry_points"})

    if not all(isinstance(key, str) and isinstance(value, str) for key, value in fallbacks_raw.items()):
        raise ValueError("[fallbacks] keys and values must be strings")

    source_raw = build.get("source_format")
    source_format = PackFormat.parse(source_raw) if source_raw is not None else None
    fallbacks = {str(key): (path.parent / str(value)).resolve() for key, value in fallbacks_raw.items()}
    rule_files_raw = rules_raw.get("files", [])
    if not isinstance(rule_files_raw, list) or not all(isinstance(value, str) for value in rule_files_raw):
        raise ValueError("[rules].files must be an array of strings")
    rule_files = [(path.parent / str(value)).resolve() for value in rule_files_raw]

    payload = {
        "targets": build.get("targets", []),
        "universal": build.get("universal", True),
        "output_name": build.get("output_name", "datapack"),
        "source_format": source_format,
        "clean_output": build.get("clean_output", True),
        "policy": policy,
        "fallbacks": fallbacks,
        "rules": {**rules_raw, "files": rule_files},
    }
    return ProjectConfig.model_validate(payload)
