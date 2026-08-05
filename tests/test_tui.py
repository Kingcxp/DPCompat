"""Smoke tests for the Textual TUI: screens render and plugin state applies."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from dpcompat.plugins import PluginStore, scaffold_plugin_template
from dpcompat.ui import DpCompatApp
from dpcompat.ui.app import PluginsScreen, TemplateScreen
from dpcompat.versions import PROFILES
from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Input


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
            toggles = [box for box in app.screen.query(Checkbox) if box.id and box.id.startswith("enable-")]
            assert len(toggles) >= 13  # every built-in plugin is browsable
            # Toggle the first built-in plugin off and back on through the UI.
            first = toggles[0]
            first.value = False
            await pilot.pause()
            first.value = True
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PluginsScreen)

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
            app.screen.query_one("#migration-root", VerticalScroll).scroll_end(animate=False)
            await pilot.pause()
            await pilot.click("#build-start")
            await pilot.pause()
            # The invalid subfolder name must abort before a build worker starts.
            assert not [worker for worker in app.workers if worker.group == "build"]

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
    created = scaffold_plugin_template("demo.template", tmp_path, subfolder=True)
    assert created.parent == tmp_path / "demo.template"
    assert created.is_file()
