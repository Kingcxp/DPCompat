"""Parser and serializer for the SNBT subset required by migration rules.

The syntax tree retains numeric suffixes, typed arrays, calls, and compound ordering.  Parsing
errors are explicit because falling back to string replacement would corrupt nested command
payloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


class SnbtError(ValueError):
    """Raised when SNBT cannot be parsed without ambiguity."""


@dataclass(frozen=True, slots=True)
class SnbtNumber:
    """Numeric token that retains its exact spelling and type suffix."""

    raw: str

    @property
    def suffix(self) -> str:
        match = re.search(r"([a-zA-Z]+)$", self.raw)
        return match.group(1) if match else ""

    def as_int(self) -> int | None:
        text = self.raw
        suffix = self.suffix
        if suffix:
            text = text[: -len(suffix)]
        text = text.replace("_", "")
        try:
            return int(text, 0)
        except ValueError:
            try:
                value = float(text)
            except ValueError:
                return None
            return int(value) if value.is_integer() else None


@dataclass(frozen=True, slots=True)
class SnbtArray:
    """Typed ``[B;]``, ``[I;]``, or ``[L;]`` SNBT array."""

    kind: str
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class SnbtCall:
    """Function-like value used by component-oriented SNBT syntax."""

    name: str
    argument: Any


_BARE_SAFE_RE = re.compile(r"^[A-Za-z0-9_+\-.]+$")
_NUMBER_RE = re.compile(
    r"^[+-]?(?:(?:0[xX][0-9a-fA-F_]+)|(?:0[bB][01_]+)|"
    r"(?:(?:\d[\d_]*)?(?:\.[\d_]*)?(?:[eE][+-]?[\d_]+)?))"
    r"(?:[sSuU]?[bBsSiIlL]|[fFdD])?$"
)


class Parser:
    """Single-pass recursive-descent SNBT parser with position-aware failures."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse(self) -> Any:
        self._skip_ws()
        value = self._parse_value()
        self._skip_ws()
        if self.index != len(self.text):
            raise self._error("Unexpected trailing content")
        return value

    def _error(self, message: str) -> SnbtError:
        return SnbtError(f"{message} at character {self.index + 1}")

    def _peek(self) -> str:
        return self.text[self.index] if self.index < len(self.text) else ""

    def _take(self) -> str:
        char = self._peek()
        if not char:
            raise self._error("Unexpected end of input")
        self.index += 1
        return char

    def _skip_ws(self) -> None:
        while self._peek().isspace():
            self.index += 1

    def _parse_value(self) -> Any:
        self._skip_ws()
        char = self._peek()
        if char == "{":
            return self._parse_compound()
        if char == "[":
            return self._parse_list_or_array()
        if char in {'"', "'"}:
            return self._parse_quoted()

        token = self._parse_bare()
        self._skip_ws()
        if self._peek() == "(" and token in {"bool", "uuid"}:
            self._take()
            argument = self._parse_value()
            self._skip_ws()
            if self._take() != ")":
                raise self._error("Expected ')' after SNBT call")
            return SnbtCall(token, argument)
        lowered = token.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if _NUMBER_RE.fullmatch(token) and any(char.isdigit() for char in token):
            return SnbtNumber(token)
        return token

    def _parse_compound(self) -> dict[str, Any]:
        self._take()
        result: dict[str, Any] = {}
        self._skip_ws()
        if self._peek() == "}":
            self._take()
            return result
        while True:
            self._skip_ws()
            key = self._parse_key()
            if key in result:
                raise self._error(f"Duplicate compound key {key!r}")
            self._skip_ws()
            if self._take() != ":":
                raise self._error("Expected ':' after compound key")
            result[key] = self._parse_value()
            self._skip_ws()
            char = self._take()
            if char == "}":
                return result
            if char != ",":
                raise self._error("Expected ',' or '}'")
            self._skip_ws()
            if self._peek() == "}":
                self._take()
                return result

    def _parse_key(self) -> str:
        if self._peek() in {'"', "'"}:
            value = self._parse_quoted()
            if not isinstance(value, str):
                raise self._error("Compound key must be a string")
            return value
        return self._parse_bare(stoppers=":")

    def _parse_list_or_array(self) -> list[Any] | SnbtArray:
        self._take()
        self._skip_ws()
        kind = ""
        if self._peek().upper() in {"B", "I", "L"}:
            saved = self.index
            candidate = self._take().upper()
            self._skip_ws()
            if self._peek() == ";":
                self._take()
                kind = candidate
            else:
                self.index = saved

        values: list[Any] = []
        self._skip_ws()
        if self._peek() == "]":
            self._take()
            return SnbtArray(kind, tuple()) if kind else values
        while True:
            values.append(self._parse_value())
            self._skip_ws()
            char = self._take()
            if char == "]":
                return SnbtArray(kind, tuple(values)) if kind else values
            if char != ",":
                raise self._error("Expected ',' or ']' in list")
            self._skip_ws()
            if self._peek() == "]":
                self._take()
                return SnbtArray(kind, tuple(values)) if kind else values

    def _parse_quoted(self) -> str:
        quote = self._take()
        output: list[str] = []
        while True:
            char = self._take()
            if char == quote:
                return "".join(output)
            if char != "\\":
                output.append(char)
                continue
            escape = self._take()
            simple = {
                "b": "\b",
                "s": " ",
                "t": "\t",
                "n": "\n",
                "f": "\f",
                "r": "\r",
                "\\": "\\",
                "'": "'",
                '"': '"',
            }
            if escape in simple:
                output.append(simple[escape])
                continue
            if escape in {"x", "u", "U"}:
                lengths = {"x": 2, "u": 4, "U": 8}
                count = lengths[escape]
                raw = self.text[self.index : self.index + count]
                if len(raw) != count or not all(char in "0123456789abcdefABCDEF" for char in raw):
                    raise self._error(f"Invalid \\{escape} escape")
                self.index += count
                output.append(chr(int(raw, 16)))
                continue
            # Named Unicode escapes are intentionally kept unsupported instead of guessed.
            raise self._error(f"Unsupported escape \\{escape}")

    def _parse_bare(self, *, stoppers: str = ",]}):") -> str:
        start = self.index
        while self.index < len(self.text):
            char = self.text[self.index]
            if char.isspace() or char in stoppers:
                break
            self.index += 1
        if self.index == start:
            raise self._error("Expected value")
        return self.text[start : self.index]


