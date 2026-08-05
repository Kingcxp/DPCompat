"""Read source-attributed feature minimums from bundled JSON manifests."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from .models import Compatibility, FrozenModel, PackFormat


class FeatureSpec(FrozenModel):
    """Minimum-format fact and its downgrade classification."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    min_format: PackFormat
    resource_types: frozenset[str] = frozenset()
    commands: tuple[str, ...] = ()
    identifiers: frozenset[str] = frozenset()
    downgrade: Compatibility
    source: HttpUrl

    @field_validator("min_format", mode="before")
    @classmethod
    def parse_min_format(cls, value: object) -> PackFormat:
        return PackFormat.parse(value)

    @field_validator("downgrade", mode="before")
    @classmethod
    def map_conditional(cls, value: object) -> object:
        return Compatibility.UNKNOWN if value == "conditional" else value

    @model_validator(mode="after")
    def require_matcher(self) -> Self:
        if not (self.resource_types or self.commands or self.identifiers):
            raise ValueError("A feature needs at least one resource, command, or identifier matcher")
        return self


class FeatureManifest(BaseModel):
    """Strict on-disk schema for ``data/features.json``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(alias="schema", ge=1)
    features: tuple[FeatureSpec, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [feature.id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("Feature manifest contains duplicate ids")
        return self


@lru_cache(maxsize=1)
def feature_manifest() -> FeatureManifest:
    """Return the validated, cached feature manifest."""

    raw = json.loads(files("dpcompat.data").joinpath("features.json").read_text(encoding="utf-8"))
    return FeatureManifest.model_validate(raw)


def feature_specs() -> tuple[FeatureSpec, ...]:
    """Return all registered feature facts."""

    return feature_manifest().features


@lru_cache(maxsize=1)
def resource_minimums() -> dict[str, tuple[PackFormat, FeatureSpec]]:
    """Index resource directory names by minimum supported format."""

    return {resource_type: (spec.min_format, spec) for spec in feature_specs() for resource_type in spec.resource_types}


@lru_cache(maxsize=1)
def identifier_minimums() -> dict[str, tuple[PackFormat, FeatureSpec]]:
    """Index exact registry identifiers by minimum supported format."""

    return {identifier: (spec.min_format, spec) for spec in feature_specs() for identifier in spec.identifiers}
