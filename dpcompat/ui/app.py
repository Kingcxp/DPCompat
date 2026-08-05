"""Textual screens for DPCompat: migration form, plugin manager, file picker.

The migration screen is the default view.  The plugins screen, the file picker,
and the template screen are pushed on top of it as modal screens.  All heavy
work (pack materialization and compilation) runs in a worker thread and reports
back through :meth:`App.call_from_thread`.

Layout notes: static text widgets inside horizontal rows must be given explicit
widths (``auto`` resolves to the full row width in Textual), and plugin cards
must use ``height: auto`` instead of the container default ``1fr`` so a scroll
list does not squeeze every card to a few rows.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

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
    Markdown,
    RichLog,
    Static,
    Tree,
)

from ..config import ProjectConfig, load_config
from ..engine import compile_pack
from ..models import BuildPolicy, Diagnostic, PackFormat, VersionProfile
from ..packio import materialize_source
from ..plugins import (
    PluginInfo,
    PluginStore,
    create_effective_registry,
    scaffold_plugin_template,
)
from ..versions import PROFILES

_SUBFOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


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
        directories_only: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._start = (start or Path.cwd()).expanduser().resolve()
        self._allowed_suffixes = allowed_suffixes
        self._directories_only = directories_only

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
            self.notify("请先在目录树中选择一项", severity="warning")
            return
        if current.is_dir():
            self.dismiss(current)
            return
        if self._directories_only:
            self.notify("这里需要选择一个文件夹", severity="warning")
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


class PluginItem(Button):
    """One plugin row: name, version, status dot, and a short description.

    The whole row is a button; pressing it opens the plugin detail page with
    the plugin's full Markdown documentation, like a VS Code extension.
    """

    def __init__(self, info: PluginInfo) -> None:
        self.info = info
        status = "●" if info.enabled else "○"
        status_style = "green" if info.enabled else "dim red"
        origin = "内置" if info.origin == "builtin" else "文件"
        label = (
            f"[bold]{info.name}[/bold]  [dim]v{info.version} · {origin}[/dim]  [{status_style}]{status}[/]"
            f"\n[dim]{info.description}[/dim]"
        )
        super().__init__(label, id=f"plugin-{_widget_safe(info.id)}", classes="plugin-item")


class VersionSection(Vertical):
    """A collapsible group of plugin rows for one target Minecraft version.

    The header is a full-width button so the whole row toggles the body; the
    body lists one :class:`PluginItem` per plugin of that version.
    """

    class ExpansionChanged(Message):
        """The section was expanded or collapsed by the user."""

        def __init__(self, version: str, expanded: bool) -> None:
            super().__init__()
            self.version = version
            self.expanded = expanded

    class PluginSelected(Message):
        """The user pressed one of the plugin rows."""

        def __init__(self, info: PluginInfo) -> None:
            super().__init__()
            self.info = info

    def __init__(
        self,
        version: str,
        pack_format: PackFormat | None,
        plugins: list[PluginInfo],
        *,
        expanded: bool,
    ) -> None:
        super().__init__(classes="version-section")
        self._version = version
        self._pack_format = pack_format
        self._plugins = plugins
        self._expanded = expanded
        format_part = f"格式 {pack_format}" if pack_format is not None else "未注册版本"
        enabled = sum(1 for info in plugins if info.enabled)
        self._head_suffix = f"{version}  ·  {format_part}  ·  {len(plugins)} 个插件 · {enabled}/{len(plugins)} 已启用"

    def compose(self) -> ComposeResult:
        """Render the toggle header and the collapsible plugin body."""

        safe = _widget_safe(self._version)
        arrow = "▾" if self._expanded else "▸"
        yield Button(f"{arrow}  {self._head_suffix}", id=f"fold-{safe}", classes="version-fold")
        with Vertical(id=f"version-body-{safe}", classes="version-body"):
            for info in self._plugins:
                yield PluginItem(info)

    def on_mount(self) -> None:
        """Start collapsed so the screen reads as a compact version list."""

        if not self._expanded:
            self.query_one(f"#version-body-{_widget_safe(self._version)}", Vertical).styles.display = "none"

    @on(Button.Pressed, ".version-fold")
    def _toggle(self, event: Button.Pressed) -> None:
        """Expand or collapse this version's plugin rows."""

        self._expanded = not self._expanded
        body = self.query_one(f"#version-body-{_widget_safe(self._version)}", Vertical)
        body.styles.display = "block" if self._expanded else "none"
        event.button.label = ("▾" if self._expanded else "▸") + "  " + self._head_suffix
        self.post_message(self.ExpansionChanged(self._version, self._expanded))

    @on(Button.Pressed, ".plugin-item")
    def _on_plugin_pressed(self, event: Button.Pressed) -> None:
        """Forward a plugin row press so the screen can open the detail page."""

        if isinstance(event.button, PluginItem):
            self.post_message(self.PluginSelected(event.button.info))


