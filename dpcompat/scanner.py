"""Statically scan a pack for version evidence and unsafe target features.

Scanning is read-only.  It validates paths and JSON, records minimum-format evidence, and emits
conservative diagnostics for syntax that the migration layer cannot prove safe.  A clean scan
means “no known blocker”, not behavioral equivalence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .commands import iter_execute_segments, parse_command_line
from .jsonutil import JsonNormalizationError, is_strict_json, load_path
from .manifests import feature_specs, identifier_minimums, resource_minimums
from .models import (
    Compatibility,
    DetectionEvidence,
    Diagnostic,
    PackFormat,
    ScanResult,
    Severity,
)

_NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")
_RESOURCE_PATH_RE = re.compile(r"^[a-z0-9_./-]+$")
_RUNTIME_RESOURCE_SUFFIXES = frozenset({".json", ".mcfunction", ".nbt"})

# Resource directories that exist somewhere in the supported 1.21.4+ range. This is used for
# typo detection only; feature minimums are loaded from the reviewable feature manifest.
KNOWN_RESOURCE_TYPES = frozenset(
    {
        "advancement",
        "banner_pattern",
        "biome",
        "cat_sound_variant",
        "cat_variant",
        "chicken_sound_variant",
        "cow_sound_variant",
        "damage_type",
        "dialog",
        "dimension",
        "dimension_type",
        "enchantment",
        "enchantment_provider",
        "flat_level_generator_preset",
        "fluid",
        "frog_variant",
        "function",
        "instrument",
        "item_modifier",
        "jukebox_song",
        "loot_table",
        "painting_variant",
        "pig_sound_variant",
        "pig_variant",
        "predicate",
        "recipe",
        "structure",
        "structure_set",
        "sulfur_cube_archetype",
        "tags",
        "test_environment",
        "test_instance",
        "timeline",
        "trade_set",
        "trim_material",
        "trim_pattern",
        "villager_trade",
        "wolf_sound_variant",
        "wolf_variant",
        "world_clock",
        "worldgen",
        "zombie_nautilus_variant",
    }
)


def _iter_json_nodes(value: Any, path: tuple[str | int, ...] = ()) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    yield path, value
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_json_nodes(item, (*path, index))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_json_nodes(item, (*path, key))


def _iter_json_strings(value: Any) -> Iterable[tuple[tuple[str | int, ...], str]]:
    for path, node in _iter_json_nodes(value):
        if isinstance(node, str):
            yield path, node


def _feature_diagnostic(
    *,
    code: str,
    message: str,
    minimum: PackFormat,
    target: PackFormat | None,
    path: str,
    line: int | None = None,
    feature_id: str | None = None,
    source_url: str | None = None,
) -> Diagnostic | None:
    if target is None or target >= minimum:
        return None
    details = {"required_format": str(minimum), "target_format": str(target)}
    if source_url:
        details["source"] = source_url
    return Diagnostic(
        Severity.ERROR,
        code,
        message,
        path=path,
        line=line,
        compatibility=Compatibility.UNSUPPORTED,
        rule_id=feature_id,
        details=details,
    )


def _record_evidence(
    evidence: list[DetectionEvidence],
    inferred: PackFormat,
    *,
    kind: str,
    value: str,
    minimum: PackFormat,
    weight: float,
    path: str,
    line: int | None = None,
) -> PackFormat:
    evidence.append(DetectionEvidence(kind, value, minimum, weight, path, line))
    return max(inferred, minimum)


def _scan_command(
    command: str,
    *,
    relative: str,
    line_number: int,
    target: PackFormat | None,
    inferred: PackFormat,
    diagnostics: list[Diagnostic],
    evidence: list[DetectionEvidence],
) -> PackFormat:
    parsed = parse_command_line(command)
    if not parsed.tokens:
        return inferred

    for segment in iter_execute_segments(parsed):
        segment_values = tuple(token.value for token in segment)
        segment_prefixes = {
            " ".join(segment_values[:1]),
            " ".join(segment_values[:2]),
        }
        for spec in feature_specs():
            for command_name in spec.commands:
                if command_name not in segment_prefixes:
                    continue
                inferred = _record_evidence(
                    evidence,
                    inferred,
                    kind="command",
                    value=command_name,
                    minimum=spec.min_format,
                    weight=0.9,
                    path=relative,
                    line=line_number,
                )
                diagnostic = _feature_diagnostic(
                    code="command-too-new",
                    message=f"Command feature '{command_name}' requires data-pack format {spec.min_format} or newer",
                    minimum=spec.min_format,
                    target=target,
                    path=relative,
                    line=line_number,
                    feature_id=spec.id,
                    source_url=str(spec.source),
                )
                if diagnostic:
                    diagnostics.append(diagnostic)
        if len(segment_values) >= 3 and segment_values[0] == "execute":
            for index in range(1, len(segment_values) - 1):
                if segment_values[index] in {"if", "unless"} and segment_values[index + 1] == "stopwatch":
                    minimum = PackFormat(94, 1)
                    inferred = _record_evidence(
                        evidence,
                        inferred,
                        kind="command",
                        value="execute stopwatch condition",
                        minimum=minimum,
                        weight=0.95,
                        path=relative,
                        line=line_number,
                    )
                    diagnostic = _feature_diagnostic(
                        code="command-too-new",
                        message=f"execute stopwatch conditions require data-pack format {minimum} or newer",
                        minimum=minimum,
                        target=target,
                        path=relative,
                        line=line_number,
                        feature_id="mounts_of_mayhem_resources",
                    )
                    if diagnostic:
                        diagnostics.append(diagnostic)

    for identifier, (minimum, spec) in identifier_minimums().items():
        if not any(identifier in token.value for token in parsed.tokens):
            continue
        inferred = _record_evidence(
            evidence,
            inferred,
            kind="identifier",
            value=identifier,
            minimum=minimum,
            weight=0.75,
            path=relative,
            line=line_number,
        )
        diagnostic = _feature_diagnostic(
            code="identifier-too-new",
            message=f"Identifier {identifier} requires data-pack format {minimum} or newer",
            minimum=minimum,
            target=target,
            path=relative,
            line=line_number,
            feature_id=spec.id,
            source_url=str(spec.source),
        )
        if diagnostic:
            diagnostics.append(diagnostic)

    command_text = command
    if target is not None and target >= PackFormat(71) and "show_in_tooltip" in command_text:
        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                "legacy-item-component-command",
                (
                    "Legacy show_in_tooltip inside command item syntax requires a component parser; "
                    "no safe rewrite was proven"
                ),
                path=relative,
                line=line_number,
                compatibility=Compatibility.UNKNOWN,
                rule_id="item-components.tooltip-display-and-simplification@71",
            )
        )
    if target is not None and target < PackFormat(71) and "minecraft:tooltip_display" in command_text:
        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                "new-item-component-command",
                "minecraft:tooltip_display cannot be represented safely in 1.21.4 command item syntax",
                path=relative,
                line=line_number,
                compatibility=Compatibility.LOSSY,
                rule_id="item-components.tooltip-display-and-simplification@71",
            )
        )

    return inferred


def _scan_json_semantics(
    value: Any,
    *,
    relative: str,
    target: PackFormat | None,
    inferred: PackFormat,
    diagnostics: list[Diagnostic],
    evidence: list[DetectionEvidence],
) -> PackFormat:
    for _, text in _iter_json_strings(value):
        item = identifier_minimums().get(text)
        if item is None:
            continue
        minimum, spec = item
        inferred = _record_evidence(
            evidence,
            inferred,
            kind="identifier",
            value=text,
            minimum=minimum,
            weight=0.75,
            path=relative,
        )
        diagnostic = _feature_diagnostic(
            code="identifier-too-new",
            message=f"Identifier {text} requires data-pack format {minimum} or newer",
            minimum=minimum,
            target=target,
            path=relative,
            feature_id=spec.id,
            source_url=str(spec.source),
        )
        if diagnostic:
            diagnostics.append(diagnostic)

    for _, node in _iter_json_nodes(value):
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        # ``type`` is a common schema key and is not always a string. Damage predicates,
        # for example, use an object-valued ``damage.type``. Only string text-component
        # discriminators can identify the format-88 sprite component.
        if isinstance(node_type, str) and node_type in {"sprite", "minecraft:sprite"}:
            minimum = PackFormat(88)
            inferred = _record_evidence(
                evidence,
                inferred,
                kind="text-component",
                value="sprite",
                minimum=minimum,
                weight=0.9,
                path=relative,
            )
            diagnostic = _feature_diagnostic(
                code="text-sprite-too-new",
                message=f"Sprite text components require data-pack format {minimum} or newer",
                minimum=minimum,
                target=target,
                path=relative,
                feature_id="copper_age_resources",
            )
            if diagnostic:
                diagnostics.append(diagnostic)

        if node.get("function") == "minecraft:discard":
            minimum = PackFormat(94, 1)
            inferred = _record_evidence(
                evidence,
                inferred,
                kind="loot-function",
                value="minecraft:discard",
                minimum=minimum,
                weight=0.95,
                path=relative,
            )
            diagnostic = _feature_diagnostic(
                code="loot-function-too-new",
                message=f"minecraft:discard requires data-pack format {minimum} or newer",
                minimum=minimum,
                target=target,
                path=relative,
                feature_id="mounts_of_mayhem_resources",
            )
            if diagnostic:
                diagnostics.append(diagnostic)

        if node.get("function") == "minecraft:filtered" and "on_fail" in node:
            minimum = PackFormat(94, 1)
            inferred = _record_evidence(
                evidence,
                inferred,
                kind="loot-function-field",
                value="minecraft:filtered.on_fail",
                minimum=minimum,
                weight=0.95,
                path=relative,
            )
            diagnostic = _feature_diagnostic(
                code="loot-filter-on-fail-too-new",
                message=f"minecraft:filtered.on_fail requires data-pack format {minimum} or newer",
                minimum=minimum,
                target=target,
                path=relative,
                feature_id="mounts_of_mayhem_resources",
            )
            if diagnostic:
                diagnostics.append(diagnostic)

    resource_path = "/" + relative
    if (
        any(marker in resource_path for marker in ("/dimension_type/", "/biome/"))
        and isinstance(value, dict)
        and any(key in value for key in ("attributes", "timelines", "skybox", "cardinal_light", "has_fixed_time"))
    ):
        minimum = PackFormat(94, 1)
        inferred = _record_evidence(
            evidence,
            inferred,
            kind="environment-attributes",
            value="attributes",
            minimum=minimum,
            weight=1.0,
            path=relative,
        )
        diagnostic = _feature_diagnostic(
            code="environment-attributes-too-new",
            message=(
                "Environment Attributes and the new dimension visual fields require format 94.1; "
                "official mappings are sometimes non-identical, so automatic downgrade is refused"
            ),
            minimum=minimum,
            target=target,
            path=relative,
            feature_id="environment-attributes@94.1",
            source_url="https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11",
        )
        if diagnostic:
            diagnostics.append(diagnostic)

    if (
        "/dimension_type/" in resource_path
        and isinstance(value, dict)
        and any(key in value for key in ("default_clock", "has_ender_dragon_fight"))
    ):
        minimum = PackFormat(101, 1)
        inferred = _record_evidence(
            evidence,
            inferred,
            kind="dimension-type-field",
            value="default_clock",
            minimum=minimum,
            weight=1.0,
            path=relative,
        )
        diagnostic = _feature_diagnostic(
            code="dimension-type-field-too-new",
            message=(
                "Dimension-type default_clock and has_ender_dragon_fight require format 101.1 "
                "and have no older equivalent"
            ),
            minimum=minimum,
            target=target,
            path=relative,
            feature_id="world-clock-dimension-fields@101.1",
            source_url="https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1",
        )
        if diagnostic:
            diagnostics.append(diagnostic)

    return inferred


def scan_pack(root: Path, target: PackFormat | None = None) -> ScanResult:
    """Scan a materialized pack and optionally validate it against ``target``."""

    inferred = PackFormat(61)
    diagnostics: list[Diagnostic] = []
    evidence: list[DetectionEvidence] = []
    files_scanned = 0
    commands_scanned = 0
    json_files_scanned = 0

    data_root = root / "data"
    if not data_root.exists():
        diagnostics.append(Diagnostic(Severity.WARNING, "missing-data-directory", "The pack has no data directory"))
        return ScanResult(inferred, diagnostics, evidence)

    if not data_root.is_dir():
        diagnostics.append(
            Diagnostic(Severity.ERROR, "invalid-data-directory", "data must be a directory", path="data")
        )
        return ScanResult(inferred, diagnostics, evidence)

    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        files_scanned += 1
        relative_path = path.relative_to(root)
        relative = relative_path.as_posix()
        parts = relative_path.parts

        if len(parts) >= 2 and not _NAMESPACE_RE.fullmatch(parts[1]):
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "invalid-namespace",
                    f"Invalid namespace {parts[1]!r}; use lowercase [a-z0-9_.-]",
                    path=relative,
                )
            )

        if len(parts) >= 4:
            resource_type = parts[2]
            resource_tail = "/".join(parts[3:])
            if resource_type not in KNOWN_RESOURCE_TYPES:
                diagnostics.append(
                    Diagnostic(
                        Severity.WARNING,
                        "unknown-resource-type",
                        (
                            f"Unknown data-pack resource directory {resource_type!r}; it will be "
                            "copied but not semantically validated"
                        ),
                        path=relative,
                        compatibility=Compatibility.UNKNOWN,
                    )
                )
            if not _RESOURCE_PATH_RE.fullmatch(resource_tail):
                runtime_resource = path.suffix.lower() in _RUNTIME_RESOURCE_SUFFIXES
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR if runtime_resource else Severity.WARNING,
                        "invalid-resource-path" if runtime_resource else "non-runtime-file-invalid-path",
                        (
                            "Runtime resource paths must use lowercase letters, digits, '_', '-', '.', and '/'"
                            if runtime_resource
                            else (
                                "A non-runtime file under data uses a path that is not a valid resource location; "
                                "DPCompat does not parse this extension as a runtime resource, but moving "
                                "documentation outside data avoids ambiguous loader behavior"
                            )
                        ),
                        path=relative,
                    )
                )

            feature = resource_minimums().get(resource_type)
            if feature is not None:
                minimum, spec = feature
                inferred = _record_evidence(
                    evidence,
                    inferred,
                    kind="resource-type",
                    value=resource_type,
                    minimum=minimum,
                    weight=1.0,
                    path=relative,
                )
                diagnostic = _feature_diagnostic(
                    code="resource-too-new",
                    message=f"Resource type '{resource_type}' requires data-pack format {minimum} or newer",
                    minimum=minimum,
                    target=target,
                    path=relative,
                    feature_id=spec.id,
                    source_url=str(spec.source),
                )
                if diagnostic:
                    diagnostics.append(diagnostic)

        if path.suffix == ".mcfunction":
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "invalid-utf8",
                        "mcfunction files must be UTF-8",
                        path=relative,
                    )
                )
                continue
            for line_number, raw_line in enumerate(lines, start=1):
                command = raw_line.strip()
                if not command or command.startswith("#"):
                    continue
                commands_scanned += 1
                inferred = _scan_command(
                    command,
                    relative=relative,
                    line_number=line_number,
                    target=target,
                    inferred=inferred,
                    diagnostics=diagnostics,
                    evidence=evidence,
                )

        if path.suffix == ".json":
            json_files_scanned += 1
            try:
                text = path.read_text(encoding="utf-8")
                value = load_path(path)
            except (OSError, UnicodeDecodeError, JsonNormalizationError) as exc:
                diagnostics.append(Diagnostic(Severity.ERROR, "invalid-json", str(exc), path=relative))
                continue

            if target is not None and target >= PackFormat(80) and not is_strict_json(text, source=relative):
                diagnostics.append(
                    Diagnostic(
                        Severity.WARNING,
                        "json-requires-normalization",
                        "Target uses strict JSON; the build pipeline must normalize this file",
                        path=relative,
                        compatibility=Compatibility.LOSSLESS,
                    )
                )

            inferred = _scan_json_semantics(
                value,
                relative=relative,
                target=target,
                inferred=inferred,
                diagnostics=diagnostics,
                evidence=evidence,
            )

    return ScanResult(
        inferred,
        diagnostics,
        evidence,
        files_scanned=files_scanned,
        commands_scanned=commands_scanned,
        json_files_scanned=json_files_scanned,
    )
