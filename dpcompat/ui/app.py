"""Textual screens for DPCompat: migration form, plugin manager, file picker.

The migration screen is the default view.  The plugins screen and the file
picker are pushed on top of it as modal screens.  All heavy work (pack
materialization and compilation) runs in a worker thread and reports back
through :meth:`App.call_from_thread`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DirectoryTree,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    Tree,
)

from ..config import ProjectConfig, load_config
from ..engine import compile_pack
from ..models import BuildPolicy, Diagnostic, VersionProfile
from ..packio import materialize_source
from ..plugins import PluginInfo, PluginStore, create_effective_registry
from ..versions import PROFILES


def _widget_safe(value: str) -> str:
    """Turn an arbitrary plugin/version id into a valid widget id fragment."""

    return re.sub(r"[^a-zA-Z0-9_-]", "-", value)


def _target_widget_id(game_version: str) -> str:
    return "target-" + _widget_safe(game_version)


class FilePickerScreen(Screen[Path | None]):
    """Modal filesystem browser returning a directory or a matching file."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding("escape", "cancel", "取消")]

    def __init__(
        self,
        *,
        title: str,
        start: Path | None = None,
        allowed_suffixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self._title = title
        self._start = (start or Path.cwd()).expanduser().resolve()
        self._allowed_suffixes = allowed_suffixes

    def compose(self) -> ComposeResult:
        """Render the tree browser with pick/cancel/up controls."""

        yield Header(show_clock=False)
        with Vertical(id="picker-root"):
            yield Static(self._title, classes="screen-title")
            yield Static(str(self._start), id="picker-current")
            yield DirectoryTree(self._start, id="picker-tree")
            with Horizontal(classes="button-row"):
                yield Button("上级目录", id="picker-up")
                yield Button("选择当前项", id="picker-pick", variant="primary")
                yield Button("取消", id="picker-cancel")
        yield Footer()

    def _current(self) -> Path | None:
        tree = self.query_one("#picker-tree", DirectoryTree)
        node = tree.cursor_node
        return node.data.path if node is not None and node.data is not None else None

    def on_directory_tree_node_highlighted(self, event: Tree.NodeHighlighted[Path]) -> None:
        """Show the highlighted path in the status line."""

        self.query_one("#picker-current", Static).update(str(event.node.data))

    def action_cancel(self) -> None:
        """Close the picker without a result."""

        self.dismiss(None)

    def _pick(self) -> None:
        current = self._current()
        if current is None:
            self.notify("请先在左侧目录树中选择一项", severity="warning")
            return
        if current.is_dir():
            self.dismiss(current)
            return
        if self._allowed_suffixes and current.suffix.lower() not in self._allowed_suffixes:
            allowed = "、".join(self._allowed_suffixes)
            self.notify(f"此处只能选择 {allowed} 文件", severity="warning")
            return
        self.dismiss(current)

    @on(Button.Pressed, "#picker-cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#picker-pick")
    def _on_pick(self) -> None:
        self._pick()

    @on(Button.Pressed, "#picker-up")
    def _on_up(self) -> None:
        current = self._current()
        parent = (current or self._start).parent
        tree = self.query_one("#picker-tree", DirectoryTree)
        tree.path = parent
        self.query_one("#picker-current", Static).update(str(parent))


class PluginCard(Vertical):
    """One browsable plugin row: toggle, name, description, and rules."""

    class Removed(Message):
        """The user asked to uninstall this file plugin."""

        def __init__(self, plugin_id: str) -> None:
            super().__init__()
            self.plugin_id = plugin_id

    def __init__(self, info: PluginInfo, store: PluginStore) -> None:
        super().__init__()
        self._info = info
        self._store = store

    def compose(self) -> ComposeResult:
        """Render one plugin card with its toggle and metadata."""

        safe = _widget_safe(self._info.id)
        with Vertical(classes="plugin-card"):
            with Horizontal():
                yield Checkbox(value=self._info.enabled, id=f"enable-{safe}")
                yield Static(Text(self._info.name, style="bold"), classes="plugin-name")
                yield Static(self._info.id, classes="plugin-id")
                yield Static(
                    "内置" if self._info.origin == "builtin" else "文件",
                    classes=f"plugin-badge {'badge-builtin' if self._info.origin == 'builtin' else 'badge-file'}",
                )
            yield Static(self._info.description, classes="plugin-desc")
            yield Static(f"规则: {', '.join(self._info.rules) or '—'}", classes="plugin-rules")
            if self._info.origin == "file":
                yield Button("卸载", id=f"remove-{safe}", classes="plugin-remove")

    @on(Checkbox.Changed)
    def _on_toggle(self, event: Checkbox.Changed) -> None:
        if not event.checkbox.id or not event.checkbox.id.startswith("enable-"):
            return
        self._store.set_enabled(self._info.id, event.checkbox.value)
        self.notify(f"{'已启用' if event.checkbox.value else '已禁用'}插件：{self._info.name}")

    @on(Button.Pressed)
    def _on_remove(self, event: Button.Pressed) -> None:
        if not event.button.id or not event.button.id.startswith("remove-"):
            return
        self.post_message(self.Removed(self._info.id))


class PluginsScreen(Screen[None]):
    """Browse, install, and toggle built-in and installed plugins."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self) -> None:
        super().__init__()
        self._store: PluginStore | None = None

    def compose(self) -> ComposeResult:
        """Render the plugin list with install and navigation buttons."""

        yield Header(show_clock=False)
        with Vertical(id="plugins-root"):
            yield Static("插件管理", classes="screen-title")
            yield Static(
                "内置与已安装插件均可启用或禁用；禁用后其迁移规则不再参与构建。",
                classes="hint",
            )
            yield VerticalScroll(id="plugin-list")
            with Horizontal(classes="button-row"):
                yield Button("安装插件文件...", id="plugins-install", variant="primary")
                yield Button("刷新", id="plugins-refresh")
                yield Button("返回", id="plugins-back")
        yield Footer()

    def on_mount(self) -> None:
        """Load the plugin store and render the current plugin state."""

        self._store = PluginStore()
        self._refresh()

    def _refresh(self) -> None:
        box = self.query_one("#plugin-list", VerticalScroll)
        box.remove_children()
        assert self._store is not None
        for info in self._store.list_plugins():
            box.mount(PluginCard(info, self._store))

    def _install_flow(self, path: Path | None) -> None:
        if path is None:
            return
        assert self._store is not None
        try:
            info = self._store.install(path)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"已安装插件：{info.name} ({info.id})")
        self._refresh()

    @on(Button.Pressed, "#plugins-install")
    def _on_install(self) -> None:
        self.app.push_screen(
            FilePickerScreen(
                title="选择插件文件（.py 或 .json）",
                allowed_suffixes=(".py", ".json"),
            ),
            callback=self._install_flow,
        )

    @on(Button.Pressed, "#plugins-refresh")
    def _on_refresh(self) -> None:
        self._refresh()

    @on(Button.Pressed, "#plugins-back")
    def _on_back(self) -> None:
        self.app.pop_screen()

    @on(PluginCard.Removed)
    def _on_removed(self, event: PluginCard.Removed) -> None:
        assert self._store is not None
        try:
            self._store.uninstall(event.plugin_id)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"已卸载插件：{event.plugin_id}")
        self._refresh()


class MigrationScreen(Screen[None]):
    """Main screen: pack path, target versions, policy, and build output."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("p", "open_plugins", "插件管理")
    ]

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._config: ProjectConfig | None = None

    def compose(self) -> ComposeResult:
        """Render the migration form: pack, targets, policy, and build log."""

        yield Header(show_clock=False)
        with VerticalScroll(id="migration-root"):
            yield Static("数据包兼容性迁移", classes="screen-title")
            with Horizontal(classes="field-row"):
                yield Input(
                    placeholder="数据包目录或 ZIP 文件路径",
                    id="pack-path-input",
                )
                yield Button("浏览...", id="pack-browse", variant="primary")
            with Horizontal(classes="field-row"):
                yield Input(value="dist", id="output-input", placeholder="输出目录（相对当前目录）")
            yield Static("目标版本（勾选要迁移到的版本）", classes="section-title")
            with VerticalScroll(id="target-list", classes="target-list"):
                for profile in PROFILES:
                    yield Checkbox(
                        f"{profile.game_version}  （格式 {profile.pack_format}）",
                        value=True,
                        id=_target_widget_id(profile.game_version),
                    )
            with Horizontal(classes="button-row"):
                yield Button("全选", id="targets-all")
                yield Button("全不选", id="targets-none")
            yield Static("迁移策略（默认拒绝有损/未知迁移）", classes="section-title")
            yield Checkbox("允许模拟迁移 allow_emulated", value=True, id="policy-emulated")
            yield Checkbox("允许有损迁移 allow_lossy", id="policy-lossy")
            yield Checkbox("允许未知迁移 allow_unknown", id="policy-unknown")
            yield Checkbox("警告即失败 fail_on_warnings", id="policy-fail-warnings")
            with Horizontal(classes="button-row"):
                yield Button("开始迁移", id="build-start", variant="success")
                yield Button("插件管理", id="open-plugins")
            yield RichLog(id="build-log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        """Load the optional config and apply its target defaults."""

        self._config = load_config(self._config_path) if self._config_path else ProjectConfig()
        assert self._config is not None
        if self._config.targets:
            selected = set(self._config.targets)
            for profile in PROFILES:
                checkbox = self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox)
                checkbox.value = profile.game_version in selected

    def action_open_plugins(self) -> None:
        """Push the plugin management screen."""

        self.app.push_screen(PluginsScreen())

    def _selected_targets(self) -> list[VersionProfile]:
        return [
            profile
            for profile in PROFILES
            if self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox).value
        ]

    def _policy(self) -> BuildPolicy:
        return BuildPolicy(
            allow_emulated=self.query_one("#policy-emulated", Checkbox).value,
            allow_lossy=self.query_one("#policy-lossy", Checkbox).value,
            allow_unknown=self.query_one("#policy-unknown", Checkbox).value,
            fail_on_warnings=self.query_one("#policy-fail-warnings", Checkbox).value,
        )

    @on(Button.Pressed, "#pack-browse")
    def _on_browse_pack(self) -> None:
        self.app.push_screen(
            FilePickerScreen(
                title="选择数据包目录或 ZIP 文件",
                allowed_suffixes=(".zip",),
            ),
            callback=self._set_pack_path,
        )

    def _set_pack_path(self, path: Path | None) -> None:
        if path is not None:
            self.query_one("#pack-path-input", Input).value = str(path)

    @on(Button.Pressed, "#targets-all")
    def _on_targets_all(self) -> None:
        for profile in PROFILES:
            self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox).value = True

    @on(Button.Pressed, "#targets-none")
    def _on_targets_none(self) -> None:
        for profile in PROFILES:
            self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox).value = False

    @on(Button.Pressed, "#open-plugins")
    def _on_open_plugins(self) -> None:
        self.action_open_plugins()

    @on(Button.Pressed, "#build-start")
    def _on_build_start(self) -> None:
        pack_path = self.query_one("#pack-path-input", Input).value.strip()
        if not pack_path:
            self.notify("请先填写数据包路径", severity="error")
            return
        targets = self._selected_targets()
        if not targets:
            self.notify("请至少勾选一个目标版本", severity="error")
            return
        output = self.query_one("#output-input", Input).value.strip() or "dist"
        log = self.query_one("#build-log", RichLog)
        self.run_worker(
            self._build_task(log, pack_path, output, targets, self._policy()),
            thread=True,
            exclusive=True,
            group="build",
        )

    async def _build_task(
        self,
        log: RichLog,
        pack_path: str,
        output: str,
        targets: list[VersionProfile],
        policy: BuildPolicy,
    ) -> None:
        """Blocking build executed by the threaded worker."""

        # Widget references must be captured on the main thread; the worker only
        # posts log writes back through call_from_thread.
        assert self._config is not None

        def write(line: str) -> None:
            self.app.call_from_thread(log.write, line)

        write("[bold cyan]开始构建…[/bold cyan]")
        try:
            registry = create_effective_registry(self._config)
            with materialize_source(Path(pack_path)) as root:
                detection, results, universal = compile_pack(
                    root,
                    targets,
                    Path(output),
                    self._config.universal,
                    policy=policy,
                    fallbacks=self._config.fallbacks,
                    output_name=self._config.output_name,
                    emit_archives=True,
                    rules=registry.rules(),
                )
        except Exception as exc:  # Surface any failure in the log instead of crashing the UI.
            write(f"[bold red]构建失败：{exc}[/bold red]")
            self.app.call_from_thread(self.notify, f"构建失败：{exc}", severity="error")
            return

        write(f"来源格式：{detection.source_format}  候选版本：{', '.join(detection.candidates) or '—'}")
        for diagnostic in detection.diagnostics:
            write(self._diagnostic_line(diagnostic))
        for result in results:
            status = "OK" if result.successful else "FAILED"
            style = "bold green" if result.successful else "bold red"
            artifact = result.archive.name if result.archive else (result.sha256 or "—")
            summary = f"[{style}]{status}[/{style}] {result.profile.game_version}（格式 {result.profile.pack_format}）"
            write(f"{summary} {artifact}")
            for diagnostic in result.diagnostics:
                write(self._diagnostic_line(diagnostic))
        if universal:
            write(f"[bold]通用 overlay 包：{universal}[/bold]")
        write(f"报告：{Path(output).resolve() / 'compatibility-report.json'}")
        self.app.call_from_thread(self.notify, "迁移完成", severity="information")

    @staticmethod
    def _diagnostic_line(diagnostic: Diagnostic) -> str:
        location = diagnostic.path or ""
        if diagnostic.line:
            location += f":{diagnostic.line}"
        style = "red" if diagnostic.severity.value >= 30 else "yellow" if diagnostic.severity.value >= 20 else "cyan"
        return f"[{style}]{diagnostic.code}[/{style}] {location} {diagnostic.message}"


class DpCompatApp(App[None]):
    """Textual application tying the migration and plugin screens together."""

    TITLE = "DPCompat"
    SUB_TITLE = "数据包兼容性迁移工具"
    CSS = """
    Screen {
        layout: vertical;
    }
    #migration-root, #plugins-root, #picker-root {
        padding: 1 2;
    }
    .screen-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    .section-title {
        text-style: bold;
        padding-top: 1;
        padding-bottom: 1;
    }
    .hint {
        color: $text-muted;
        padding-bottom: 1;
    }
    .field-row {
        height: 3;
    }
    .target-list {
        height: 12;
        border: round $primary;
    }
    .button-row {
        height: 3;
        padding-top: 1;
    }
    #build-log {
        height: 16;
        border: round $primary;
        margin-top: 1;
    }
    .plugin-card {
        border: round $surface;
        padding: 1;
        margin-bottom: 1;
    }
    .plugin-name {
        padding: 0 1;
    }
    .plugin-id {
        color: $text-muted;
        padding: 0 1;
    }
    .plugin-badge {
        padding: 0 1;
    }
    .badge-builtin {
        color: $success;
    }
    .badge-file {
        color: $warning;
    }
    .plugin-desc {
        padding-top: 1;
    }
    .plugin-rules {
        color: $text-muted;
        padding-top: 1;
    }
    .plugin-remove {
        width: 12;
        margin-top: 1;
    }
    #picker-tree {
        height: 1fr;
        border: round $primary;
    }
    """

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path

    def on_mount(self) -> None:
        """Push the migration screen as the default view."""

        self.push_screen(MigrationScreen(self._config_path))
