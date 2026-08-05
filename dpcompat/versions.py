"""Load and validate stable release profiles from the bundled manifest."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from itertools import pairwise
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import PackFormat, VersionProfile


class ReleaseManifest(BaseModel):
    """Strict on-disk schema for ``data/releases.json``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(alias="schema", ge=1)
    updated: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    releases: tuple[VersionProfile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_release_order(self) -> Self:
        versions = [profile.game_version for profile in self.releases]
        if len(versions) != len(set(versions)):
            raise ValueError("Release manifest contains duplicate game versions")
        dates = [profile.release_date for profile in self.releases]
        if dates != sorted(dates):
            raise ValueError("Release manifest must be ordered by release date")
        formats = [profile.pack_format for profile in self.releases]
        if any(current < previous for previous, current in pairwise(formats)):
            raise ValueError("Pack formats must not decrease across stable releases")
        return self


def _load_json(name: str) -> dict[str, Any]:
    resource = files("dpcompat.data").joinpath(name)
    parsed = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return parsed


@lru_cache(maxsize=1)
def release_manifest() -> ReleaseManifest:
    """Return the validated, cached release manifest."""

    return ReleaseManifest.model_validate(_load_json("releases.json"))


PROFILES: tuple[VersionProfile, ...] = release_manifest().releases
LATEST_PROFILE = PROFILES[-1]
_BY_GAME_VERSION = {profile.game_version: profile for profile in PROFILES}


def profiles_for_format(pack_format: PackFormat) -> list[VersionProfile]:
    """Return every registered release sharing ``pack_format``."""

    return [profile for profile in PROFILES if profile.pack_format == pack_format]


def resolve_profile(value: str) -> VersionProfile:
    """Resolve a game version, pack format, or ``latest`` to one stable profile."""

    normalized = value.strip().lower()
    if normalized == "latest":
        return LATEST_PROFILE
    if normalized in _BY_GAME_VERSION:
        return _BY_GAME_VERSION[normalized]
    try:
        pack_format = PackFormat.parse(normalized)
    except ValueError as exc:
        supported = ", ".join(profile.game_version for profile in PROFILES)
        raise ValueError(f"Unknown target {value!r}. Supported: {supported}, latest") from exc
    matches = profiles_for_format(pack_format)
    if not matches:
        raise ValueError(f"No stable release uses data-pack format {pack_format}")
    return matches[-1]


def unique_format_profiles(profiles: list[VersionProfile]) -> list[VersionProfile]:
    """Collapse releases sharing a format, keeping the latest profile for each."""

    by_format: dict[PackFormat, VersionProfile] = {}
    for profile in profiles:
        by_format[profile.pack_format] = profile
    return sorted(by_format.values(), key=lambda profile: profile.pack_format)


def profiles_between(minimum: PackFormat, maximum: PackFormat) -> list[VersionProfile]:
    """Return registered stable releases within an inclusive format range."""

    return [profile for profile in PROFILES if minimum <= profile.pack_format <= maximum]
