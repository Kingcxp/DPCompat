"""Smoke tests for the Textual TUI: screens render and plugin state applies."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from dpcompat.plugins import PluginStore, scaffold_plugin_template
from dpcompat.ui import DpCompatApp
from dpcompat.ui.app import PluginDetailScreen, PluginsScreen, TemplateScreen, VersionSection
from dpcompat.versions import PROFILES
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Markdown, RichLog, Static


def _run(coro) -> None:
    asyncio.run(coro)


def test_tui_boots_and_lists_every_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            boxes = list(app.screen.query(Checkbox))
            target_boxes = [box for box in boxes if box.id and box.id.startswith("target-")]
            assert len(target_boxes) == len(PROFILES)
            assert all(box.value for box in target_boxes)
            # Navigation and quit buttons must be present.
            assert app.screen.query_one("#quit-app", Button) is not None
            assert app.screen.query_one("#open-plugins", Button) is not None
            assert app.screen.query_one("#pack-browse", Button) is not None
            assert app.screen.query_one("#output-browse", Button) is not None

    _run(scenario())


def test_tui_plugins_screen_shows_builtin_and_installed_plugins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")  # open the plugins screen
            await pilot.pause()
            assert isinstance(app.screen, PluginsScreen)
            items = [button for button in app.screen.query(Button) if button.has_class("plugin-item")]
            assert len(items) >= 13  # every built-in plugin is browsable as a row

    _run(scenario())


def test_tui_target_checkboxes_reflect_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    config = tmp_path / "dpcompat.toml"
    config.write_text('[build]\ntargets=["1.21.4","1.21.5"]\n', encoding="utf-8")

    async def scenario() -> None:
        app = DpCompatApp(config_path=config)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.screen.query_one("#target-1-21-4", Checkbox).value is True
            assert app.screen.query_one("#target-1-21-5", Checkbox).value is True
            assert app.screen.query_one("#target-1-21-6", Checkbox).value is False

    _run(scenario())


def test_tui_output_subfolder_field_toggles_and_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            name_input = app.screen.query_one("#output-subfolder-name", Input)
            assert name_input.styles.display == "none"
            app.screen.query_one("#output-subfolder", Checkbox).value = True
            await pilot.pause()
            assert name_input.styles.display != "none"
            # Invalid names are rejected before the build starts.
            app.screen.query_one("#pack-path-input", Input).value = str(tmp_path)
            name_input.value = "bad/name"
            build_button = app.screen.query_one("#build-start", Button)
            build_button.scroll_visible(animate=False)
            build_button.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # The invalid subfolder name must abort before a build worker starts.
            assert not [worker for worker in app.workers if worker.group == "build"]

    _run(scenario())


def test_tui_plugins_screen_groups_plugins_by_target_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")  # open the plugins screen
            await pilot.pause()
            sections = list(app.screen.query(VersionSection))
            versions_with_plugins = sorted({info.target_version for info in PluginStore().list_plugins()})
            # Every version that owns plugins gets exactly one collapsible section.
            assert len(sections) == len(versions_with_plugins)
            # Sections start collapsed: the first plugin body is hidden.
            body = app.screen.query_one("#version-body-1-21-5", Vertical)
            assert body.styles.display == "none"
            # Clicking the full-width version header reveals its plugin rows.
            await pilot.click("#fold-1-21-5")
            await pilot.pause(0.3)  # wait out the button's 0.2s active effect
            assert body.styles.display != "none"
            # Clicking the header again collapses the section.
            await pilot.click("#fold-1-21-5")
            await pilot.pause(0.3)
            assert body.styles.display == "none"

    _run(scenario())


def test_tui_plugin_detail_page_toggles_and_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.click("#fold-1-21-5")
            await pilot.pause(0.3)
            # Opening a plugin row shows the detail page with its Markdown docs.
            await pilot.click("#plugin-text-components-71")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PluginDetailScreen)
            assert app.screen.query_one("#detail-doc", Markdown) is not None
            # The toggle flips the persisted enable state.
            await pilot.click("#detail-toggle")
            await pilot.pause(0.3)
            store = PluginStore()
            info = next(item for item in store.list_plugins() if item.id == "text-components@71")
            assert info.enabled is False
            # Toggling back and returning to the list works.
            await pilot.click("#detail-toggle")
            await pilot.pause(0.3)
            assert (
                next(item for item in PluginStore().list_plugins() if item.id == "text-components@71").enabled is True
            )
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PluginDetailScreen)
            assert isinstance(app.screen, PluginsScreen)

    _run(scenario())


def test_tui_template_screen_scaffolds_a_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            # Drive the template screen directly instead of walking the file tree.
            app.push_screen(TemplateScreen(tmp_path))
            await pilot.pause()
            app.screen.query_one("#template-name", Input).value = "demo.template"
            await pilot.click("#template-create")
            await pilot.pause()
            created = tmp_path / "demo.template.py"
            assert created.is_file()
            assert (tmp_path / "README.md").is_file()
            # The scaffolded file installs cleanly through the store.
            store = PluginStore()
            info = store.install(created)
            assert info.id == "demo.template@88"

    _run(scenario())


def test_tui_plugin_detail_renders_markdown_description_without_readme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    from dpcompat.plugins import PluginInfo

    info = PluginInfo(
        id="demo.markdown@88",
        name="Markdown Intro",
        description="**Bold** summary\n\n- one\n- two",
        version="1.0.0",
        origin="file",
        kind="python",
        enabled=True,
        target_version="1.21.9",
        readme="",  # no full documentation: the description itself is rendered as Markdown
    )

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(PluginDetailScreen(info, PluginStore()))
            await pilot.pause()
            doc = app.screen.query_one("#detail-doc", Markdown)
            assert doc is not None  # the Markdown widget is used for the description
            assert app.screen.query_one(".hint", Static) is not None  # no-readme hint shown

    _run(scenario())


def test_tui_marketplace_browses_and_installs_plugins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The marketplace screen lists remote plugins and installs one on demand."""

    import json as _json

    from dpcompat import market
    from dpcompat.ui.app import MarketDetailScreen, MarketScreen

    from helpers import repo_server

    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    plugin_py = '''"""demo.market: TUI fixture plugin."""

from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat

PLUGIN = {
    "id": "demo.market@88",
    "name": "Market Demo",
    "description": "TUI marketplace fixture.",
    "version": "1.0.0",
    "target_version": "1.21.9",
    "readme": "# Market Demo\\n\\nReadme.",
    "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"],
}


class DemoRule:
    id = "demo.market.rule@88"
    boundary = PackFormat(88)
    priority = 450

    def applies(self, source, target):
        return crosses(source, target, self.boundary)

    def apply(self, context):
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (DemoRule(),)
'''
    tree = tmp_path / "repo"
    tree.mkdir()
    (tree / "index.json").write_text(
        _json.dumps({"name": "tui-repo", "schema": 1, "categories": [{"id": "1.21.9", "path": "1.21.9"}]}),
        encoding="utf-8",
    )
    category = tree / "1.21.9"
    category.mkdir()
    (category / "INDEX.json").write_text(
        _json.dumps({"category": "1.21.9", "plugins": ["demo.market@88"]}),
        encoding="utf-8",
    )
    plugin = category / "demo.market@88"
    plugin.mkdir()
    (plugin / "demo.market@88.py").write_text(plugin_py, encoding="utf-8")

    with repo_server(tree) as base:
        monkeypatch.setattr(market, "load_repos", lambda: [market.RepoSpec(name="tui", url=base)])

        async def scenario() -> None:
            app = DpCompatApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("p")
                await pilot.pause()
                await pilot.click("#plugins-market")
                await pilot.pause()
                assert isinstance(app.screen, MarketScreen)
                # Wait for the loading worker to render the plugin row.
                rows: list[Button] = []
                for _ in range(40):
                    await pilot.pause(0.1)
                    rows = [button for button in app.screen.query(Button) if button.has_class("plugin-item")]
                    if rows:
                        break
                assert any("Market Demo" in str(button.label) for button in rows)
                # Open the detail page and install.
                await pilot.click("#market-tui-demo-market-88")
                await pilot.pause(0.3)
                assert isinstance(app.screen, MarketDetailScreen)
                await pilot.click("#market-install")
                for _ in range(40):
                    await pilot.pause(0.1)
                    if "demo.market@88" in {item.id for item in PluginStore().list_plugins()}:
                        break
                assert "demo.market@88" in {item.id for item in PluginStore().list_plugins()}
                # Back on the marketplace, the row now carries the installed mark.
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert isinstance(app.screen, MarketScreen)
                for _ in range(40):
                    await pilot.pause(0.1)
                    rows = [button for button in app.screen.query(Button) if button.has_class("plugin-item")]
                    if (rows and "installed" in str(rows[0].label).lower()) or "已安装" in str(rows[0].label):
                        break
                assert "已安装" in str(rows[0].label) or "installed" in str(rows[0].label).lower()

        _run(scenario())


def test_scaffold_helper_round_trip(tmp_path: Path) -> None:
    root = tmp_path.resolve()  # canonical form; see test_config notes for the 8.3-name quirk
    created = scaffold_plugin_template("demo.template", root, subfolder=True)
    assert created.parent == root / "demo.template"
    assert created.is_file()


def test_tui_language_switch_re_renders_and_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    from dpcompat import i18n

    # Redirect the preference file so the test never touches the real home directory,
    # and pin the locale so the first-run default is deterministic.
    monkeypatch.setattr(i18n, "PREFS_DIR", tmp_path)
    monkeypatch.setattr(i18n, "PREFS_FILE", tmp_path / "prefs.toml")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.language == "zh-CN"
            assert "数据包兼容性迁移" in str(app.screen.query_one("#main-title", Static).renderable)
            assert "简体中文" in str(app.screen.query_one("#lang-switch", Button).label)

            await pilot.press("l")  # cycle to English
            await pilot.pause()
            assert app.language == "en"
            assert "Data-pack Compatibility Migration" in str(app.screen.query_one("#main-title", Static).renderable)
            assert "English" in str(app.screen.query_one("#lang-switch", Button).label)
            # The choice is persisted for the next launch.
            assert i18n.load_preferred_language() == "en"

            # Plugins re-render in the new language: open the manager and check a
            # built-in row shows its localized name.
            await pilot.press("p")
            await pilot.pause()
            assert "Plugin Manager" in str(app.screen.query_one(".screen-title", Static).renderable)
            # The list is built by a worker, so wait for its rows instead of assuming.
            fold = None
            for _ in range(40):
                await pilot.pause(0.1)
                found = app.screen.query("#fold-1-21-11")
                if found:
                    fold = found.first(Button)
                    break
            assert fold is not None
            fold.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#fold-1-21-11")
            await pilot.pause(0.3)
            rows = [button for button in app.screen.query(Button) if button.has_class("plugin-item")]
            labels = [str(row.label) for row in rows]
            assert any("Gamerule registry renames" in label for label in labels)

    _run(scenario())


def test_tui_starts_with_localized_bindings_in_english(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot with a persisted English preference; footers must not show Chinese."""

    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))
    from dpcompat import i18n

    monkeypatch.setattr(i18n, "PREFS_DIR", tmp_path)
    monkeypatch.setattr(i18n, "PREFS_FILE", tmp_path / "prefs.toml")
    i18n.save_preferred_language("en")

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.language == "en"

            def descriptions(widget: object) -> dict[str, str]:
                return {key: active.binding.description for key, active in widget.active_bindings.items()}  # type: ignore[attr-defined]

            # App shell: q quits, l cycles language — in English.
            shell = descriptions(app)
            assert shell["q"] == "Quit"
            assert shell["l"] == "Language"
            # Migration screen: p opens the plugin manager — in English.
            screen = descriptions(app.screen)
            assert screen["p"] == "Plugin Manager"

            # Modal screens localize their escape binding on mount as well.
            await pilot.press("p")
            await pilot.pause()
            plugins = descriptions(app.screen)
            assert plugins["escape"] == "Back"

    _run(scenario())


def test_tab_moves_focus_on_every_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tab traversal must survive the localized binding override.

    ``_set_bindings`` used to replace the whole binding map, which silently removed
    Textual's inherited tab/shift+tab focus bindings.
    """

    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            first = app.focused
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is not None and app.focused is not first
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is first

    _run(scenario())


def test_language_switch_on_a_detail_page_keeps_the_app_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching language while a detail page is open must rebuild it, not crash it."""

    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp(language="en")
        async with app.run_test() as pilot:
            await pilot.pause()
            store = PluginStore()
            info = next(item for item in store.list_plugins() if item.id == "gamerules@94.1")
            app.push_screen(PluginDetailScreen(info, store))
            await pilot.pause()
            assert app.screen.query_one("#detail-meta", Static) is not None
            await pilot.press("l")
            await pilot.pause(0.3)
            assert app.screen.query_one("#detail-meta", Static) is not None
            assert len(app.screen.query("#detail-meta")) == 1

    _run(scenario())


def test_market_rows_are_unique_per_repository() -> None:
    """Two repositories may publish one plugin id without breaking the list."""

    from dpcompat.market import MarketPlugin, MarketPluginMeta
    from dpcompat.plugins import BUILTIN_PLUGINS
    from dpcompat.ui.app import MarketRow

    info = BUILTIN_PLUGINS[0]
    meta = MarketPluginMeta()
    first = MarketRow(MarketPlugin(info=info, repo="alpha", category="1.21.9", meta=meta), "en", False)
    second = MarketRow(MarketPlugin(info=info, repo="beta", category="1.21.9", meta=meta), "en", False)
    assert first.id is not None and second.id is not None
    assert first.id != second.id
    assert "alpha" in first.id and "beta" in second.id
    assert info.id.replace("@", "-") in first.id


def test_build_validation_is_logged_and_focuses_the_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-flight failure must leave a trace in the log and focus the offending input."""

    monkeypatch.setenv("DPCOMPAT_PLUGIN_DIR", str(tmp_path / "plugins"))

    async def scenario() -> None:
        app = DpCompatApp(language="en")
        async with app.run_test() as pilot:
            await pilot.pause()
            log = app.screen.query_one("#build-log", RichLog)
            await pilot.press("ctrl+b")  # the keyboard build shortcut
            await pilot.pause()
            assert app.screen.query_one("#pack-path-input", Input) is app.focused
            assert any("data pack" in line.text.lower() or "路径" in line.text for line in log.lines)

    _run(scenario())
