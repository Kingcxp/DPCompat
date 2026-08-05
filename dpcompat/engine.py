"""Orchestrate isolated target builds and universal overlay packaging.

The engine owns the build transaction: materialize an effective source pack, apply migration
rules, merge reviewed fallbacks, rescan the result, enforce policy, and only then publish an
archive.  Mojang-specific field transformations belong in :mod:`dpcompat.migrations`, not in
this orchestration layer.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .detector import detect_pack
from .fallback import apply_fallback_files, load_fallback, resolve_with_fallback
from .jsonutil import dump_path
from .metadata import render_single_target_metadata, render_universal_metadata
from .migrations import BUILTIN_RULES, MigrationContext, MigrationRule
from .models import (
    BuildPolicy,
    Compatibility,
    Diagnostic,
    MigrationRecord,
    PackFormat,
    PackFormatRange,
    Severity,
    TargetBuildResult,
    VersionProfile,
)
from .packio import (
    copy_pack,
    create_deterministic_zip,
    flatten_pack,
    tree_sha256,
)
from .scanner import scan_pack
from .versions import unique_format_profiles

_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
logger = logging.getLogger(__name__)


def _safe_name(value: str) -> str:
    cleaned = _NAME_RE.sub("-", value.strip()).strip("-.")
    return cleaned or "datapack"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _enforce_policy(diagnostics: list[Diagnostic], policy: BuildPolicy) -> None:
    for item in diagnostics:
        compatibility = item.compatibility
        denied_compatibility = compatibility is not None and not policy.permits(compatibility)
        denied_warning = policy.fail_on_warnings and item.severity == Severity.WARNING
        if denied_compatibility or denied_warning:
            item.severity = Severity.ERROR


def _fallback_for(profile: VersionProfile, fallbacks: dict[str, Path]) -> Path | None:
    return fallbacks.get(profile.game_version) or fallbacks.get(str(profile.pack_format))


def build_target(
    effective_source: Path,
    source_format: PackFormat,
    original_metadata: dict[str, Any],
    description: Any,
    profile: VersionProfile,
    work_root: Path,
    output_root: Path,
    *,
    policy: BuildPolicy,
    fallbacks: dict[str, Path],
    output_name: str,
    emit_archive: bool,
    rules: tuple[MigrationRule, ...],
) -> TargetBuildResult:
    """Build one target in an isolated working tree and publish only on success."""

    target_dir = work_root / f"pack-{profile.game_version}"
    copy_pack(effective_source, target_dir)
    diagnostics: list[Diagnostic] = []
    migrations: list[MigrationRecord] = []
    context = MigrationContext(target_dir, source_format, profile.pack_format, policy)

    # Rules are deterministic and ordered.  A later rule may rely on structure created by
    # an earlier boundary migration, so do not sort this registry dynamically.
    logger.info("Building target Minecraft %s (format %s)", profile.game_version, profile.pack_format)
    for rule in rules:
        if not rule.applies(source_format, profile.pack_format):
            continue
        logger.debug("Applying rule %s to target %s", rule.id, profile.game_version)
        try:
            result = rule.apply(context)
        except Exception as exc:  # A rule failure must never produce a silent partial archive.
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "migration-rule-crashed",
                    f"{type(exc).__name__}: {exc}",
                    compatibility=Compatibility.UNKNOWN,
                    rule_id=rule.id,
                )
            )
            break
        migrations.append(result.record)
        diagnostics.extend(result.diagnostics)

    fallback = _fallback_for(profile, fallbacks)
    fallback_spec = None
    fallback_application = None
    if fallback is not None:
        try:
            fallback_spec = load_fallback(fallback)
            fallback_application = apply_fallback_files(fallback_spec, target_dir)
            migrations.append(
                MigrationRecord(
                    "project.target-fallback",
                    Compatibility.EMULATED,
                    changed_files=fallback_application.changed_files,
                    changed_nodes=fallback_application.deleted_paths,
                    notes=(str(fallback),),
                )
            )
        except (OSError, ValueError) as exc:
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "fallback-invalid",
                    str(exc),
                    compatibility=Compatibility.UNSUPPORTED,
                )
            )

    # Rescan the transformed tree.  Pre-migration diagnostics are not enough because a
    # rule or fallback can introduce a target-only path, malformed JSON, or unresolved ID.
    scan = scan_pack(target_dir, target=profile.pack_format)
    diagnostics.extend(scan.diagnostics)
    if fallback_spec is not None and fallback_application is not None:
        resolve_with_fallback(diagnostics, fallback_spec, fallback_application)
        diagnostics.extend(fallback_application.diagnostics)
        if fallback_application.resolved_diagnostics:
            migrations.append(
                MigrationRecord(
                    "project.fallback-resolutions",
                    Compatibility.EMULATED,
                    changed_files=0,
                    changed_nodes=fallback_application.resolved_diagnostics,
                    notes=("Explicit author-reviewed diagnostic resolutions",),
                )
            )
    _enforce_policy(diagnostics, policy)

    target_metadata = render_single_target_metadata(original_metadata, profile.pack_format, description)
    dump_path(target_dir / "pack.mcmeta", target_metadata)

    # Fail closed: a target with any error never reaches the output directory.
    if any(item.severity >= Severity.ERROR for item in diagnostics):
        logger.warning("Target %s failed with %d diagnostic(s)", profile.game_version, len(diagnostics))
        shutil.rmtree(target_dir, ignore_errors=True)
        return TargetBuildResult(profile, None, None, diagnostics, migrations)

    content_hash = tree_sha256(target_dir, include_mcmeta=False)
    archive: Path | None = None
    archive_hash: str | None = None
    if emit_archive:
        output_root.mkdir(parents=True, exist_ok=True)
        archive = output_root / f"{_safe_name(output_name)}-{profile.game_version}.zip"
        create_deterministic_zip(target_dir, archive)
        archive_hash = _sha256_file(archive)
    else:
        archive_hash = content_hash
    logger.info("Target %s built successfully", profile.game_version)
    return TargetBuildResult(profile, target_dir, archive, diagnostics, migrations, archive_hash)


def _write_universal_guard(universal_root: Path) -> None:
    function = universal_root / "data/dpcompat/function/unsupported_format.mcfunction"
    function.parent.mkdir(parents=True, exist_ok=True)
    function.write_text(
        'tellraw @a [{"text":"[DPCompat] "},'
        '{"text":"This data-pack format was not built or tested. Use a listed stable target.",'
        '"color":"red"}]\n'
        "data modify storage dpcompat:status unsupported_format set value 1b\n",
        encoding="utf-8",
    )
    load_tag = universal_root / "data/minecraft/tags/function/load.json"
    load_tag.parent.mkdir(parents=True, exist_ok=True)
    dump_path(load_tag, {"replace": True, "values": ["dpcompat:unsupported_format"]})


def _ensure_overlay_load_override(overlay_root: Path) -> None:
    load_tag = overlay_root / "data/minecraft/tags/function/load.json"
    if not load_tag.exists():
        load_tag.parent.mkdir(parents=True, exist_ok=True)
        dump_path(load_tag, {"replace": True, "values": []})


def build_universal(
    effective_source: Path,
    original_metadata: dict[str, Any],
    description: Any,
    results: list[TargetBuildResult],
    work_root: Path,
    output_root: Path,
    *,
    output_name: str,
    emit_archive: bool,
) -> Path | None:
    """Package successful unique-format targets as complete overlay data trees."""

    successful = [result for result in results if result.successful and result.directory]
    profiles = unique_format_profiles([result.profile for result in successful])
    if len(profiles) < 2:
        return None

    result_by_format = {result.profile.pack_format: result for result in successful if result.directory is not None}
    universal_root = work_root / "universal"
    universal_root.mkdir(parents=True)

    for child in effective_source.iterdir():
        if child.name in {"pack.mcmeta", "data"}:
            continue
        destination = universal_root / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)
    _write_universal_guard(universal_root)

    ranges: list[tuple[PackFormatRange, str]] = []
    for profile in profiles:
        result = result_by_format[profile.pack_format]
        directory_name = f"fmt_{profile.pack_format.major}_{profile.pack_format.minor}"
        overlay_root = universal_root / directory_name
        overlay_root.mkdir(parents=True)
        source_data = result.directory / "data"  # type: ignore[operator]
        if source_data.is_dir():
            shutil.copytree(source_data, overlay_root / "data")
        else:
            (overlay_root / "data").mkdir()
        _ensure_overlay_load_override(overlay_root)
        ranges.append((PackFormatRange(profile.pack_format, profile.pack_format), directory_name))

    universal_description: dict[str, Any] = {
        "text": "DPCompat universal pack — stable releases 1.21.4+",
        "color": "gold",
    }
    if isinstance(description, str) and description:
        universal_description["extra"] = [{"text": f" | {description}", "color": "gray"}]

    metadata = render_universal_metadata(
        original_metadata,
        ranges,
        universal_description,
    )
    dump_path(universal_root / "pack.mcmeta", metadata)
    if not emit_archive:
        return None
    archive = output_root / f"{_safe_name(output_name)}-universal-1.21.4-plus.zip"
    create_deterministic_zip(universal_root, archive)
    return archive


def compile_pack(
    source_root: Path,
    profiles: list[VersionProfile],
    output_root: Path,
    universal: bool = True,
    *,
    policy: BuildPolicy | None = None,
    source_format: PackFormat | None = None,
    fallbacks: dict[str, Path] | None = None,
    output_name: str = "datapack",
    emit_archives: bool = True,
    rules: tuple[MigrationRule, ...] | None = None,
) -> tuple[Any, list[TargetBuildResult], Path | None]:
    """Run source detection and the full multi-target build transaction."""

    policy = policy or BuildPolicy()
    fallbacks = fallbacks or {}
    effective_rules = rules if rules is not None else BUILTIN_RULES
    logger.info("Inspecting source data pack at %s", source_root)
    detection = detect_pack(source_root)
    if source_format is not None:
        detection.source_format = source_format
        detection.candidates = []
        detection.confidence = 1.0
        detection.diagnostics.append(
            Diagnostic(
                Severity.INFO,
                "source-format-overridden",
                f"Using explicitly configured source format {source_format}",
                path="pack.mcmeta",
            )
        )
    _enforce_policy(detection.diagnostics, policy)
    if any(item.severity >= Severity.ERROR for item in detection.diagnostics):
        return detection, [], None

    output_root = output_root.expanduser().resolve()
    if emit_archives:
        output_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dpcompat-build-") as temp_dir:
        work_root = Path(temp_dir)
        effective_source = work_root / "effective-source"
        applied_overlays = flatten_pack(
            source_root,
            effective_source,
            detection.source_format,
            detection.metadata,
        )
        if applied_overlays:
            detection.diagnostics.append(
                Diagnostic(
                    Severity.INFO,
                    "source-overlays-flattened",
                    "Applied source overlay directories: " + ", ".join(applied_overlays),
                    path="pack.mcmeta",
                )
            )
        source_scan = scan_pack(effective_source, target=detection.source_format)
        detection.diagnostics.extend(source_scan.diagnostics)
        _enforce_policy(detection.diagnostics, policy)
        if any(item.severity >= Severity.ERROR for item in detection.diagnostics):
            return detection, [], None

        results = [
            build_target(
                effective_source,
                detection.source_format,
                detection.metadata,
                detection.description,
                profile,
                work_root,
                output_root,
                policy=policy,
                fallbacks=fallbacks,
                output_name=output_name,
                emit_archive=emit_archives,
                rules=effective_rules,
            )
            for profile in profiles
        ]
        universal_archive = (
            build_universal(
                effective_source,
                detection.metadata,
                detection.description,
                results,
                work_root,
                output_root,
                output_name=output_name,
                emit_archive=emit_archives,
            )
            if universal
            else None
        )
        for result in results:
            result.directory = None
    return detection, results, universal_archive
