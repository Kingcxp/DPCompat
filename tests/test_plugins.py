"""Validation tests for the installable plugin store and effective registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dpcompat import plugins as plugins_module
from dpcompat.config import ProjectConfig
from dpcompat.migrations import BUILTIN_RULES
from dpcompat.models import BuildPolicy, PackFormat
from dpcompat.plugins import (
    BUILTIN_PLUGINS,
    PluginStore,
    create_effective_registry,
    default_plugin_dir,
    scaffold_plugin_template,
)
from dpcompat.rules import create_rule_registry

_PYTHON_PLUGIN = '''
"""Demo plugin used only by the test suite."""

from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat

PLUGIN = {
    "id": "demo.python@88",
    "name": "演示 Python 插件",
    "description": "把 demo:old 重命名为 demo:new 的示例规则。",
    "version": "1.0.0",
    "target_version": "1.21.9",
    "readme": "## 文档\\n\\n这是演示插件的说明。",
    "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"],
}


class DemoRenameRule:
    id = "demo.rename@88"
    boundary = PackFormat(88)
    priority = 450

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (DemoRenameRule(),)
'''

_JSON_PLUGIN = {
    "schema": 1,
    "plugin": {
        "id": "demo.json@88",
        "name": "演示 JSON 插件",
        "description": "声明式重命名 demo:old -> demo:new。",
        "version": "1.0.0",
        "target_version": "1.21.9",
    },
    "rules": [
        {
            "schema": 1,
            "id": "demo.json-rename@88",
            "description": "精确值替换",
            "boundary": [88, 0],
            "compatibility": "lossless",
            "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"],
            "upgrade": [
                {
                    "type": "json_exact_value",
                    "include": ["data/**/*.json"],
                    "old": "demo:old",
                    "new": "demo:new",
                }
            ],
            "downgrade": [
                {
                    "type": "json_exact_value",
                    "include": ["data/**/*.json"],
                    "old": "demo:new",
                    "new": "demo:old",
                }
            ],
        }
    ],
}


@pytest.fixture()
def plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "plugins"
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(directory))
    return directory


def test_default_plugin_dir_is_scoped_to_the_installation() -> None:
    # Installed plugins live next to the dpcompat package so separate Python
    # environments never share plugin state.
    expected = Path(plugins_module.__file__).resolve().parent / "plugins"
    assert default_plugin_dir() == expected


def test_scaffold_plugin_template_creates_a_working_project(tmp_path: Path) -> None:
    # Resolve the base so both sides of the comparisons below use the canonical
    # long path form; on Windows CI the temp dir may use the 8.3 short name.
    root = tmp_path.resolve()
    created = scaffold_plugin_template("demo.template", root, subfolder=True)
    assert created == root / "demo.template" / "demo.template.py"
    assert created.is_file()
    assert (root / "demo.template" / "README.md").is_file()
    source = created.read_text(encoding="utf-8")
    assert '"id": "demo.template@88"' in source
    assert '"target_version": "1.21.9"' in source
    assert '"readme"' in source
    assert "RULES = (ExampleRule(),)" in source

    # The scaffolded file must be installable as a real plugin.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    store = PluginStore()
    info = store.install(created)
    assert info.id == "demo.template@88"
    assert info.rules == ("demo.template.example@88",)


def test_scaffold_plugin_template_validates_the_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="插件名称"):
        scaffold_plugin_template("Bad Name", tmp_path)
    with pytest.raises(ValueError, match="插件名称"):
        scaffold_plugin_template("bad-name!", tmp_path)
    with pytest.raises(ValueError, match="插件名称"):
        scaffold_plugin_template("", tmp_path)


def test_builtin_plugins_cover_every_builtin_rule_exactly_once() -> None:
    builtin = [info for info in BUILTIN_PLUGINS if info.origin == "builtin"]
    ids = [info.id for info in builtin]
    assert len(ids) == len(set(ids))
    covered: list[str] = []
    for info in builtin:
        assert info.name
        assert info.description
        assert info.readme  # every built-in ships a generated Markdown detail page
        assert info.target_version
        covered.extend(info.rules)
    assert sorted(covered) == sorted(rule.id for rule in BUILTIN_RULES)


def test_install_enable_disable_uninstall_round_trip(plugin_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "demo_plugin.py"
    source.write_text(_PYTHON_PLUGIN, encoding="utf-8")
    store = PluginStore()
    info = store.install(source)
    assert info.origin == "file"
    assert info.rules == ("demo.rename@88",)
    assert info.readme == "## 文档\n\n这是演示插件的说明。"
    assert (plugin_dir / "demo.python@88.py").is_file()

    listed = {item.id: item for item in store.list_plugins()}
    assert listed["demo.python@88"].enabled is True
    store.set_enabled("demo.python@88", False)
    assert store.list_plugins()[-1].enabled is False  # file plugins sort after built-ins
    store.set_enabled("demo.python@88", True)
    assert store.list_plugins()[-1].enabled is True

    store.uninstall("demo.python@88")
    assert not (plugin_dir / "demo.python@88.py").exists()
    assert "demo.python@88" not in {item.id for item in store.list_plugins()}


def test_install_rejects_duplicate_ids_and_rule_collisions(plugin_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "demo_plugin.py"
    source.write_text(_PYTHON_PLUGIN, encoding="utf-8")
    store = PluginStore()
    store.install(source)
    with pytest.raises(ValueError, match="already installed"):
        store.install(source)
    store.install(source, force=True)  # force replaces

    builtin_rule = tmp_path / "colliding.py"
    builtin_rule.write_text(
        _PYTHON_PLUGIN.replace('"id": "demo.python@88"', '"id": "colliding.python@88"').replace(
            "demo.rename@88", "command.gamerule-registry-names@94.1"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provided by another plugin"):
        store.install(builtin_rule)


def test_install_rejects_invalid_files(plugin_dir: Path, tmp_path: Path) -> None:
    store = PluginStore()
    notes = tmp_path / "notes.txt"
    notes.write_text("not a plugin\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.py or \.json"):
        store.install(notes)

    missing_meta = tmp_path / "missing_meta.py"
    missing_meta.write_text("RULES = ()\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PLUGIN"):
        store.install(missing_meta)


def test_install_declarative_json_plugin(plugin_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "demo.json"
    source.write_text(json.dumps(_JSON_PLUGIN), encoding="utf-8")
    store = PluginStore()
    info = store.install(source)
    assert info.kind == "declarative"
    assert info.rules == ("demo.json-rename@88",)


def test_bare_json_spec_derives_target_version_from_boundary(plugin_dir: Path, tmp_path: Path) -> None:
    # A bare DeclarativeRuleSpec has no plugin wrapper, so the target version is
    # derived from the boundary pack format (latest registered release).
    spec = _JSON_PLUGIN["rules"][0]
    source = tmp_path / "bare.json"
    source.write_text(json.dumps(spec), encoding="utf-8")
    store = PluginStore()
    info = store.install(source)
    assert info.id == spec["id"]
    assert info.target_version == "1.21.10"  # format 88 -> latest release with format 88


def test_disabled_builtin_plugin_drops_its_rules(plugin_dir: Path) -> None:
    store = PluginStore()
    store.set_enabled("gamerules@94.1", False)
    registry = create_effective_registry(ProjectConfig(), store)
    rule_ids = {item.id for item in registry.info()}
    assert "command.gamerule-registry-names@94.1" not in rule_ids
    assert "identifier.chain-to-iron-chain@88" in rule_ids


def test_enabled_python_plugin_participates_in_the_registry(plugin_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "demo_plugin.py"
    source.write_text(_PYTHON_PLUGIN, encoding="utf-8")
    store = PluginStore()
    store.install(source)
    registry = create_effective_registry(ProjectConfig(), store)
    rule_ids = {item.id for item in registry.info()}
    assert "demo.rename@88" in rule_ids


def test_enabled_declarative_plugin_applies_its_operations(plugin_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "demo.json"
    source.write_text(json.dumps(_JSON_PLUGIN), encoding="utf-8")
    store = PluginStore()
    store.install(source)

    pack = tmp_path / "pack"
    (pack / "data/demo/recipe").mkdir(parents=True)
    (pack / "data/demo/recipe/test.json").write_text('{"id": "demo:old"}\n', encoding="utf-8")

    registry = create_effective_registry(ProjectConfig(), store)
    from dpcompat.migrations.base import MigrationContext

    rule = next(rule for rule in registry.rules() if rule.id == "demo.json-rename@88")
    rule.apply(MigrationContext(pack, PackFormat(80), PackFormat(88), BuildPolicy()))
    assert '"demo:new"' in (pack / "data/demo/recipe/test.json").read_text(encoding="utf-8")


def test_unknown_plugin_operations_fail(plugin_dir: Path) -> None:
    store = PluginStore()
    with pytest.raises(ValueError, match="Unknown plugin"):
        store.set_enabled("does-not-exist@1", False)
    with pytest.raises(ValueError, match="No installed plugin"):
        store.uninstall("does-not-exist@1")


def test_enabled_rule_ids_reflects_plugin_state(plugin_dir: Path) -> None:
    store = PluginStore()
    assert "command.gamerule-registry-names@94.1" in store.enabled_rule_ids()
    store.set_enabled("gamerules@94.1", False)
    assert "command.gamerule-registry-names@94.1" not in store.enabled_rule_ids()


def test_create_rule_registry_accepts_an_enabled_filter() -> None:
    registry = create_rule_registry(
        load_entry_points=False,
        enabled_rule_ids=frozenset({"identifier.chain-to-iron-chain@88"}),
    )
    ids = {item.id for item in registry.info()}
    assert ids == {"identifier.chain-to-iron-chain@88"}
