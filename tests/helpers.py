from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def make_pack(root: Path, pack_format: int | list[int] = 61, *, description: str = "test") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pack: dict[str, Any] = {"description": description}
    if isinstance(pack_format, int) and pack_format < 82:
        pack["pack_format"] = pack_format
    else:
        value = pack_format if isinstance(pack_format, list) else [pack_format, 0]
        pack["min_format"] = value
        pack["max_format"] = value
    (root / "pack.mcmeta").write_text(json.dumps({"pack": pack}, indent=2) + "\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    return root


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
