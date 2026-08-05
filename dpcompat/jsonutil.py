"""Strict and legacy-tolerant JSON helpers used by data-pack resources.

Legacy input may contain comments or trailing commas, but duplicate object keys are always
rejected because normal JSON parsers would silently discard one value.  Every emitted file is
canonical strict JSON so targets at and after format 80 load deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonNormalizationError(ValueError):
    """Base error for JSON syntax that cannot be normalized safely."""


class DuplicateJsonKeyError(JsonNormalizationError):
    """Raised when an object repeats a key and the intended value is ambiguous."""


def _strip_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                if text[index] in "\r\n":
                    output.append(text[index])
                index += 1
            if index + 1 >= len(text):
                raise JsonNormalizationError("Unterminated block comment")
            index += 2
            continue

        output.append(char)
        index += 1
    return "".join(output)


def _remove_trailing_commas(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue

        output.append(char)
        index += 1
    return "".join(output)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def loads_lenient(text: str, *, source: str = "<string>") -> Any:
    """Parse comments and trailing commas while still rejecting duplicate keys."""

    cleaned = _remove_trailing_commas(_strip_comments(text.lstrip("\ufeff")))
    return _loads(cleaned, source=source)


def loads_strict(text: str, *, source: str = "<string>") -> Any:
    """Parse standards-compliant JSON with duplicate-key detection."""

    return _loads(text.lstrip("\ufeff"), source=source)


def _loads(text: str, *, source: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_object_without_duplicates)
    except DuplicateJsonKeyError:
        raise
    except json.JSONDecodeError as exc:
        raise JsonNormalizationError(f"{source}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc


def load_path(path: Path, *, strict: bool = False) -> Any:
    """Read one UTF-8 JSON file using strict or legacy-tolerant syntax."""

    text = path.read_text(encoding="utf-8")
    loader = loads_strict if strict else loads_lenient
    return loader(text, source=str(path))


def is_strict_json(text: str, *, source: str = "<string>") -> bool:
    """Return whether ``text`` parses under the strict loader."""

    try:
        loads_strict(text, source=source)
    except JsonNormalizationError:
        return False
    return True


def dump_path(path: Path, value: Any) -> None:
    """Write canonical UTF-8 JSON with deterministic indentation."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )
