"""Migrate the game-rule registry naming boundary introduced at format 94.1."""

from __future__ import annotations

import re

from ..commands import iter_execute_segments, parse_command_line
from ..models import Compatibility, Diagnostic, MigrationRecord, PackFormat
from .base import MigrationContext, RuleResult, crosses
from .common import policy_diagnostic

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

_SPECIAL_UPGRADE = {
    "announceAdvancements": "minecraft:show_advancement_messages",
    "commandBlocksEnabled": "minecraft:command_blocks_work",
    "command_modification_block_limit": "minecraft:max_block_modifications",
    "disableElytraMovementCheck": "minecraft:elytra_movement_check",
    "disablePlayerMovementCheck": "minecraft:player_movement_check",
    "disableRaids": "minecraft:raids",
    "doDaylightCycle": "minecraft:advance_time",
    "doEntityDrops": "minecraft:entity_drops",
    "doImmediateRespawn": "minecraft:immediate_respawn",
    "doInsomnia": "minecraft:spawn_phantoms",
    "doLimitedCrafting": "minecraft:limited_crafting",
    "doMobLoot": "minecraft:mob_drops",
    "doMobSpawning": "minecraft:spawn_mobs",
    "doPatrolSpawning": "minecraft:spawn_patrols",
    "doTileDrops": "minecraft:block_drops",
    "doTraderSpawning": "minecraft:spawn_wandering_traders",
    "doVinesSpread": "minecraft:spread_vines",
    "doWardenSpawning": "minecraft:spawn_wardens",
    "doWeatherCycle": "minecraft:advance_weather",
    "maxCommandChainLength": "minecraft:max_command_sequence_length",
    "maxCommandForkCount": "minecraft:max_command_forks",
    "naturalRegeneration": "minecraft:natural_health_regeneration",
    "snowAccumulationHeight": "minecraft:max_snow_accumulation_height",
    "spawnRadius": "minecraft:respawn_radius",
    "spawnerBlocksEnabled": "minecraft:spawner_blocks_work",
}
_SPECIAL_DOWNGRADE = {new: old for old, new in _SPECIAL_UPGRADE.items()}
_INVERTED = {
    "disableElytraMovementCheck": "minecraft:elytra_movement_check",
    "disablePlayerMovementCheck": "minecraft:player_movement_check",
    "disableRaids": "minecraft:raids",
}
_INVERTED_REVERSE = {new: old for old, new in _INVERTED.items()}
_REMOVED = {"doFireTick", "allowFireTicksAwayFromPlayer"}
# The 1.21.11 fire rules have no legacy game rule at all.  Downgrading the new
# namespaced rule through the generic camelCase conversion would produce a rule
# name that does not exist on old versions, so it must be blocked like the
# upgrade-side doFireTick removal.
_REMOVED_NEW = {"minecraft:fire_spread_radius_around_player"}


def _camel_to_namespaced(value: str) -> str:
    special = _SPECIAL_UPGRADE.get(value)
    if special is not None:
        return special
    return "minecraft:" + _CAMEL_BOUNDARY.sub("_", value).lower()


def _snake_to_camel(value: str) -> str | None:
    special = _SPECIAL_DOWNGRADE.get(value)
    if special is not None:
        return special
    if not value.startswith("minecraft:"):
        return None
    parts = value.removeprefix("minecraft:").split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


class GameRuleRegistryRule:
    """Rename game rules and invert only explicit boolean assignments that require it."""

    id = "command.gamerule-registry-names@94.1"
    boundary = PackFormat(94, 1)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        upgrading = context.source < self.boundary <= context.target
        changed_files = 0
        changed_nodes = 0
        diagnostics: list[Diagnostic] = []
        for path in sorted((context.root / "data").rglob("*.mcfunction")):
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            output: list[str] = []
            local_changed = 0
            for line_number, line in enumerate(lines, start=1):
                body = line.rstrip("\r\n")
                suffix = line[len(body) :]
                if not body.strip() or body.lstrip().startswith("#"):
                    output.append(line)
                    continue
                parsed = parse_command_line(body)
                replacements: list[tuple[int, int, str]] = []
                for segment in iter_execute_segments(parsed):
                    values = tuple(token.value for token in segment)
                    if len(values) < 2 or values[0] != "gamerule":
                        continue
                    if parsed.macro or any("$(" in value for value in values):
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="macro-gamerule-cannot-migrate",
                                message="A macro-generated gamerule command cannot be statically migrated",
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                            )
                        )
                        continue
                    name = values[1]
                    if upgrading and name in _REMOVED:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="fire-gamerules-replaced",
                                message=(
                                    "doFireTick and allowFireTicksAwayFromPlayer were replaced by "
                                    "minecraft:fire_spread_radius_around_player"
                                ),
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                                details={"game_rule": name},
                            )
                        )
                        continue
                    if not upgrading and name in _REMOVED_NEW:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNSUPPORTED,
                                code="fire-gamerules-replaced",
                                message=(
                                    "minecraft:fire_spread_radius_around_player does not exist before "
                                    "1.21.11; rewrite the behavior instead of converting the name"
                                ),
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                                details={"game_rule": name},
                            )
                        )
                        continue
                    new_name = _camel_to_namespaced(name) if upgrading else _snake_to_camel(name)
                    if new_name is None:
                        diagnostics.append(
                            policy_diagnostic(
                                context,
                                compatibility=Compatibility.UNKNOWN,
                                code="custom-gamerule-cannot-downgrade",
                                message="A non-Minecraft namespaced game rule has no vanilla legacy name",
                                path=context.relative(path),
                                line=line_number,
                                rule_id=self.id,
                                details={"game_rule": name},
                            )
                        )
                        continue
                    replacements.append((segment[1].start, segment[1].end, new_name))
                    local_changed += 1

                    inverted = name in _INVERTED if upgrading else name in _INVERTED_REVERSE
                    if inverted:
                        if len(values) == 3 and values[2] in {"true", "false"}:
                            replacement = "false" if values[2] == "true" else "true"
                            replacements.append((segment[2].start, segment[2].end, replacement))
                            local_changed += 1
                        else:
                            diagnostics.append(
                                policy_diagnostic(
                                    context,
                                    compatibility=Compatibility.UNSUPPORTED,
                                    code="inverted-gamerule-query-cannot-migrate",
                                    message=(
                                        "A queried inverted game rule changes its returned boolean; "
                                        "only explicit true/false assignments are automatically migrated"
                                    ),
                                    path=context.relative(path),
                                    line=line_number,
                                    rule_id=self.id,
                                    details={"game_rule": name},
                                )
                            )
                output.append(parsed.replace_spans(replacements) + suffix)
            if local_changed:
                path.write_text("".join(output), encoding="utf-8")
                changed_files += 1
                changed_nodes += local_changed
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, changed_files, changed_nodes), diagnostics)
