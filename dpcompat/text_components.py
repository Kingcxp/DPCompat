"""Upgrade and downgrade the reviewed subset of text-component event objects.

The transformation is recursive but only entered after a caller has established text-component
context.  Action-specific fields are validated so a value is never moved under the wrong key.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from . import snbt


class TextComponentMigrationError(ValueError):
    """Raised when an event object cannot be mapped without changing meaning."""


def _maybe_unwrap_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def upgrade_component(value: Any) -> Any:
    """Upgrade legacy event keys in one established text-component value."""

    value = deepcopy(_maybe_unwrap_json_string(value))
    return _upgrade_node(value)


def _upgrade_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_upgrade_node(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = {key: _upgrade_node(item) for key, item in value.items()}
    if "hoverEvent" in result:
        if "hover_event" in result:
            raise TextComponentMigrationError("Both hoverEvent and hover_event are present")
        result["hover_event"] = result.pop("hoverEvent")
    if "clickEvent" in result:
        if "click_event" in result:
            raise TextComponentMigrationError("Both clickEvent and click_event are present")
        result["click_event"] = result.pop("clickEvent")

    hover = result.get("hover_event")
    if isinstance(hover, dict):
        action = hover.get("action")
        if "value" in hover and "contents" not in hover:
            if action == "show_text":
                hover["contents"] = hover.pop("value")
            else:
                raise TextComponentMigrationError(
                    f"Legacy hover value cannot be safely converted for action {action!r}"
                )
        if action == "show_text" and "contents" in hover:
            hover["value"] = _upgrade_node(hover.pop("contents"))
        elif action == "show_item" and "contents" in hover:
            contents = hover.pop("contents")
            if isinstance(contents, str):
                hover["id"] = contents
            elif isinstance(contents, dict):
                for key, item in contents.items():
                    if key in hover:
                        raise TextComponentMigrationError(f"Duplicate show_item field while inlining contents: {key}")
                    hover[key] = item
            else:
                raise TextComponentMigrationError("show_item contents must be an item id or object")
        elif action == "show_entity" and "contents" in hover:
            contents = hover.pop("contents")
            if not isinstance(contents, dict):
                raise TextComponentMigrationError("show_entity contents must be an object")
            for key, item in contents.items():
                new_key = {"id": "uuid", "type": "id"}.get(key, key)
                if new_key in hover:
                    raise TextComponentMigrationError(f"Duplicate show_entity field while inlining contents: {new_key}")
                hover[new_key] = item

    click = result.get("click_event")
    if isinstance(click, dict) and "value" in click:
        action = click.get("action")
        target = (
            {
                "open_url": "url",
                "run_command": "command",
                "suggest_command": "command",
                "change_page": "page",
            }.get(action)
            if isinstance(action, str)
            else None
        )
        if target:
            value = click.pop("value")
            if action == "change_page" and isinstance(value, str) and value.isdigit():
                value = snbt.SnbtNumber(value)
            click[target] = value
    return result


def downgrade_component(value: Any) -> Any:
    """Downgrade modern event keys when every action has a legacy equivalent."""

    return _downgrade_node(deepcopy(value))


def _downgrade_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_downgrade_node(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = {key: _downgrade_node(item) for key, item in value.items()}
    if "hover_event" in result:
        if "hoverEvent" in result:
            raise TextComponentMigrationError("Both hover_event and hoverEvent are present")
        result["hoverEvent"] = result.pop("hover_event")
    if "click_event" in result:
        if "clickEvent" in result:
            raise TextComponentMigrationError("Both click_event and clickEvent are present")
        result["clickEvent"] = result.pop("click_event")

    hover = result.get("hoverEvent")
    if isinstance(hover, dict):
        action = hover.get("action")
        if action == "show_text" and "value" in hover:
            hover["contents"] = hover.pop("value")
        elif action == "show_item" and "id" in hover:
            item_contents: dict[str, Any] = {}
            for key in ("id", "count", "components", "tag"):
                if key in hover:
                    item_contents[key] = hover.pop(key)
            hover["contents"] = item_contents
        elif action == "show_entity" and ("uuid" in hover or "id" in hover):
            entity_contents: dict[str, Any] = {}
            if "uuid" in hover:
                entity_contents["id"] = hover.pop("uuid")
            if "id" in hover:
                entity_contents["type"] = hover.pop("id")
            if "name" in hover:
                entity_contents["name"] = hover.pop("name")
            hover["contents"] = entity_contents

    click = result.get("clickEvent")
    if isinstance(click, dict):
        action = click.get("action")
        source = (
            {
                "open_url": "url",
                "run_command": "command",
                "suggest_command": "command",
                "change_page": "page",
            }.get(action)
            if isinstance(action, str)
            else None
        )
        if source and source in click:
            value = click.pop(source)
            if action == "change_page":
                if isinstance(value, snbt.SnbtNumber):
                    value = value.raw
                elif isinstance(value, int):
                    value = str(value)
            click["value"] = value
    return result
