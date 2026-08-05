# Changelog

## [0.4.3] - 2026-08-05

### Added

- Plugins can ship a full Markdown documentation page (`readme` in `PLUGIN` metadata / the JSON plugin wrapper; the scaffold template includes one). The TUI plugin detail page renders it like a VS Code extension page. Built-in plugins get an auto-generated detail page from their catalog metadata.
- TUI: the plugin manager list items are now buttons — each target version is one full-width toggle row, and each plugin is a two-line row (name, version, origin, enable status dot, short description). Clicking a plugin row opens the detail page with enable/disable and (for file plugins) uninstall actions.

### Fixed

- TUI: the version sections lost their content-sized height during the list-item rework, which made the sections overlap and the expand rows unclickable; they are content-sized again.
- TUI: removing the double refresh on screen mount/resume, which raced two concurrent list rebuilds and could leave freshly mounted sections without children.
- CLI: `dpcompat plugin list --json` no longer embeds the full readme text in the machine-readable listing.

## [0.4.2] - 2026-08-05

### Added

- Plugins must declare the Minecraft release they migrate towards (`target_version` in `PLUGIN` metadata / the JSON plugin wrapper); the TUI plugin manager is now a collapsible version list (one section per version, showing the pack format and enabled/total plugin counts), so new releases and the plugins written for them appear in both the target checkboxes and the plugin list automatically. Bare declarative JSON specs derive `target_version` from their boundary pack format.
- TUI: every migration-policy checkbox gained an inline description (模拟/有损/未知/警告即失败) and the build log has a placeholder line before the first build.
- CLI: `dpcompat plugin list` shows the target version of each plugin.

### Fixed

- TUI: target/policy columns no longer stretch to fill the scroll area, which previously left a large blank gap between the checkboxes and the buttons; checkboxes render one row tall in the form.
- TUI: button rows use margin spacing instead of squeezing the buttons to two rows, so button labels are vertically centered again.
- TUI: `scroll_visible` is used to reach the build button in tests instead of scrolling the whole form to the end.

## [0.4.1] - 2026-08-05

### Added

- TUI: top navigation bar with plugin management and quit buttons, file-tree browsing for the pack and output folders, optional validated output subfolder creation, two-column target and policy layouts, policy explanations, and colored buttons.
- TUI: "create plugin template" flow that picks a location from the file tree, optionally creates a same-named subfolder, and scaffolds a ready-to-install plugin project (`plugins.py` helper: `scaffold_plugin_template`).

### Fixed

- TUI: static widgets in horizontal rows are given explicit widths so browse buttons and plugin badges are no longer pushed off-screen.
- TUI: plugin cards use content-sized heights instead of the container `1fr` default, which previously squeezed every card into empty-looking boxes.
- Plugin store now lives next to the installed package (`site-packages/dpcompat/plugins`), so plugins installed in one Python environment never affect another dpcompat; `DPCOMPAT_PLUGIN_DIR` overrides it. The directory is excluded from wheel/sdist builds.

## [0.4.0] - 2026-08-05

### Added

- Textual TUI (`dpcompat tui`): pick a data-pack directory or ZIP, choose target releases with checkboxes, adjust the fail-closed policy, run the migration, and browse the build log and report.
- Installable rule plugins: Python (`.py`) and declarative JSON plugin files install into a user plugin directory (`DPCOMPAT_PLUGIN_DIR` or `~/.dpcompat/plugins`) from the CLI (`dpcompat plugin install/remove/enable/disable/list`) or the TUI file picker.
- Built-in migration rules are grouped into thirteen named built-in plugins with Chinese name/description metadata; every plugin (built-in or installed) can be enabled or disabled persistently, and disabled plugins contribute no rules to builds.
- `docs/PLUGIN_DEVELOPMENT.zh-CN.md` documents the plugin file formats, metadata contract, safety requirements, and CLI/TUI workflows.
- `docs/ADDING_A_NEW_VERSION.zh-CN.md` documents the four-layer process for registering new releases, feature minimums, and migration rules.
- Pyright joins the dev dependency group and the `typecheck`/`check` gates.

### Changed

- The effective rule registry now reflects plugin enable state in `rules`, `plan`, `build`, and `validate`.
- `RuleRegistry.load_module_file()` loads Python rule modules from plugin file paths; `create_rule_registry()` accepts an `enabled_rule_ids` filter.

