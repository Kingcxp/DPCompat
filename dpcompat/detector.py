"""Combine pack metadata and content evidence into a source-format estimate.

Detection is advisory rather than magical: identical pack formats can correspond to several
Minecraft releases, and metadata may describe a range.  The result therefore records both a
selected syntax format and auditable evidence instead of claiming an exact source release.
"""

from __future__ import annotations

from pathlib import Path

from .jsonutil import load_path
from .metadata import detect_format_range
from .models import DetectionResult, Diagnostic, PackFormat, Severity
from .scanner import scan_pack
from .versions import PROFILES, profiles_for_format


def _best_known_format_in_range(minimum: PackFormat, maximum: PackFormat) -> PackFormat | None:
    candidates = [profile.pack_format for profile in PROFILES if minimum <= profile.pack_format <= maximum]
    return max(candidates) if candidates else None


def detect_pack(root: Path) -> DetectionResult:
    """Detect the most plausible source format for a materialized pack directory."""

    metadata_path = root / "pack.mcmeta"
    metadata = load_path(metadata_path)
    if not isinstance(metadata, dict):
        raise ValueError("pack.mcmeta root must be a JSON object")

    declared_range, preferred = detect_format_range(metadata)
    scan = scan_pack(root)
    inferred = scan.inferred_format

    # pack_format remains the best source hint for old metadata. New range-only metadata is a
    # compatibility declaration, so content evidence is preferred and the newest registered
    # stable format in the declared range is only used to flatten an existing overlay source.
    source_format = max(preferred, inferred)
    if "overlays" in metadata and source_format == declared_range.minimum:
        known = _best_known_format_in_range(declared_range.minimum, declared_range.maximum)
        if known is not None:
            source_format = max(source_format, known)

    diagnostics = list(scan.diagnostics)
    if inferred > declared_range.maximum:
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "metadata-understates-content",
                f"Content appears to require format {inferred}, but pack.mcmeta ends at {declared_range.maximum}",
                path="pack.mcmeta",
            )
        )
    if not declared_range.contains(preferred):
        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                "metadata-source-outside-range",
                f"Declared pack_format {preferred} is outside {declared_range.minimum}..{declared_range.maximum}",
                path="pack.mcmeta",
            )
        )
    if declared_range.minimum != declared_range.maximum:
        diagnostics.append(
            Diagnostic(
                Severity.INFO,
                "metadata-range",
                (
                    f"Input declares compatibility from {declared_range.minimum} through "
                    f"{declared_range.maximum}; selected source syntax is {source_format}"
                ),
                path="pack.mcmeta",
            )
        )

    matches = profiles_for_format(source_format)
    candidates = [profile.game_version for profile in matches]
    evidence_weight = sum(item.weight for item in scan.evidence)
    confidence = 0.98 if matches and source_format == preferred else min(0.95, 0.65 + evidence_weight / 20)
    description = metadata.get("pack", {}).get("description", "DPCompat generated data pack")
    return DetectionResult(
        source_format=source_format,
        declared_range=declared_range,
        inferred_format=inferred,
        candidates=candidates,
        confidence=confidence,
        description=description,
        metadata=metadata,
        evidence=scan.evidence,
        diagnostics=diagnostics,
    )
