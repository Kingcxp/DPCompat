"""Securely materialize, flatten, hash, and archive data-pack trees.

All paths crossing the archive or fallback boundary are treated as untrusted.  Extraction
rejects traversal and special files, while output archives use fixed metadata and atomic
replacement so the same input produces the same bytes or no archive at all.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .metadata import overlay_matches
from .models import PackFormat

IGNORED_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
}


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if destination_resolved not in member_path.parents and member_path != destination_resolved:
            raise ValueError(f"Unsafe ZIP member path: {member.filename}")
        if member.is_dir():
            continue
        # Refuse symlinks and other special Unix entries.
        mode = member.external_attr >> 16
        if mode and (mode & 0o170000) not in {0, 0o100000}:
            raise ValueError(f"Unsafe ZIP member type: {member.filename}")
    archive.extractall(destination)


def validate_regular_tree(root: Path) -> None:
    """Reject symlinks and non-regular entries in directory inputs."""
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Data-pack directory must not contain symlinks: {path}")
        if path.exists() and not path.is_dir() and not path.is_file():
            raise ValueError(f"Data-pack directory contains a special entry: {path}")


def locate_pack_root(path: Path) -> Path:
    """Locate the unique directory containing ``pack.mcmeta``."""

    if (path / "pack.mcmeta").is_file():
        return path
    candidates = [candidate.parent for candidate in path.rglob("pack.mcmeta")]
    candidates = [candidate for candidate in candidates if len(candidate.relative_to(path).parts) <= 2]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"No pack.mcmeta found below {path}")
    rendered = ", ".join(sorted(candidate.relative_to(path).as_posix() or "." for candidate in candidates))
    raise ValueError(
        f"Multiple possible data-pack roots were found; select one with --pack-root. Candidates: {rendered}"
    )


@contextlib.contextmanager
def materialize_source(source: Path) -> Iterator[Path]:
    """Yield a validated pack root from a directory or plain ZIP input."""

    source = source.expanduser().resolve()
    if source.is_dir():
        root = locate_pack_root(source)
        validate_regular_tree(root)
        yield root
        return
    if source.is_file() and source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="dpcompat-source-") as temp_dir:
            destination = Path(temp_dir)
            with zipfile.ZipFile(source) as archive:
                _safe_extract(archive, destination)
            root = locate_pack_root(destination)
            validate_regular_tree(root)
            yield root
        return
    raise ValueError(f"Input must be a data-pack directory or ZIP file: {source}")


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_NAMES}


def copy_pack(source: Path, destination: Path) -> None:
    """Copy a pack tree while omitting transient build artifacts."""

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=_ignore)


def merge_tree(source: Path, destination: Path) -> None:
    """Merge source onto destination, replacing files and preserving unrelated paths."""
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def overlay_directories(metadata: dict[str, Any]) -> set[str]:
    """Return every directory reserved by source overlay declarations."""

    overlays = metadata.get("overlays")
    if not isinstance(overlays, dict):
        return set()
    entries = overlays.get("entries")
    if not isinstance(entries, list):
        return set()
    return {
        entry["directory"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("directory"), str)
    }


def flatten_pack(
    source: Path,
    destination: Path,
    source_format: PackFormat,
    metadata: dict[str, Any],
) -> list[str]:
    """Materialize the effective pack for one source format, including source overlays."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    overlay_dirs = overlay_directories(metadata)
    ignored = IGNORED_NAMES | overlay_dirs
    for child in source.iterdir():
        if child.name in ignored:
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=_ignore)
        elif child.is_file():
            shutil.copy2(child, target)

    applied: list[str] = []
    overlays = metadata.get("overlays")
    entries = overlays.get("entries") if isinstance(overlays, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or not overlay_matches(entry, source_format):
                continue
            directory = entry.get("directory")
            if not isinstance(directory, str):
                continue
            overlay_root = source / directory
            if not overlay_root.is_dir():
                raise ValueError(f"Overlay directory declared but missing: {directory}")
            merge_tree(overlay_root, destination)
            applied.append(directory)
    return applied