### Fixed

- Pyright diagnostics in `tests/test_migrations.py` (optional narrowing) and pre-existing issues in `migrations/text.py` and `rules/registry.py`.

### Validation boundary

- The real `phainon_v1.4` bundle under `test_datapack/` is used for local static validation and is excluded from version control.
- Vanilla server and gameplay validation remain explicit release follow-ups.

## [0.3.1] - 2026-08-02

### Added

- Direct selection of a data pack inside a ZIP/folder bundle through automatic root detection or the validated `--pack-root`/`[build].pack_root` override.
- Safe migration of quoted scalar macros whose surrounding JSON/SNBT structure is statically known.
- Recursive 1.21.5 text-component migration for entity `CustomName`, `text_display.text`, and nested `Passengers`.
- Real-pack validation evidence is recorded outside the repository because it references user-provided bundles.

### Fixed

- Object-valued JSON `type` fields no longer crash sprite-component scanning.
- Documentation files below `data/` with non-resource paths produce a warning instead of a false runtime-resource error; invalid runtime JSON/function/NBT paths still fail.
- Exact source diagnostics are deduplicated after existing overlays are flattened.
- Bundles containing one data pack and one or more resource packs no longer fail with an ambiguous `pack.mcmeta` error.

### Validation boundary

- All ten registered targets build from the supplied bundle and every generated JSON document passes strict parsing.
- This release has not run the generated packs in matching vanilla server JARs or executed author-defined behavior tests.
- Resource-pack migration remains outside DPCompat's current scope.

## [0.3.0] - 2026-08-01

### Added

- Pydantic v2 models for configuration, manifests, rules, diagnostics, build results, fallbacks, and server checks.
- Rich progress, tables, diagnostics, and queued rotating per-module logs.
- Extensible rule registry for project modules, installed `dpcompat.rules` entry points, and strict declarative JSON rules.
- Source-enforced built-in rule registration and `dpcompat rules` inspection.
- Source-backed gamerule and conservative world-border rules for format 94.1.
- Ruff, strict mypy, pytest/coverage, Makefile workflows, and Python 3.12/3.13 CI.
- Official change audit, production-pack validation procedure, and a C00–C48 reconstruction guide with commit messages.

### Changed

- The migration engine now receives an explicit ordered registry instead of owning a closed global list.
- Unknown configuration keys and declarative conflicts fail before writing artifacts.
- Chain renaming is restricted to exact JSON scalar values and complete command atoms; JSON keys are never renamed.
- JSON CLI modes suppress Rich progress and module logs on stdout.

### Validation boundary

- Repository fixtures validate converter behavior but are not presented as a real third-party data-pack port.
- Timed world-border conversions remain `unknown` because real-time and game-tick progression are not behaviorally equivalent.

## [0.2.1] - 2026-07-20

### Added

- A 43-checkpoint, file-by-file reconstruction guide for rebuilding the project from an empty uv repository.
- A contributor commenting standard and source-level module/public API documentation.
- Regression tests that require module and public API docstrings and preserve every reconstruction checkpoint.
- Deterministic archive tests that verify byte-for-byte reproduction and conventional Unix output permissions.

### Changed

- The earlier development guide is now explicitly labeled as an architectural overview and links to the detailed reconstruction path.

## [0.2.0] - 2026-07-20

### Added

- Direction-aware migration framework and compatibility policy.
- Reviewable release/feature manifests for stable 1.21.4 through 26.2.
- SNBT and binary NBT parsers; structure entity migration.
- Text component, entity equipment, item tooltip, command rotation, loot, clock and selected recipe rules.
- Source overlay flattening, review-manifest target fallbacks and guarded universal overlays.
- Planning, JSON reports, SHA-256 and local vanilla server load checks with explicit EULA consent.
- Expanded tests and open-source review documentation.

### Changed

- Unknown or unsafe semantics now fail by default instead of producing warning-only archives.
- Strict JSON handling rejects duplicate object keys.

### Known limitations

- No complete Brigadier parser or complete per-version registry snapshot.
- General Environment Attributes/worldgen downgrade remains intentionally unsupported.
- Item component syntax embedded in arbitrary commands is only detected, not fully rewritten.

## [0.1.0]

- Initial proof of concept with version detection, strict JSON, chain rename and overlay packaging.
