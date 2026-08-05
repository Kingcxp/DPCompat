"""Pydantic schemas for reviewable declarative migration rules."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, JsonValue, field_validator, model_validator

from ..models import Compatibility, FrozenModel, PackFormat


class JsonOperationBase(FrozenModel):
    """Common filesystem scope for a JSON operation."""

    include: tuple[str, ...] = ("data/**/*.json",)
    within_keys: frozenset[str] = frozenset()

    @field_validator("include")
    @classmethod
    def require_safe_globs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("include needs at least one project-relative glob")
        if any(pattern.startswith(("/", "\\")) or ".." in pattern.split("/") for pattern in value):
            raise ValueError("include patterns must stay inside the data-pack root")
        return value


class JsonExactValueOperation(JsonOperationBase):
    """Replace an exactly equal JSON value in an explicitly scoped context."""

    type: Literal["json_exact_value"]
    old: JsonValue
    new: JsonValue

    @model_validator(mode="after")
    def values_must_differ(self) -> Self:
        if self.old == self.new:
            raise ValueError("json_exact_value old and new values must differ")
        return self


class JsonRenameKeyOperation(JsonOperationBase):
    """Rename an exact JSON object key, refusing destination conflicts."""

    type: Literal["json_rename_key"]
    old_key: str = Field(min_length=1)
    new_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def keys_must_differ(self) -> Self:
        if self.old_key == self.new_key:
            raise ValueError("json_rename_key old_key and new_key must differ")
        return self


DeclarativeOperation = Annotated[
    JsonExactValueOperation | JsonRenameKeyOperation,
    Field(discriminator="type"),
]


class DeclarativeRuleSpec(FrozenModel):
    """One boundary rule loaded from a contributor-authored JSON file."""

    schema_version: Literal[1] = Field(alias="schema")
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._@-]*$")
    description: str = Field(min_length=1)
    boundary: PackFormat
    compatibility: Compatibility = Compatibility.LOSSLESS
    priority: int = Field(default=500, ge=0, le=10_000)
    official_sources: tuple[HttpUrl, ...] = Field(min_length=1)
    upgrade: tuple[DeclarativeOperation, ...] = ()
    downgrade: tuple[DeclarativeOperation, ...] = ()

    @field_validator("boundary", mode="before")
    @classmethod
    def parse_boundary(cls, value: object) -> PackFormat:
        return PackFormat.parse(value)

    @model_validator(mode="after")
    def require_directional_operations(self) -> Self:
        if not (self.upgrade or self.downgrade):
            raise ValueError("A declarative rule needs upgrade or downgrade operations")
        if self.compatibility == Compatibility.LOSSLESS and not (self.upgrade and self.downgrade):
            raise ValueError("A lossless declarative rule must define both directions")
        return self


class RuleInfo(FrozenModel):
    """Validated metadata shown by ``dpcompat rules`` and reports."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._@-]*$")
    boundary: PackFormat | None = None
    origin: str = Field(min_length=1)
    priority: int = Field(ge=0, le=10_000)
    official_sources: tuple[HttpUrl, ...] = Field(min_length=1)
