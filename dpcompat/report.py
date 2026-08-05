"""Serialize detection and per-target build results into a stable JSON report.

The report is a public integration surface for CI and future editor plugins.  Schema changes
must increment the report schema number and retain the safety classification of every result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BuildPolicy, DetectionResult, TargetBuildResult


def build_report(
    detection: DetectionResult,
    results: list[TargetBuildResult],
    universal_archive: Path | None,
    *,
    policy: BuildPolicy | None = None,
) -> dict[str, Any]:
    """Build the versioned machine-readable report consumed by CI and reviewers."""

    policy = policy or BuildPolicy()
    return {
        "schema": 2,
        "source": {
            "detected_format": str(detection.source_format),
            "declared_range": {
                "minimum": str(detection.declared_range.minimum),
                "maximum": str(detection.declared_range.maximum),
            },
            "inferred_format": str(detection.inferred_format),
            "candidate_versions": detection.candidates,
            "confidence": detection.confidence,
            "evidence": [item.as_dict() for item in detection.evidence],
            "diagnostics": [item.as_dict() for item in detection.diagnostics],
        },
        "targets": [
            {
                "game_version": result.profile.game_version,
                "pack_format": str(result.profile.pack_format),
                "success": result.successful,
                "archive": str(result.archive) if result.archive else None,
                "sha256": result.sha256,
                "migrations": [record.as_dict() for record in result.migrations],
                "diagnostics": [item.as_dict() for item in result.diagnostics],
            }
            for result in results
        ],
        "universal_archive": str(universal_archive) if universal_archive else None,
        "policy": {
            "allow_emulated": policy.allow_emulated,
            "allow_lossy": policy.allow_lossy,
            "allow_unknown": policy.allow_unknown,
            "fail_on_warnings": policy.fail_on_warnings,
        },
        "scope": {
            "stable_releases_only": True,
            "binary_structure_nbt_rewritten": True,
            "command_parser": "top-level token and selected command grammars; not full Brigadier",
            "worldgen": "validated conservatively; non-identical Environment Attribute downgrade is refused",
            "safety": "unsupported, unproven, and policy-disallowed conversions fail the target build",
        },
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write a UTF-8 report with stable indentation and a final newline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