def loads(text: str) -> Any:
    """Parse one complete SNBT value."""

    return Parser(text).parse()


def _dump_string(value: str) -> str:
    if value and _BARE_SAFE_RE.fullmatch(value) and value.lower() not in {"true", "false"}:
        return value
    return json.dumps(value, ensure_ascii=False)


def dumps(value: Any) -> str:
    """Serialize a supported typed value to deterministic SNBT."""

    if isinstance(value, dict):
        return "{" + ",".join(f"{_dump_string(str(key))}:{dumps(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, SnbtArray):
        return f"[{value.kind};" + ",".join(dumps(item) for item in value.values) + "]"
    if isinstance(value, SnbtCall):
        return f"{value.name}({dumps(value.argument)})"
    if isinstance(value, SnbtNumber):
        return value.raw
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _dump_string(value)
    if isinstance(value, int | float):
        return str(value)
    raise TypeError(f"Unsupported SNBT value: {type(value).__name__}")


def from_json(value: Any) -> Any:
    """Convert JSON-compatible values into the neutral SNBT value model."""

    if isinstance(value, dict):
        return {str(key): from_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_json(item) for item in value]
    if isinstance(value, bool | str) or value is None:
        return value
    if isinstance(value, int | float):
        return SnbtNumber(str(value))
    raise TypeError(type(value).__name__)


def to_json_compatible(value: Any) -> Any:
    """Drop SNBT-only wrappers when a migration needs JSON output."""

    if isinstance(value, dict):
        return {key: to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_compatible(item) for item in value]
    if isinstance(value, SnbtNumber):
        raw = value.raw
        suffix = value.suffix
        if suffix:
            raw = raw[: -len(suffix)]
        raw = raw.replace("_", "")
        try:
            return int(raw, 0)
        except ValueError:
            return float(raw)
    if isinstance(value, SnbtArray):
        return [to_json_compatible(item) for item in value.values]
    if isinstance(value, SnbtCall):
        raise SnbtError(f"SNBT call {value.name} cannot be represented in legacy JSON")
    return value
