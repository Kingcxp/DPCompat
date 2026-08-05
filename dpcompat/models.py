"""Strict domain models shared by detection, migration, policy, and reporting.

Objects that cross a module boundary use Pydantic so malformed manifests, plug-ins, and
configuration fail at the edge of the application. Parser-only syntax nodes remain small
dataclasses in their own modules because they are internal implementation details.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from functools import total_ordering
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class FrozenModel(BaseModel):
    """Base class for immutable, hashable value objects."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


@total_ordering
class PackFormat(FrozenModel):
    """A Minecraft pack format, including the minor component introduced in 1.21.9."""

    major: int = Field(ge=0)
    minor: int = Field(default=0, ge=0)

    def __init__(self, major: int | None = None, minor: int = 0, **data: Any) -> None:
        """Allow the compact ``PackFormat(71)`` spelling used by migration rules."""

        if major is not None:
            if "major" in data:
                raise TypeError("major was provided twice")
            data["major"] = major
            data.setdefault("minor", minor)
        super().__init__(**data)

    @classmethod
    def parse(cls, value: Any) -> Self:
        """Parse legacy integer, decimal string, or ``[major, minor]`` metadata values."""

        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise ValueError(f"Invalid pack format value: {value!r}")
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, float):
            return cls.parse(format(value, "g"))
        if isinstance(value, str):
            parts = value.strip().split(".", maxsplit=1)
            if not parts[0].isdigit() or (len(parts) == 2 and not parts[1].isdigit()):
                raise ValueError(f"Invalid pack format: {value!r}")
            return cls(int(parts[0]), int(parts[1]) if len(parts) == 2 else 0)
        if (
            isinstance(value, list | tuple)
            and 1 <= len(value) <= 2
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            return cls(value[0], value[1] if len(value) == 2 else 0)
        raise ValueError(f"Invalid pack format value: {value!r}")

    def _key(self) -> tuple[int, int]:
        return self.major, self.minor

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PackFormat):
            return NotImplemented
        return self._key() < other._key()

    def exact_metadata_value(self) -> list[int]:
        """Return the unambiguous post-1.21.9 metadata representation."""

        return [self.major, self.minor]

    def compact_metadata_value(self) -> int | list[int]:
        """Return an integer when the minor component is zero."""

        return self.major if self.minor == 0 else [self.major, self.minor]

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}" if self.minor else str(self.major)


class PackFormatRange(FrozenModel):
    """Inclusive range of pack formats used by metadata and overlays."""

    minimum: PackFormat
    maximum: PackFormat

    @field_validator("minimum", "maximum", mode="before")
    @classmethod
    def parse_formats(cls, value: Any) -> PackFormat:
        return PackFormat.parse(value)

    def __init__(
        self,
        minimum: PackFormat | None = None,
        maximum: PackFormat | None = None,
        **data: Any,
    ) -> None:
        if minimum is not None:
            data.setdefault("minimum", minimum)
        if maximum is not None:
            data.setdefault("maximum", maximum)
        super().__init__(**data)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Pack format range minimum must not exceed maximum")
        return self

    def contains(self, value: PackFormat) -> bool:
        return self.minimum <= value <= self.maximum


class VersionProfile(FrozenModel):
    """One supported stable release and the runtime facts needed to build it."""

    game_version: str = Field(min_length=1, pattern=r"^[0-9]+(?:\.[0-9]+){1,2}$")
    pack_format: PackFormat
    release_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    java_major: int = Field(ge=21)
    note: str = ""
    capabilities: frozenset[str] = frozenset()
    official_url: HttpUrl

    @field_validator("pack_format", mode="before")
    @classmethod
    def parse_pack_format(cls, value: Any) -> PackFormat:
        return PackFormat.parse(value)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("Capability names must be non-empty and trimmed")
        return value


class Severity(IntEnum):
    """Diagnostic importance ordered so numeric comparison can gate builds."""

    INFO = 10
    WARNING = 20
    ERROR = 30

    @property
    def label(self) -> str:
        return self.name.lower()


class Compatibility(StrEnum):
    """Semantic confidence assigned to a migration or unresolved feature."""

    LOSSLESS = "lossless"
    EMULATED = "emulated"
    LOSSY = "lossy"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class BuildPolicy(FrozenModel):
    """User-selected policy that converts compatibility classes into permission."""

    allow_emulated: bool = True
    allow_lossy: bool = False
    allow_unknown: bool = False
    fail_on_warnings: bool = False

    def permits(self, compatibility: Compatibility) -> bool:
        """Return whether a target may contain this compatibility outcome."""

        if compatibility == Compatibility.LOSSLESS:
            return True
        if compatibility == Compatibility.EMULATED:
            return self.allow_emulated
        if compatibility == Compatibility.LOSSY:
            return self.allow_lossy
        if compatibility == Compatibility.UNKNOWN:
            return self.allow_unknown
        return False


