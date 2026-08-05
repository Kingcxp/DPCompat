# Version and migration matrix

| Boundary | Automatic, scoped transformations | Diagnostic-only / author fallback required |
| --- | --- | --- |
| 61 → 71 (1.21.5) | Text click/hover event shapes; inline `CustomName`/`text_display.text` through nested passengers; quoted scalar macro preservation; selected entity equipment/drop chances/fields in known summon/data/structure contexts; tooltip consolidation; `horse.saddle` slot | Complete item-component command grammar; all entity/block-entity DFU changes; structure-generating/unquoted runtime macros; every recipe change |
| 71 → 80 (1.21.6) | Strict JSON normalization and duplicate-key rejection | Dialog/waypoint backport; custom click-event emulation; complete command-tree diff |
| 80/81 → 88.0 (1.21.9) | Exact `minecraft:chain` scalar/command atom ↔ `minecraft:iron_chain`; spawn yaw/pitch defaults; metadata model | JSON object keys/custom storage named `chain`; sprite backport; fetchprofile; non-zero pitch downgrade |
| 88.0 → 94.1 (1.21.11) | Literal gamerule registry names, including documented special and inverted names; filtered `modifier` ↔ `on_pass` when no `on_fail`; syntax unit conversion for worldborder | Gamerule macros/queries/removed fire split; any timed worldborder semantic equivalence; `on_fail`; general Environment Attributes/worldgen |
| 94.1 → 101.1 (26.1) | Overworld defaults for timeline/test environment/time_check; selected recipe defaults/result forms | Custom world clocks/time markers; trade registries; new recipe types/configurable special recipes; behavior-changing recipe fields |
| 101.1 → 107.1 (26.2) | Metadata/pass-through validation | `sulfur_cube_archetype` and content absent from older registries |

“Automatic” means scoped code and regression tests exist, not that every data pack for that release can be converted. `worldborder` is intentionally classified `unknown` even after seconds↔ticks numeric rewriting because 1.21.11 changed progression from real time to game ticks; matching the numeral does not prove matching behavior under pause or lag.
