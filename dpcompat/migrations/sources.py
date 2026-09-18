"""Primary Mojang changelog sources attached to every built-in migration rule."""

from __future__ import annotations

_MC = "https://www.minecraft.net/en-us/article/minecraft-java-edition-"

BUILTIN_RULE_SOURCES: dict[str, tuple[str, ...]] = {
    "text-component.events-and-inline-snbt@71": (_MC + "1-21-5",),
    "item-components.tooltip-display-and-simplification@71": (_MC + "1-21-5",),
    "entity-nbt-equipment-and-fields@71": (_MC + "1-21-5",),
    "structure.entity-nbt@71": (_MC + "1-21-5",),
    "command.slot-horse-saddle-to-saddle@71": (_MC + "1-21-5",),
    "json.strict-normalization@80": (_MC + "1-21-6",),
    "identifier.chain-to-iron-chain@88": (_MC + "1-21-9",),
    "command.spawn-rotation-pitch@88": (_MC + "1-21-9",),
    "command.gamerule-registry-names@94.1": (_MC + "1-21-11",),
    "command.worldborder-tick-time@94.1": (_MC + "1-21-11",),
    "loot.filtered-on-pass-on-fail@94.1": (_MC + "1-21-11",),
    "timeline.clock-and-time-markers@101.1": (_MC + "26-1",),
    "predicate.time-check-clock@101.1": (_MC + "26-1",),
    "recipe.syntax-and-types@101.1": (_MC + "26-1",),
    "test-environment.time-of-day-to-clock-time@101.1": (_MC + "26-1",),
    "item-components.swing-animation-split@121.0": (_MC + "26-3",),
    "item-components.map-color-removed@121.0": (_MC + "26-3",),
    "item-components.pot-decorations-faces@121.0": (_MC + "26-3",),
    "environment-attributes.bed-rule-fields@121.0": (_MC + "26-3",),
    "registry.trim-material-palette-id@121.0": (_MC + "26-3",),
    "loot.function-condition-and-pool-keys@121.0": (_MC + "26-3",),
    "worldgen.registry-and-config@121.0": (_MC + "26-3",),
}
