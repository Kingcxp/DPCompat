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
from textual.widgets import Button, Checkbox, Input, Markdown


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
            app.screen.query_one("#build-start", Button).scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#build-start")
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


def test_scaffold_helper_round_trip(tmp_path: Path) -> None:
    root = tmp_path.resolve()  # canonical form; see test_config notes for the 8.3-name quirk
    created = scaffold_plugin_template("demo.template", root, subfolder=True)
    assert created.parent == root / "demo.template"
    assert created.is_file()