class Diagnostic(BaseModel):
    """A source-located finding suitable for both CLI output and JSON reports."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    severity: Severity
    code: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    message: str = Field(min_length=1)
    path: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    compatibility: Compatibility | None = None
    rule_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    def __init__(
        self,
        severity: Severity | None = None,
        code: str | None = None,
        message: str | None = None,
        **data: Any,
    ) -> None:
        if severity is not None:
            data.setdefault("severity", severity)
        if code is not None:
            data.setdefault("code", code)
        if message is not None:
            data.setdefault("message", message)
        super().__init__(**data)

    def as_dict(self) -> dict[str, Any]:
        result = self.model_dump(mode="json", exclude_none=True)
        result["severity"] = self.severity.label
        if self.compatibility is not None:
            result["compatibility"] = self.compatibility.value
        return result


class DetectionEvidence(FrozenModel):
    """One weighted observation that raises the inferred minimum pack format."""

    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)
    minimum_format: PackFormat
    weight: float = Field(ge=0.0, le=1.0)
    path: str | None = None
    line: int | None = Field(default=None, ge=1)

    def __init__(
        self,
        kind: str | None = None,
        value: str | None = None,
        minimum_format: PackFormat | None = None,
        weight: float | None = None,
        path: str | None = None,
        line: int | None = None,
        **data: Any,
    ) -> None:
        if kind is not None:
            data.setdefault("kind", kind)
        if value is not None:
            data.setdefault("value", value)
        if minimum_format is not None:
            data.setdefault("minimum_format", minimum_format)
        if weight is not None:
            data.setdefault("weight", weight)
        data.setdefault("path", path)
        data.setdefault("line", line)
        super().__init__(**data)

    def as_dict(self) -> dict[str, Any]:
        result = self.model_dump(mode="json", exclude_none=True)
        result["minimum_format"] = str(self.minimum_format)
        return result


class ScanResult(BaseModel):
    """Static scan summary for a materialized pack tree."""

    model_config = ConfigDict(extra="forbid")

    inferred_format: PackFormat
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence: list[DetectionEvidence] = Field(default_factory=list)
    files_scanned: int = Field(default=0, ge=0)
    commands_scanned: int = Field(default=0, ge=0)
    json_files_scanned: int = Field(default=0, ge=0)

    def __init__(
        self,
        inferred_format: PackFormat | None = None,
        diagnostics: list[Diagnostic] | None = None,
        evidence: list[DetectionEvidence] | None = None,
        **data: Any,
    ) -> None:
        if inferred_format is not None:
            data.setdefault("inferred_format", inferred_format)
            data.setdefault("diagnostics", diagnostics or [])
            data.setdefault("evidence", evidence or [])
        super().__init__(**data)


class DetectionResult(BaseModel):
    """Selected source syntax plus evidence and metadata used to select it."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, arbitrary_types_allowed=True)

    source_format: PackFormat
    declared_range: PackFormatRange
    inferred_format: PackFormat
    candidates: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    description: Any
    metadata: dict[str, Any]
    evidence: list[DetectionEvidence] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class MigrationRecord(FrozenModel):
    """Auditable summary of one applied migration rule."""

    rule_id: str = Field(min_length=1)
    compatibility: Compatibility
    changed_files: int = Field(ge=0)
    changed_nodes: int = Field(default=0, ge=0)
    notes: tuple[str, ...] = ()

    def __init__(
        self,
        rule_id: str | None = None,
        compatibility: Compatibility | None = None,
        changed_files: int | None = None,
        changed_nodes: int = 0,
        **data: Any,
    ) -> None:
        if rule_id is not None:
            data.setdefault("rule_id", rule_id)
        if compatibility is not None:
            data.setdefault("compatibility", compatibility)
        if changed_files is not None:
            data.setdefault("changed_files", changed_files)
            data.setdefault("changed_nodes", changed_nodes)
        super().__init__(**data)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TargetBuildResult(BaseModel):
    """Files, diagnostics, and rule history produced for one target release."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    profile: VersionProfile
    directory: Path | None
    archive: Path | None
    diagnostics: list[Diagnostic]
    migrations: list[MigrationRecord]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def __init__(
        self,
        profile: VersionProfile | None = None,
        directory: Path | None = None,
        archive: Path | None = None,
        diagnostics: list[Diagnostic] | None = None,
        migrations: list[MigrationRecord] | None = None,
        sha256: str | None = None,
        **data: Any,
    ) -> None:
        if profile is not None:
            data.setdefault("profile", profile)
            data.setdefault("directory", directory)
            data.setdefault("archive", archive)
            data.setdefault("diagnostics", diagnostics or [])
            data.setdefault("migrations", migrations or [])
            data.setdefault("sha256", sha256)
        super().__init__(**data)

    @property
    def successful(self) -> bool:
        return not any(item.severity >= Severity.ERROR for item in self.diagnostics)

    @property
    def applied_rules(self) -> list[str]:
        return [record.rule_id for record in self.migrations]