class PluginDetailScreen(Screen[None]):
    """Detail page for one plugin: metadata, enable toggle, and its Markdown docs."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self, info: PluginInfo, store: PluginStore) -> None:
        super().__init__()
        self._info = info
        self._store = store

    def compose(self) -> ComposeResult:
        """Render the plugin header, actions, and the Markdown documentation."""

        origin = "内置" if self._info.origin == "builtin" else "文件"
        kind = {"python": "Python", "declarative": "声明式", "builtin": "内置"}[self._info.kind]
        type_part = f"{origin}插件（{kind}）" if self._info.origin == "file" else "内置插件"
        yield Header(show_clock=False)
        with Vertical(id="detail-root"):
            yield Static(self._info.name, classes="screen-title")
            yield Static(
                f"{self._info.id} · v{self._info.version} · {type_part} · 目标 {self._info.target_version}",
                id="detail-meta",
            )
            with Horizontal(classes="button-row"):
                yield Button("禁用" if self._info.enabled else "启用", id="detail-toggle", variant="primary")
                if self._info.origin == "file":
                    yield Button("卸载", id="detail-remove", variant="error")
                yield Button("返回", id="detail-back")
            if self._info.readme:
                yield Markdown(self._info.readme, id="detail-doc")
            else:
                yield Static(self._info.description, classes="hint")
                yield Static("该插件没有提供 Markdown 说明文档，只有上面的简短描述。", classes="hint")
        yield Footer()

    @on(Button.Pressed, "#detail-toggle")
    def _on_toggle(self) -> None:
        """Flip the persisted enable state and refresh the toggle label."""

        enabled = not self._info.enabled
        self._store.set_enabled(self._info.id, enabled)
        self._info = self._info.model_copy(update={"enabled": enabled})
        self.query_one("#detail-toggle", Button).label = "禁用" if enabled else "启用"
        self.notify(f"{'已启用' if enabled else '已禁用'}插件：{self._info.name}")

    @on(Button.Pressed, "#detail-remove")
    def _on_remove(self) -> None:
        """Uninstall this file plugin and return to the plugin list."""

        try:
            self._store.uninstall(self._info.id)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"已卸载插件：{self._info.id}")
        self.app.pop_screen()

    @on(Button.Pressed, "#detail-back")
    def _on_back(self) -> None:
        """Return to the plugin list."""

        self.app.pop_screen()


class TemplateScreen(Screen[Path | None]):
    """Ask for a plugin template name and whether to create a subfolder."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding("escape", "cancel", "取消")]

    def __init__(self, location: Path) -> None:
        super().__init__()
        self._location = location

    def compose(self) -> ComposeResult:
        """Render the template name form."""

        yield Header(show_clock=False)
        with Vertical(id="template-root"):
            yield Static("创建插件模板", classes="screen-title")
            yield Static(f"位置：{self._location}", id="template-location")
            yield Input(placeholder="插件名称（小写字母/数字/._-）", id="template-name")
            yield Checkbox("在所选文件夹下创建同名子文件夹", id="template-subfolder")
            with Horizontal(classes="button-row"):
                yield Button("创建", id="template-create", variant="success")
                yield Button("取消", id="template-cancel")
        yield Footer()

    def action_cancel(self) -> None:
        """Close the template screen without creating anything."""

        self.dismiss(None)

    def _create(self) -> None:
        name = self.query_one("#template-name", Input).value.strip()
        if not _TEMPLATE_NAME_RE.fullmatch(name):
            self.notify(
                "插件名称只能包含小写字母、数字、'.'、'_'、'-'，且不能以数字开头",
                severity="error",
            )
            return
        subfolder = self.query_one("#template-subfolder", Checkbox).value
        try:
            created = scaffold_plugin_template(name, self._location, subfolder=subfolder)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.dismiss(created)

    @on(Button.Pressed, "#template-create")
    def _on_create(self) -> None:
        self._create()

    @on(Button.Pressed, "#template-cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()


class PluginsScreen(Screen[None]):
    """Browse, install, scaffold, and toggle built-in and installed plugins."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self) -> None:
        super().__init__()
        self._store: PluginStore | None = None
        self._expanded_versions: set[str] = set()

    def compose(self) -> ComposeResult:
        """Render the version-grouped plugin list with install and scaffold buttons."""

        yield Header(show_clock=False)
        with Vertical(id="plugins-root"):
            yield Static("插件管理", classes="screen-title")
            yield Static(
                "插件按目标版本分组：点击版本行展开该版本的插件列表，点击插件行查看它的完整文档、启用/禁用或卸载。",
                classes="hint",
            )
            yield VerticalScroll(id="plugin-list")
            with Horizontal(classes="button-row"):
                yield Button("安装插件文件...", id="plugins-install", variant="primary")
                yield Button("创建插件模板...", id="plugins-template", variant="primary")
                yield Button("刷新", id="plugins-refresh")
                yield Button("返回", id="plugins-back")
        yield Footer()

    def on_mount(self) -> None:
        """Load the plugin store and render the current plugin state."""

        self._store = PluginStore()
        self._refresh()

    def _refresh(self) -> None:
        """Rebuild the list, grouping plugins by their declared target version."""

        box = self.query_one("#plugin-list", VerticalScroll)
        box.remove_children()
        assert self._store is not None
        infos = self._store.list_plugins()
        by_version: dict[str, list[PluginInfo]] = {}
        for info in infos:
            by_version.setdefault(info.target_version, []).append(info)
        format_by_version = {profile.game_version: profile.pack_format for profile in PROFILES}
        for profile in PROFILES:
            if profile.game_version in by_version:
                box.mount(
                    VersionSection(
                        profile.game_version,
                        profile.pack_format,
                        by_version[profile.game_version],
                        expanded=profile.game_version in self._expanded_versions,
                    )
                )
        # Plugins written for versions that are not registered yet (e.g. ahead of
        # a release) stay browsable at the end of the list.
        for version in sorted(set(by_version) - set(format_by_version)):
            box.mount(
                VersionSection(
                    version,
                    None,
                    by_version[version],
                    expanded=version in self._expanded_versions,
                )
            )

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

    def _template_location(self, location: Path | None) -> None:
        if location is None:
            return
        self.app.push_screen(TemplateScreen(location), callback=self._template_result)

    def _template_result(self, created: Path | None) -> None:
        if created is None:
            return
        self.notify(f"插件模板已创建：{created}")
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

    @on(Button.Pressed, "#plugins-template")
    def _on_template(self) -> None:
        self.app.push_screen(
            FilePickerScreen(title="选择插件模板位置（文件夹）", directories_only=True),
            callback=self._template_location,
        )

    @on(Button.Pressed, "#plugins-refresh")
    def _on_refresh(self) -> None:
        self._refresh()

    @on(Button.Pressed, "#plugins-back")
    def _on_back(self) -> None:
        self.app.pop_screen()

    @on(VersionSection.ExpansionChanged)
    def _on_expansion(self, event: VersionSection.ExpansionChanged) -> None:
        """Remember which sections are open so a refresh keeps them open."""

        if event.expanded:
            self._expanded_versions.add(event.version)
        else:
            self._expanded_versions.discard(event.version)

    @on(VersionSection.PluginSelected)
    def _on_plugin_selected(self, event: VersionSection.PluginSelected) -> None:
        """Open the detail page for the selected plugin and refresh on return."""

        assert self._store is not None
        self.app.push_screen(PluginDetailScreen(event.info, self._store), callback=self._detail_closed)

    def _detail_closed(self, result: object) -> None:
        """Rebuild the list when the detail page is closed (toggle or uninstall)."""

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
        """Render the migration form: pack, output, targets, policy, and log."""

        yield Header(show_clock=False)
        with VerticalScroll(id="migration-root"):
            with Horizontal(id="top-bar"):
                yield Static("数据包兼容性迁移", classes="screen-title", id="main-title")
                yield Button("插件管理", id="open-plugins", variant="primary")
                yield Button("退出", id="quit-app", variant="error")
            yield Static("数据包", classes="section-title")
            with Horizontal(classes="field-row"):
                yield Input(
                    placeholder="数据包目录或 ZIP 文件路径",
                    id="pack-path-input",
                )
                yield Button("浏览...", id="pack-browse", variant="primary")
            yield Static("输出", classes="section-title")
            with Horizontal(classes="field-row"):
                yield Input(value="dist", id="output-input", placeholder="输出目录（相对当前目录）")
                yield Button("浏览...", id="output-browse", variant="primary")
            with Horizontal(classes="field-row"):
                yield Checkbox("创建子文件夹", id="output-subfolder")
                yield Input(placeholder="子文件夹名称（字母/数字/._-）", id="output-subfolder-name")
            yield Static("目标版本（勾选要迁移到的版本）", classes="section-title")
            yield Static(
                "目标版本由已注册的正式发布自动生成；每个版本对应的迁移插件可在“插件管理”中按版本查看与开关。",
                classes="hint",
            )
            with Horizontal(id="target-columns"):
                with Vertical(id="target-col-a"):
                    for profile in PROFILES[: len(PROFILES) // 2]:
                        yield Checkbox(
                            f"{profile.game_version}  （格式 {profile.pack_format}）",
                            value=True,
                            id=_target_widget_id(profile.game_version),
                            classes="-textual-compact",
                        )
                with Vertical(id="target-col-b"):
                    for profile in PROFILES[len(PROFILES) // 2 :]:
                        yield Checkbox(
                            f"{profile.game_version}  （格式 {profile.pack_format}）",
                            value=True,
                            id=_target_widget_id(profile.game_version),
                            classes="-textual-compact",
                        )
            with Horizontal(classes="button-row"):
                yield Button("全选", id="targets-all", variant="primary")
                yield Button("全不选", id="targets-none")
            yield Static("迁移策略", classes="section-title")
            with Horizontal(id="policy-columns"):
                with Vertical(classes="policy-col"):
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            "允许模拟迁移（allow_emulated）",
                            value=True,
                            id="policy-emulated",
                            classes="-textual-compact",
                        )
                        yield Static(
                            "模拟迁移：按等价规则改写结构，结果与目标版本行为一致但不逐字相同。",
                            classes="policy-desc",
                        )
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            "允许有损迁移（allow_lossy）",
                            id="policy-lossy",
                            classes="-textual-compact",
                        )
                        yield Static(
                            "有损迁移：接受会丢失部分信息的改写结果，例如精度舍入或字段移除。",
                            classes="policy-desc",
                        )
                with Vertical(classes="policy-col"):
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            "允许未知迁移（allow_unknown）",
                            id="policy-unknown",
                            classes="-textual-compact",
                        )
                        yield Static(
                            "未知迁移：接受无法确认是否等价的改写，需要事后人工复核。",
                            classes="policy-desc",
                        )
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            "警告即失败（fail_on_warnings）",
                            id="policy-fail-warnings",
                            classes="-textual-compact",
                        )
                        yield Static(
                            "警告即失败：构建中出现任何警告都视为失败，常用于严格校验。",
                            classes="policy-desc",
                        )
            yield Static(
                "默认只允许无损与模拟迁移；有损/未知迁移需要显式勾选，unsupported（目标版本没有等价机制）永远拒绝。",
                classes="hint",
            )
            with Horizontal(classes="button-row"):
                yield Button("开始迁移", id="build-start", variant="success")
            yield Static("构建日志", classes="section-title")
            yield RichLog(id="build-log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        """Load the optional config, apply its target defaults, and hint at the log."""

        self._config = load_config(self._config_path) if self._config_path else ProjectConfig()
        assert self._config is not None
        if self._config.targets:
            selected = set(self._config.targets)
            for profile in PROFILES:
                checkbox = self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox)
                checkbox.value = profile.game_version in selected
        self.query_one("#build-log", RichLog).write("[dim]构建日志将显示在这里；点击“开始迁移”开始。[/dim]")

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

    def _resolve_output(self) -> Path | None:
        base = self.query_one("#output-input", Input).value.strip() or "dist"
        output = Path(base).expanduser()
        if self.query_one("#output-subfolder", Checkbox).value:
            name = self.query_one("#output-subfolder-name", Input).value.strip()
            if not _SUBFOLDER_NAME_RE.fullmatch(name):
                self.notify(
                    "子文件夹名称只能包含字母、数字、'.'、'_'、'-'",
                    severity="error",
                )
                return None
            output = output / name
        return output

    @on(Checkbox.Changed, "#output-subfolder")
    def _on_output_subfolder(self, event: Checkbox.Changed) -> None:
        self.query_one("#output-subfolder-name", Input).styles.display = "block" if event.checkbox.value else "none"

    @on(Button.Pressed, "#pack-browse")
    def _on_browse_pack(self) -> None:
        current = self.query_one("#pack-path-input", Input).value.strip()
        start = Path(current).parent if current else None
        self.app.push_screen(
            FilePickerScreen(
                title="选择数据包目录或 ZIP 文件",
                start=start,
                allowed_suffixes=(".zip",),
            ),
            callback=self._set_pack_path,
        )

    @on(Button.Pressed, "#output-browse")
    def _on_browse_output(self) -> None:
        current = self.query_one("#output-input", Input).value.strip()
        start = Path(current).expanduser() if current else None
        if start is not None and not start.is_dir():
            start = start.parent
        self.app.push_screen(
            FilePickerScreen(title="选择输出文件夹", start=start, directories_only=True),
            callback=self._set_output_path,
        )

    def _set_pack_path(self, path: Path | None) -> None:
        if path is not None:
            self.query_one("#pack-path-input", Input).value = str(path)

    def _set_output_path(self, path: Path | None) -> None:
        if path is not None:
            self.query_one("#output-input", Input).value = str(path)

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

    @on(Button.Pressed, "#quit-app")
    def _on_quit(self) -> None:
        self.app.exit()

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
        output = self._resolve_output()
        if output is None:
            return
        log = self.query_one("#build-log", RichLog)
        log.clear()
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
        output: Path,
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
                    output,
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
        write(f"报告：{output.resolve() / 'compatibility-report.json'}")
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
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding("q", "quit", "退出")]
    CSS = """
    Screen {
        layout: vertical;
    }
    Button {
        height: 3;
        content-align: center middle;
    }
    #migration-root, #plugins-root, #picker-root, #template-root {
        padding: 1 2;
    }
    .screen-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    #main-title {
        width: 1fr;
        padding-top: 1;
    }
    #top-bar {
        height: 3;
        align-horizontal: right;
    }
    #top-bar Button {
        margin-left: 1;
        width: 16;
    }
    .section-title {
        text-style: bold;
        padding-top: 1;
        padding-bottom: 1;
    }
    .hint {
        color: $text-muted;
        padding-top: 1;
        padding-bottom: 1;
    }
    .field-row {
        height: 3;
    }
    .field-row Input {
        width: 1fr;
        margin-right: 1;
    }
    .field-row Button {
        width: 14;
    }
    #output-subfolder {
        width: 22;
    }
    #output-subfolder-name {
        width: 1fr;
        display: none;
    }
    #target-columns, #policy-columns {
        height: auto;
    }
    #target-col-a, #target-col-b, .policy-col {
        width: 1fr;
        height: auto;
    }
    #target-col-a Checkbox, #target-col-b Checkbox, .policy-option Checkbox {
        height: 1;
        border: none;
        padding: 0;
    }
    .policy-option {
        height: auto;
        margin-bottom: 1;
    }
    .policy-option Checkbox {
        width: auto;
    }
    .policy-desc {
        color: $text-muted;
        padding-left: 1;
        text-wrap: wrap;
    }
    .button-row {
        height: 3;
        margin-top: 1;
    }
    .button-row Button {
        margin-right: 1;
    }
    #build-start {
        width: 24;
    }
    #build-log {
        height: 16;
        border: round $primary;
        margin-top: 1;
    }
    #plugin-list {
        height: 1fr;
    }
    .version-section {
        height: auto;
        margin-bottom: 1;
    }
    .version-fold {
        width: 1fr;
        height: 3;
        content-align: left middle;
        text-align: left;
    }
    .version-body {
        height: auto;
        padding-top: 1;
    }
    .plugin-item {
        width: 1fr;
        height: auto;
        content-align: left middle;
        text-align: left;
        margin-bottom: 1;
    }
    #detail-root {
        height: 1fr;
    }
    #detail-meta {
        color: $text-muted;
        padding-bottom: 1;
    }
    #detail-doc {
        height: 1fr;
        border: round $primary 40%;
        padding: 1;
    }
    #picker-tree {
        height: 1fr;
        border: round $primary;
    }
    #template-root Input, #template-root Checkbox {
        margin-bottom: 1;
    }
    """

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path

    def on_mount(self) -> None:
        """Push the migration screen as the default view."""

        self.push_screen(MigrationScreen(self._config_path))
