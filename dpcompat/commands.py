"""Conservative tokenization helpers for ``.mcfunction`` command lines.

This module is intentionally smaller than a Brigadier parser.  It only promises to split
on top-level whitespace while preserving nested JSON, SNBT, component expressions, quotes,
and original character offsets.  Migration rules must refuse commands outside the selected
grammars instead of treating these tokens as a complete semantic parse.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandToken:
    """One top-level command token with offsets into the original line."""

    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ParsedCommandLine:
    """Tokenized line that can be rewritten without reformatting untouched text."""

    original: str
    tokens: tuple[CommandToken, ...]
    macro: bool = False

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(token.value for token in self.tokens)

    def replace_spans(self, replacements: list[tuple[int, int, str]]) -> str:
        """Apply non-overlapping replacements using original character offsets."""
        result = self.original
        for start, end, replacement in sorted(replacements, reverse=True):
            result = result[:start] + replacement + result[end:]
        return result


_OPEN_TO_CLOSE = {"[": "]", "{": "}", "(": ")"}
_CLOSE = set(_OPEN_TO_CLOSE.values())


def parse_command_line(line: str) -> ParsedCommandLine:
    """Split a mcfunction line at top-level whitespace.

    This is deliberately not a Brigadier parser. It preserves quoted strings and nested
    SNBT/JSON/component expressions as one token, which is enough for conservative command
    recognition and a small number of syntax-preserving migrations.
    """

    offset = 0
    macro = False
    if line.startswith("$"):
        macro = True
        offset = 1

    tokens: list[CommandToken] = []
    index = offset
    start: int | None = None
    stack: list[str] = []
    quote: str | None = None
    escaped = False

    while index < len(line):
        char = line[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char in {'"', "'"}:
            if start is None:
                start = index
            quote = char
            index += 1
            continue

        if char in _OPEN_TO_CLOSE:
            if start is None:
                start = index
            stack.append(_OPEN_TO_CLOSE[char])
            index += 1
            continue

        if char in _CLOSE:
            if stack and char == stack[-1]:
                stack.pop()
            index += 1
            continue

        if char.isspace() and not stack:
            if start is not None:
                tokens.append(CommandToken(line[start:index], start, index))
                start = None
            index += 1
            continue

        if start is None:
            start = index
        index += 1

    if start is not None:
        tokens.append(CommandToken(line[start : len(line)], start, len(line)))

    return ParsedCommandLine(line, tuple(tokens), macro)


def iter_execute_segments(parsed: ParsedCommandLine) -> tuple[tuple[CommandToken, ...], ...]:
    """Return the outer command and recursively nested commands after execute ... run."""

    segments: list[tuple[CommandToken, ...]] = []
    current = parsed.tokens
    while current:
        segments.append(current)
        if current[0].value != "execute":
            break
        run_index = next((index for index, token in enumerate(current[1:], start=1) if token.value == "run"), None)
        if run_index is None or run_index + 1 >= len(current):
            break
        current = current[run_index + 1 :]
    return tuple(segments)


def looks_like_coordinate(value: str) -> bool:
    """Recognize absolute, relative, local, and macro-expanded coordinate tokens."""

    if value.startswith(("~", "^", "$(")):
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def is_zero_rotation(value: str) -> bool:
    """Return whether a numeric rotation token is exactly zero."""

    try:
        return float(value.rstrip("fFdD")) == 0.0
    except ValueError:
        return False


def macro_placeholders_are_quoted(value: str) -> bool:
    """Return whether every ``$(...)`` placeholder occurs inside a quoted scalar.

    A quoted placeholder can change only a string payload after Minecraft expands the macro.
    An unquoted placeholder may generate keys, compounds, lists, or complete command arguments,
    so a static migration cannot rely on the template's parsed structure.
    """

    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif value.startswith("$(", index):
            return False
        index += 1
    return True
