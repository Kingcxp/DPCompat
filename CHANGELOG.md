# Changelog

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
