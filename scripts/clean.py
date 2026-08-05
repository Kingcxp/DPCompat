"""Remove only known DPCompat-generated local artifacts after validating the project root."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> None:
    """Delete caches, logs, distributions, and build outputs from this project only."""

    root = Path(__file__).resolve().parents[1]
    marker = root / "pyproject.toml"
    if not marker.is_file() or 'name = "dpcompat"' not in marker.read_text(encoding="utf-8"):
        raise RuntimeError(f"Refusing to clean an unrecognized directory: {root}")
    directories = (
        root / ".mypy_cache",
        root / ".pytest_cache",
        root / ".ruff_cache",
        root / ".cache",
        root / "build",
        root / "dist",
        root / "logs",
    )
    for path in directories:
        if path.is_dir():
            shutil.rmtree(path)
            print(f"removed {path.relative_to(root)}")
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path)
            print(f"removed {path.relative_to(root)}")


if __name__ == "__main__":
    main()
