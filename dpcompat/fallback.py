"""Apply author-reviewed target fallbacks without hiding unrelated failures.

A fallback may delete exact paths, overlay replacement files, and resolve exact diagnostic
codes with a written reason.  Wildcard suppression is intentionally unsupported so a fallback
cannot silently turn every future incompatibility into a successful build.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .models import Compatibility, Diagnostic, FrozenModel, Severity

MANIFEST_NAME = ".dpcompat-fallback.toml"


class FallbackResolution(FrozenModel):
    """Exact diagnostic suppression approved by an author with a written reason."""

    code: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    path: str | None
    reason: str = Field(min_length=1)


class FallbackSpec(FrozenModel):
    """Files, deletions, and diagnostic resolutions declared by one fallback."""

    root: Path
    delete: tuple[str, ...] = ()
    resolutions: tuple[FallbackResolution, ...] = ()


class FallbackApplication(BaseModel):
    """Mutable audit record produced while applying a fallback to one target."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    changed_files: int = Field(default=0, ge=0)
    deleted_paths: int = Field(default=0, ge=0)
    resolved_diagnostics: int = Field(default=0, ge=0)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


def _safe_relative(value: str, *, field_name: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"{field_name} must be a safe relative POSIX path: {value!r}")
    return candidate.as_posix()


def load_fallback(root: Path) -> FallbackSpec:
    """Validate and load ``fallback.toml`` from an author-owned directory."""

    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Fallback directory does not exist: {root}")
    manifest = root / MANIFEST_NAME
    if not manifest.exists():
        return FallbackSpec(root=root)
    with manifest.open("rb") as handle:
        raw = tomllib.load(handle)

    raw_delete = raw.get("delete", [])
    if not isinstance(raw_delete, list) or not all(isinstance(item, str) for item in raw_delete):
        raise ValueError(f"{MANIFEST_NAME}: delete must be an array of strings")
    delete = tuple(_safe_relative(item, field_name="delete entry") for item in raw_delete)

    raw_resolve = raw.get("resolve", [])
    if not isinstance(raw_resolve, list):
        raise ValueError(f"{MANIFEST_NAME}: [[resolve]] must be an array of tables")
    resolutions: list[FallbackResolution] = []
    for index, item in enumerate(raw_resolve):
        if not isinstance(item, dict):
            raise ValueError(f"{MANIFEST_NAME}: resolve entry {index} must be a table")
        code = item.get("code")
        reason = item.get("reason")
        path = item.get("path")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{MANIFEST_NAME}: resolve entry {index}.code must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{MANIFEST_NAME}: resolve entry {index}.reason must be non-empty")
        if path is not None and not isinstance(path, str):
            raise ValueError(f"{MANIFEST_NAME}: resolve entry {index}.path must be a string")
        resolutions.append(
            FallbackResolution(
                code=code.strip(),
                path=_safe_relative(path, field_name="resolve path") if path is not None else None,
                reason=reason.strip(),
            )
        )
    return FallbackSpec(root=root, delete=delete, resolutions=tuple(resolutions))


def _copy_fallback_files(spec: FallbackSpec, target_root: Path) -> int:
    changed = 0
    for path in sorted(spec.root.rglob("*")):
        if path == spec.root / MANIFEST_NAME:
            continue
        if path.is_symlink():
            raise ValueError(f"Fallback must not contain symlinks: {path}")
        relative = path.relative_to(spec.root)
        destination = target_root / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            changed += 1
    return changed


def apply_fallback_files(spec: FallbackSpec, target_root: Path) -> FallbackApplication:
    """Apply exact deletions and overlays while recording every filesystem change."""

    application = FallbackApplication()
    target_root = target_root.resolve()
    for relative in spec.delete:
        destination = (target_root / relative).resolve()
        if target_root not in destination.parents:
            raise ValueError(f"Fallback delete escaped target root: {relative}")
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
            application.deleted_paths += 1
        elif destination.is_dir():
            shutil.rmtree(destination)
            application.deleted_paths += 1
        else:
            application.diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "fallback-delete-missing",
                    f"Fallback requested deletion of a path that did not exist: {relative}",
                    path=relative,
                    compatibility=Compatibility.EMULATED,
                )
            )
    application.changed_files = _copy_fallback_files(spec, target_root)
    return application


def resolve_with_fallback(
    diagnostics: list[Diagnostic],
    spec: FallbackSpec,
    application: FallbackApplication,
) -> None:
    """Downgrade only diagnostics that exactly match reviewed resolutions."""

    for resolution in spec.resolutions:
        # Matching by code and optional exact path prevents one old fallback from hiding
        # unrelated diagnostics introduced by future scanner versions.
        matches = [
            item
            for item in diagnostics
            if item.code == resolution.code
            and (resolution.path is None or item.path == resolution.path)
            and item.severity >= Severity.WARNING
        ]
        if not matches:
            application.diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "fallback-resolution-unused",
                    f"No diagnostic matched fallback resolution {resolution.code!r}",
                    path=resolution.path,
                    compatibility=Compatibility.EMULATED,
                    details={"reason": resolution.reason},
                )
            )
            continue
        for item in matches:
            original = item.compatibility.value if item.compatibility is not None else None
            item.severity = Severity.INFO
            item.compatibility = Compatibility.EMULATED
            item.details = {
                **item.details,
                "fallback_resolution": {
                    "original_compatibility": original,
                    "reason": resolution.reason,
                },
            }
            application.resolved_diagnostics += 1
