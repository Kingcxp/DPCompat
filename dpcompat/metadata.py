"""Parse and render legacy and modern ``pack.mcmeta`` format declarations.

Minecraft formats before the major/minor metadata transition use ``pack_format`` and
``supported_formats``.  Newer formats use ``min_format`` and ``max_format``.  The helpers here
preserve the different range semantics and generate overlay entries accepted on both sides of
the transition.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import PackFormat, PackFormatRange

# An integer modern max_format means “all minor versions of this major”.  Represent
# that open upper end with a large sentinel while comparing typed PackFormat values.
_MAX_MINOR = 2_147_483_647


def _parse_old_supported(value: Any, fallback: PackFormat) -> PackFormatRange:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = PackFormat(value)
        return PackFormatRange(parsed, parsed)
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return PackFormatRange(PackFormat(value[0]), PackFormat(value[1]))
    if isinstance(value, dict):
        minimum = value.get("min_inclusive", fallback.major)
        maximum = value.get("max_inclusive", fallback.major)
        if isinstance(minimum, int) and isinstance(maximum, int):
            return PackFormatRange(PackFormat(minimum), PackFormat(maximum))
    return PackFormatRange(fallback, fallback)


def _parse_new_bound(value: Any, *, maximum: bool) -> PackFormat:
    if isinstance(value, int) and not isinstance(value, bool):
        return PackFormat(value, _MAX_MINOR if maximum else 0)
    if isinstance(value, list) and len(value) == 1:
        parsed = PackFormat.parse(value)
        return PackFormat(parsed.major, _MAX_MINOR if maximum else 0)
    return PackFormat.parse(value)


def detect_format_range(metadata: dict[str, Any]) -> tuple[PackFormatRange, PackFormat]:
    """Return the declared compatibility range and preferred source syntax format."""

    pack = metadata.get("pack")
    if not isinstance(pack, dict):
        raise ValueError("pack.mcmeta must contain an object field named 'pack'")

    old_format_value = pack.get("pack_format")
    old_format = PackFormat.parse(old_format_value) if old_format_value is not None else None

    min_value = pack.get("min_format")
    max_value = pack.get("max_format")
    if min_value is not None or max_value is not None:
        if min_value is None or max_value is None:
            raise ValueError("pack.min_format and pack.max_format must be provided together")
        minimum = _parse_new_bound(min_value, maximum=False)
        maximum = _parse_new_bound(max_value, maximum=True)
        declared = PackFormat.parse(old_format_value) if old_format is not None else minimum
        return PackFormatRange(minimum, maximum), declared

    if old_format is None:
        raise ValueError("pack.mcmeta has neither pack_format nor min_format/max_format")
    return _parse_old_supported(pack.get("supported_formats"), old_format), old_format


def render_single_target_metadata(original: dict[str, Any], target: PackFormat, description: Any) -> dict[str, Any]:
    """Render metadata for exactly one target and remove source overlay declarations."""

    result = deepcopy(original)
    result.pop("overlays", None)
    pack = result.setdefault("pack", {})
    if not isinstance(pack, dict):
        pack = {}
        result["pack"] = pack

    pack["description"] = description
    for key in ("pack_format", "supported_formats", "min_format", "max_format"):
        pack.pop(key, None)

    if target.major < 82:
        pack["pack_format"] = target.major
        pack["supported_formats"] = target.major
    else:
        pack["min_format"] = target.exact_metadata_value()
        pack["max_format"] = target.exact_metadata_value()
    return result


def render_universal_metadata(
    original: dict[str, Any],
    ranges: list[tuple[PackFormatRange, str]],
    description: Any,
) -> dict[str, Any]:
    """Render a cross-era metadata file whose overlays cover the supplied ranges."""

    if not ranges:
        raise ValueError("At least one overlay range is required")
    ordered = sorted(ranges, key=lambda item: item[0].minimum)
    minimum = ordered[0][0].minimum
    maximum = ordered[-1][0].maximum
    contains_legacy = minimum.major < 82

    result = deepcopy(original)
    result["pack"] = {
        "description": description,
        "min_format": minimum.exact_metadata_value(),
        "max_format": maximum.exact_metadata_value(),
    }
    if contains_legacy:
        result["pack"]["pack_format"] = minimum.major
        result["pack"]["supported_formats"] = {
            "min_inclusive": minimum.major,
            "max_inclusive": maximum.major,
        }

    entries: list[dict[str, Any]] = []
    for pack_range, directory in ordered:
        entry: dict[str, Any] = {
            "directory": directory,
            "min_format": pack_range.minimum.exact_metadata_value(),
            "max_format": pack_range.maximum.exact_metadata_value(),
        }
        if contains_legacy:
            if pack_range.minimum.major == pack_range.maximum.major:
                entry["formats"] = pack_range.minimum.major
            else:
                entry["formats"] = {
                    "min_inclusive": pack_range.minimum.major,
                    "max_inclusive": pack_range.maximum.major,
                }
        entries.append(entry)
    result["overlays"] = {"entries": entries}
    return result


def overlay_matches(entry: dict[str, Any], pack_format: PackFormat) -> bool:
    """Evaluate an overlay entry using new metadata first, then the legacy formats field."""

    if "min_format" in entry and "max_format" in entry:
        minimum = _parse_new_bound(entry["min_format"], maximum=False)
        maximum = _parse_new_bound(entry["max_format"], maximum=True)
        return minimum <= pack_format <= maximum

    formats = entry.get("formats")
    if isinstance(formats, int):
        return pack_format.major == formats
    if (
        isinstance(formats, list)
        and len(formats) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in formats)
    ):
        return int(formats[0]) <= pack_format.major <= int(formats[1])
    if isinstance(formats, dict):
        legacy_minimum = formats.get("min_inclusive")
        legacy_maximum = formats.get("max_inclusive")
        if isinstance(legacy_minimum, int) and isinstance(legacy_maximum, int):
            return legacy_minimum <= pack_format.major <= legacy_maximum
    return False
