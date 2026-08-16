"""Textual screens for DPCompat: migration form, plugin manager, file picker.

The migration screen is the default view.  The plugins screen, the file picker,
and the template screen are pushed on top of it as modal screens.  All heavy
work (pack materialization and compilation) runs in a worker thread and reports
back through :meth:`App.call_from_thread`.

Every user-facing string is looked up through :meth:`DpCompatApp.tr` so a language
switch (``l`` key or the top-bar button) re-renders the active screens, persists the
preference, and re-localizes plugin metadata through ``PluginInfo.localized``.

Layout notes: static text widgets inside horizontal rows must be given explicit
widths (``auto`` resolves to the full row width in Textual), and plugin cards
must use ``height: auto`` instead of the container default ``1fr`` so a scroll
list does not squeeze every card to a few rows.
"""

from __future__ import annotations

import inspect
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, cast

from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Markdown,
    RichLog,
    Select,
    Static,
    Tree,
)

from ..config import ProjectConfig, load_config
from ..engine import compile_pack
from ..i18n import LANGUAGES, save_preferred_language, tr
from ..market import (
    CategoryInfo,
    MarketPlugin,
    install_market_plugin,
    list_categories,
    list_market_plugins,
)
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

# Inline formatting that would otherwise leak into the compact plugin list rows.
_MARKDOWN_STRIP = re.compile(r"(?m)^\s{0,3}#+\s*|^\s*[-*+]\s+|^\s*\d+\.\s+|`|\*\*|__|~~")


def _widget_safe(value: str) -> str:
    """Turn an arbitrary plugin/version id into a valid widget id fragment."""

    return re.sub(r"[^a-zA-Z0-9_-]", "-", value)


def _target_widget_id(game_version: str) -> str:
    return "target-" + _widget_safe(game_version)


def _strip_markdown(text: str) -> str:
    """Reduce Markdown to a one-line plain hint for compact list rows."""

    return _MARKDOWN_STRIP.sub("", text).replace("](", " (") if text else text


class LocalizedScreen:
    """Mixin giving every screen typed access to the localized application.

    ``DpCompatApp`` is declared at the bottom of this module; the forward reference
    in the cast keeps the screens usable before it is defined.  The return type is
    ``Any`` because Textual's ``MessagePump.app`` is typed ``App[object]``, which is
    incompatible with the invariant ``App[None]`` specialization.
    """

    @property
    def app(self) -> Any:
        from textual.message_pump import MessagePump

        return cast("DpCompatApp", MessagePump.app.fget(cast(Any, self)))  # type: ignore[attr-defined]

    def _t(self, key: str, **kwargs: object) -> str:
        return str(self.app.tr(key, **kwargs))

    def _set_bindings(self, bindings: list[Binding]) -> None:
        """Replace this instance's footer bindings with localized descriptions.

        ``self._bindings`` is a per-instance copy of the class-level merged map, so
        assigning it only affects this screen and survives a language switch.
        """

        self._bindings = BindingsMap(bindings)
        self.app.refresh_bindings()


class FilePickerScreen(LocalizedScreen, Screen[Path | None]):
    """Modal filesystem browser returning a directory or a matching file."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding("escape", "cancel", "取消")]

    def __init__(
        self,
        *,
        title_key: str,
        start: Path | None = None,
        allowed_suffixes: tuple[str, ...] = (),
        directories_only: bool = False,
    ) -> None:
        super().__init__()
        self._title_key = title_key
        self._start = (start or Path.cwd()).expanduser().resolve()
        self._allowed_suffixes = allowed_suffixes
        self._directories_only = directories_only

    def compose(self) -> ComposeResult:
        """Render the tree browser with pick/cancel/up controls."""

        yield Header(show_clock=False)
        with Vertical(id="picker-root"):
            yield Static(self._t(self._title_key), classes="screen-title")
            yield Static(str(self._start), id="picker-current")
            yield DirectoryTree(self._start, id="picker-tree")
            with Horizontal(classes="button-row"):
                yield Button(self._t("picker.up"), id="picker-up")
                yield Button(self._t("picker.pick"), id="picker-pick", variant="primary")
                yield Button(self._t("picker.cancel"), id="picker-cancel")
        yield Footer()

    def refresh_language(self) -> None:
        """Re-render localized labels after a language switch."""

        self.query_one("#picker-up", Button).label = self._t("picker.up")
        self.query_one("#picker-pick", Button).label = self._t("picker.pick")
        self.query_one("#picker-cancel", Button).label = self._t("picker.cancel")
        self._set_bindings([Binding("escape", "cancel", self._t("picker.cancel"))])

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
            self.notify(self._t("picker.select_first"), severity="warning")
            return
        if current.is_dir():
            self.dismiss(current)
            return
        if self._directories_only:
            self.notify(self._t("picker.directory_required"), severity="warning")
            return
        if self._allowed_suffixes and current.suffix.lower() not in self._allowed_suffixes:
            allowed = "、".join(self._allowed_suffixes)
            self.notify(self._t("picker.suffix_only", suffixes=allowed), severity="warning")
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

    def __init__(self, info: PluginInfo, language: str) -> None:
        self.info = info
        status = "●" if info.enabled else "○"
        status_style = "green" if info.enabled else "dim red"
        origin = tr(language, "plugin.origin_builtin" if info.origin == "builtin" else "plugin.origin_file")
        label = (
            f"[bold]{escape(info.name)}[/bold]  [dim]v{escape(info.version)} · {escape(origin)}[/dim]  "
            f"[{status_style}]{status}[/]"
            f"\n[dim]{escape(_strip_markdown(info.description))}[/dim]"
        )
        super().__init__(label, id=f"plugin-{_widget_safe(info.id)}", classes="plugin-item")


class VersionSection(LocalizedScreen, Vertical):
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

    def _head(self) -> str:
        format_part = (
            self._t("plugin.format", format=str(self._pack_format))
            if self._pack_format is not None
            else self._t("plugin.unregistered_version")
        )
        enabled = sum(1 for info in self._plugins if info.enabled)
        suffix = self._t(
            "plugin.section_suffix",
            count=len(self._plugins),
            enabled=enabled,
            total=len(self._plugins),
        )
        return f"{self._version}  ·  {format_part}  ·  {suffix}"

    def compose(self) -> ComposeResult:
        """Render the toggle header and the collapsible plugin body."""

        safe = _widget_safe(self._version)
        arrow = "▾" if self._expanded else "▸"
        yield Button(f"{arrow}  {self._head()}", id=f"fold-{safe}", classes="version-fold")
        with Vertical(id=f"version-body-{safe}", classes="version-body"):
            for info in self._plugins:
                yield PluginItem(info, self.app.language)

    def on_mount(self) -> None:
        """Start collapsed so the screen reads as a compact version list."""

        if not self._expanded:
            self.query_one(f"#version-body-{_widget_safe(self._version)}", Vertical).styles.display = "none"

    def refresh_language(self) -> None:
        """Update the fold header after a language switch (rows are rebuilt by the screen)."""

        self.query_one(f"#fold-{_widget_safe(self._version)}", Button).label = (
            ("▾" if self._expanded else "▸") + "  " + self._head()
        )

    @on(Button.Pressed, ".version-fold")
    def _toggle(self, event: Button.Pressed) -> None:
        """Expand or collapse this version's plugin rows."""

        self._expanded = not self._expanded
        body = self.query_one(f"#version-body-{_widget_safe(self._version)}", Vertical)
        body.styles.display = "block" if self._expanded else "none"
        event.button.label = ("▾" if self._expanded else "▸") + "  " + self._head()
        self.post_message(self.ExpansionChanged(self._version, self._expanded))

    @on(Button.Pressed, ".plugin-item")
    def _on_plugin_pressed(self, event: Button.Pressed) -> None:
        """Forward a plugin row press so the screen can open the detail page."""

        if isinstance(event.button, PluginItem):
            self.post_message(self.PluginSelected(event.button.info))


class PluginDetailScreen(LocalizedScreen, Screen[None]):
    """Detail page for one plugin: metadata, enable toggle, and its Markdown docs."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self, info: PluginInfo, store: PluginStore) -> None:
        super().__init__()
        self._info = info
        self._store = store

    def _display_info(self) -> PluginInfo:
        """Return the plugin metadata localized to the current UI language."""

        return self._info.localized(self.app.language)

    def _type_part(self, info: PluginInfo) -> str:
        kind = {
            "python": self._t("plugin.kind_python"),
            "declarative": self._t("plugin.kind_declarative"),
            "builtin": self._t("plugin.kind_builtin"),
        }[info.kind]
        if info.origin == "file":
            return self._t("plugin.detail_origin_file", kind=kind)
        return self._t("plugin.detail_origin_builtin")

    def _detail_widgets(self, info: PluginInfo) -> list[Widget]:
        """Build the localized body widgets for the detail page."""

        widgets: list[Widget] = [
            Static(info.name, classes="screen-title"),
            Static(
                self._t(
                    "plugin.detail_meta",
                    id=escape(info.id),
                    version=escape(info.version),
                    origin=self._type_part(info),
                    target=escape(info.target_version),
                ),
                id="detail-meta",
            ),
        ]
        row = Horizontal(classes="button-row")
        row.compose_add_child(
            Button(
                self._t("plugin.disable" if info.enabled else "plugin.enable"),
                id="detail-toggle",
                variant="primary",
            )
        )
        if info.origin == "file":
            row.compose_add_child(Button(self._t("plugin.uninstall"), id="detail-remove", variant="error"))
        row.compose_add_child(Button(self._t("plugin.back"), id="detail-back"))
        widgets.append(row)
        if info.readme:
            widgets.append(Markdown(info.readme, id="detail-doc"))
        else:
            widgets.append(Markdown(info.description, id="detail-doc"))
            widgets.append(Static(self._t("plugin.no_readme_hint"), classes="hint"))
        return widgets

    def compose(self) -> ComposeResult:
        """Render the plugin header, actions, and the Markdown documentation."""

        info = self._display_info()
        yield Header(show_clock=False)
        with Vertical(id="detail-root"):
            yield from self._detail_widgets(info)
        yield Footer()

    def refresh_language(self) -> None:
        """Rebuild the whole page with the newly selected language."""

        root = self.query_one("#detail-root", Vertical)
        root.remove_children()
        for widget in self._detail_widgets(self._display_info()):
            root.mount(widget)
        self._set_bindings([Binding("escape", "app.pop_screen", self._t("plugin.back"))])

    @on(Button.Pressed, "#detail-toggle")
    def _on_toggle(self) -> None:
        """Flip the persisted enable state and refresh the toggle label."""

        enabled = not self._info.enabled
        self._store.set_enabled(self._info.id, enabled)
        self._info = self._info.model_copy(update={"enabled": enabled})
        self.query_one("#detail-toggle", Button).label = self._t("plugin.disable" if enabled else "plugin.enable")
        key = "plugin.enabled_notify" if enabled else "plugin.disabled_notify"
        self.notify(self._t(key, name=self._display_info().name))

    @on(Button.Pressed, "#detail-remove")
    def _on_remove(self) -> None:
        """Uninstall this file plugin and return to the plugin list."""

        try:
            self._store.uninstall(self._info.id)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(self._t("plugin.uninstalled_notify", id=self._info.id))
        self.app.pop_screen()

    @on(Button.Pressed, "#detail-back")
    def _on_back(self) -> None:
        """Return to the plugin list."""

        self.app.pop_screen()


class TemplateScreen(LocalizedScreen, Screen[Path | None]):
    """Ask for a plugin template name and whether to create a subfolder."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding("escape", "cancel", "取消")]

    def __init__(self, location: Path) -> None:
        super().__init__()
        self._location = location

    def compose(self) -> ComposeResult:
        """Render the template name form."""

        yield Header(show_clock=False)
        with Vertical(id="template-root"):
            yield Static(self._t("template.title"), classes="screen-title")
            yield Static(self._t("template.location", path=str(self._location)), id="template-location")
            yield Input(placeholder=self._t("template.name_placeholder"), id="template-name")
            yield Checkbox(self._t("template.subfolder"), id="template-subfolder")
            with Horizontal(classes="button-row"):
                yield Button(self._t("template.create"), id="template-create", variant="success")
                yield Button(self._t("template.cancel"), id="template-cancel")
        yield Footer()

    def refresh_language(self) -> None:
        """Update labels after a language switch."""

        self.query_one("#template-location", Static).update(self._t("template.location", path=str(self._location)))
        self.query_one("#template-name", Input).placeholder = self._t("template.name_placeholder")
        self.query_one("#template-subfolder", Checkbox).label = self._t("template.subfolder")
        self.query_one("#template-create", Button).label = self._t("template.create")
        self.query_one("#template-cancel", Button).label = self._t("template.cancel")
        self._set_bindings([Binding("escape", "cancel", self._t("template.cancel"))])

    def action_cancel(self) -> None:
        """Close the template screen without creating anything."""

        self.dismiss(None)

    def _create(self) -> None:
        name = self.query_one("#template-name", Input).value.strip()
        if not _TEMPLATE_NAME_RE.fullmatch(name):
            self.notify(self._t("template.invalid_name"), severity="error")
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


class PluginsScreen(LocalizedScreen, Screen[None]):
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
            yield Static(self._t("plugins.title"), classes="screen-title")
            yield Static(self._t("plugins.hint"), classes="hint")
            yield VerticalScroll(id="plugin-list")
            with Horizontal(classes="button-row"):
                yield Button(self._t("plugins.install"), id="plugins-install", variant="primary")
                yield Button(self._t("market.open"), id="plugins-market", variant="primary")
                yield Button(self._t("plugins.template"), id="plugins-template", variant="primary")
                yield Button(self._t("plugins.refresh"), id="plugins-refresh")
                yield Button(self._t("plugins.back"), id="plugins-back")
        yield Footer()

    def refresh_language(self) -> None:
        """Re-render headers and rebuild the list in the new language."""

        self.query_one("#plugins-install", Button).label = self._t("plugins.install")
        self.query_one("#plugins-market", Button).label = self._t("market.open")
        self.query_one("#plugins-template", Button).label = self._t("plugins.template")
        self.query_one("#plugins-refresh", Button).label = self._t("plugins.refresh")
        self.query_one("#plugins-back", Button).label = self._t("plugins.back")
        self._set_bindings([Binding("escape", "app.pop_screen", self._t("plugins.back"))])
        self._refresh()

    def on_mount(self) -> None:
        """Load the plugin store; the list renders on screen resume."""

        self._store = PluginStore()

    def on_screen_resume(self) -> None:
        """Render (or re-render) the list whenever this screen becomes active.

        Textual only invokes ``push_screen`` callbacks on ``dismiss``, not on
        ``pop_screen``, so returning from the detail page or the marketplace
        needs this resume hook to pick up installs, toggles, and uninstalls.
        """

        self._refresh()

    def _refresh(self) -> None:
        """Rebuild the list, grouping plugins by their declared target version."""

        box = self.query_one("#plugin-list", VerticalScroll)
        box.remove_children()
        assert self._store is not None
        infos = [info.localized(self.app.language) for info in self._store.list_plugins()]
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
        self.notify(self._t("plugins.installed_notify", name=info.name, id=info.id))
        self._refresh()

    def _template_location(self, location: Path | None) -> None:
        if location is None:
            return
        self.app.push_screen(TemplateScreen(location), callback=self._template_result)

    def _template_result(self, created: Path | None) -> None:
        if created is None:
            return
        self.notify(self._t("plugins.template_created", path=str(created)))
        self._refresh()

    @on(Button.Pressed, "#plugins-install")
    def _on_install(self) -> None:
        self.app.push_screen(
            FilePickerScreen(
                title_key="picker.plugin_file",
                allowed_suffixes=(".py", ".json"),
            ),
            callback=self._install_flow,
        )

    @on(Button.Pressed, "#plugins-market")
    def _on_market(self) -> None:
        self.app.push_screen(MarketScreen())

    @on(Button.Pressed, "#plugins-template")
    def _on_template(self) -> None:
        self.app.push_screen(
            FilePickerScreen(title_key="picker.template_location", directories_only=True),
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
        """Open the detail page for the selected plugin."""

        assert self._store is not None
        self.app.push_screen(PluginDetailScreen(event.info, self._store))


class MarketRow(Button):
    """One marketplace plugin row: name, origin repo, target, and description."""

    def __init__(self, plugin: MarketPlugin, language: str, installed: bool) -> None:
        self.plugin = plugin
        info = plugin.info.localized(language)
        mark = tr(language, "market.installed_mark") if installed else ""
        top = tr(
            language,
            "market.row",
            name=escape(info.name),
            target=escape(info.target_version),
            repo=escape(plugin.repo),
        )
        label = f"[bold]{top}[/bold]{escape(mark)}\n[dim]{escape(_strip_markdown(info.description))}[/dim]"
        super().__init__(label, id=f"market-{_widget_safe(plugin.info.id)}", classes="plugin-item")


class MarketScreen(LocalizedScreen, Screen[None]):
    """Browse and install plugins from the registered repositories."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self) -> None:
        super().__init__()
        self._plugins: list[MarketPlugin] = []
        self._categories: list[CategoryInfo] = []
        self._installed: set[str] = set()
        self._load_error: str | None = None
        self._suppress_category = False

    def compose(self) -> ComposeResult:
        """Render the search bar, category filter, and plugin list."""

        yield Header(show_clock=False)
        with Vertical(id="market-root"):
            yield Static(self._t("market.title"), classes="screen-title", id="market-title")
            yield Static(self._t("market.hint"), classes="hint", id="market-hint")
            with Horizontal(classes="field-row"):
                yield Input(placeholder=self._t("market.search_placeholder"), id="market-search")
                yield Button(self._t("market.search"), id="market-search-go", variant="primary")
            yield Select(
                [(self._t("market.category_all"), "")],
                id="market-category",
                classes="market-category",
            )
            yield VerticalScroll(id="market-list")
            with Horizontal(classes="button-row"):
                yield Button(self._t("market.refresh"), id="market-refresh")
                yield Button(self._t("market.back"), id="market-back")
        yield Footer()

    def refresh_language(self) -> None:
        """Re-render labels and rebuild the list in the new language."""

        self.query_one("#market-title", Static).update(self._t("market.title"))
        self.query_one("#market-hint", Static).update(self._t("market.hint"))
        self.query_one("#market-search", Input).placeholder = self._t("market.search_placeholder")
        self.query_one("#market-search-go", Button).label = self._t("market.search")
        self.query_one("#market-refresh", Button).label = self._t("market.refresh")
        self.query_one("#market-back", Button).label = self._t("market.back")
        self._set_bindings([Binding("escape", "app.pop_screen", self._t("market.back"))])
        self.call_later(self._render_list)

    def on_screen_resume(self) -> None:
        """Load the marketplace whenever this screen becomes active.

        Reloads on the first push and again when returning from the detail page
        so freshly installed plugins get their installed mark.
        """

        self._reload()

    def _reload(self) -> None:
        # Capture widget state on the main thread; the worker only fetches.
        query = self.query_one("#market-search", Input).value.strip() or None
        selected = self.query_one("#market-category", Select).value
        category = "" if selected in (None, Select.BLANK) else str(selected)
        self._load_error = None
        self.run_worker(
            self._load_task(query, category or None),
            thread=True,
            exclusive=True,
            group="market",
        )

    async def _load_task(self, query: str | None, category: str | None) -> None:
        from ..plugins import PluginStore

        try:
            categories = list_categories()
            plugins = list_market_plugins(category=category, query=query)
            installed = {info.id for info in PluginStore().list_plugins()}
        except Exception as exc:
            self.app.call_from_thread(self._apply_error, str(exc))
            return
        self.app.call_from_thread(self._apply_loaded, categories, plugins, installed)

    async def _apply_error(self, message: str) -> None:
        self._load_error = message
        self.notify(self._t("market.load_failed", error=message), severity="error")
        await self._render_list()

    async def _apply_loaded(
        self,
        categories: list[CategoryInfo],
        plugins: list[MarketPlugin],
        installed: set[str],
    ) -> None:
        self._categories = categories
        self._plugins = plugins
        self._installed = installed
        self._suppress_category = True
        try:
            self.query_one("#market-category", Select).set_options(
                [
                    (self._t("market.category_all"), ""),
                    *[(category.display_name or category.id, category.id) for category in categories],
                ]
            )
        finally:
            self._suppress_category = False
        await self._render_list()

    async def _render_list(self) -> None:
        box = self.query_one("#market-list", VerticalScroll)
        await box.remove_children()
        if self._load_error is not None:
            await box.mount(Static(self._t("market.load_failed", error=self._load_error), classes="hint"))
            return
        if not self._plugins:
            await box.mount(Static(self._t("market.empty"), classes="hint"))
            return
        for plugin in self._plugins:
            await box.mount(MarketRow(plugin, self.app.language, plugin.info.id in self._installed))

    def _on_search(self) -> None:
        self._reload()

    @on(Input.Submitted, "#market-search")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        self._on_search()

    @on(Button.Pressed, "#market-search-go")
    def _on_search_go(self, event: Button.Pressed) -> None:
        self._on_search()

    @on(Select.Changed, "#market-category")
    def _on_category(self, event: Select.Changed) -> None:
        if self._suppress_category:
            return
        self._reload()

    @on(Button.Pressed, "#market-refresh")
    def _on_refresh(self, event: Button.Pressed) -> None:
        self._reload()

    @on(Button.Pressed, "#market-back")
    def _on_back(self, event: Button.Pressed) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, ".plugin-item")
    def _on_plugin(self, event: Button.Pressed) -> None:
        if not isinstance(event.button, MarketRow):
            return
        self.app.push_screen(MarketDetailScreen(event.button.plugin, PluginStore()))


class MarketDetailScreen(LocalizedScreen, Screen[None]):
    """Marketplace plugin detail: metadata, Markdown docs, and install action."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "app.pop_screen", "返回")
    ]

    def __init__(self, plugin: MarketPlugin, store: PluginStore) -> None:
        super().__init__()
        self._plugin = plugin
        self._store = store
        self._installing = False

    def _display_info(self) -> PluginInfo:
        return self._plugin.info.localized(self.app.language)

    def _is_installed(self) -> bool:
        return self._plugin.info.id in {item.id for item in self._store.list_plugins()}

    def _meta_lines(self, info: PluginInfo) -> list[Static]:
        meta = self._plugin.meta
        lines = [
            Static(
                self._t(
                    "market.detail_meta",
                    id=escape(info.id),
                    version=escape(info.version),
                    target=escape(info.target_version),
                    repo=escape(self._plugin.repo),
                ),
                id="market-detail-meta",
            )
        ]
        if meta.author:
            lines.append(
                Static(self._t("market.detail_author", author=escape(meta.author)), classes="market-meta-line")
            )
        if meta.license:
            lines.append(
                Static(self._t("market.detail_license", license=escape(meta.license)), classes="market-meta-line")
            )
        if meta.homepage:
            lines.append(
                Static(self._t("market.detail_homepage", homepage=escape(meta.homepage)), classes="market-meta-line")
            )
        if meta.tags:
            lines.append(
                Static(
                    self._t("market.detail_tags", tags=", ".join(escape(tag) for tag in meta.tags)),
                    classes="market-meta-line",
                )
            )
        return lines

    def _detail_widgets(self, info: PluginInfo) -> list[Widget]:
        widgets: list[Widget] = [Static(info.name, classes="screen-title"), *self._meta_lines(info)]
        if info.readme:
            widgets.append(Markdown(info.readme, id="market-doc"))
        else:
            widgets.append(Markdown(info.description, id="market-doc"))
            widgets.append(Static(self._t("plugin.no_readme_hint"), classes="hint"))
        row = Horizontal(classes="button-row")
        installed = self._is_installed()
        row.compose_add_child(
            Button(
                self._t("market.installed" if installed else "market.install"),
                id="market-install",
                variant="primary",
                disabled=installed or self._installing,
            )
        )
        if installed:
            row.compose_add_child(Static(self._t("market.installed_hint"), classes="hint"))
        row.compose_add_child(Button(self._t("market.back"), id="market-back"))
        widgets.append(row)
        return widgets

    def compose(self) -> ComposeResult:
        """Render the plugin header, actions, and the Markdown documentation."""

        info = self._display_info()
        yield Header(show_clock=False)
        with Vertical(id="market-detail-root"):
            yield from self._detail_widgets(info)
        yield Footer()

    def refresh_language(self) -> None:
        """Rebuild the whole page with the newly selected language."""

        root = self.query_one("#market-detail-root", Vertical)
        root.remove_children()
        for widget in self._detail_widgets(self._display_info()):
            root.mount(widget)
        self._set_bindings([Binding("escape", "app.pop_screen", self._t("market.back"))])

    @on(Button.Pressed, "#market-install")
    def _on_install(self) -> None:
        self._installing = True
        self.query_one("#market-install", Button).disabled = True
        self.run_worker(self._install_task(), thread=True, exclusive=True, group="market-install")

    async def _install_task(self) -> None:
        try:
            info = install_market_plugin(self._plugin.info.id, self._store, repo_name=self._plugin.repo)
        except Exception as exc:
            self.app.call_from_thread(self.notify, self._t("market.install_failed", error=exc), severity="error")
            self.app.call_from_thread(self._reset_install_button)
            return
        self.app.call_from_thread(self._install_done, info.name, info.id)

    def _install_done(self, name: str, plugin_id: str) -> None:
        if not self.is_attached:  # the user may have already returned to the marketplace
            return
        self.notify(self._t("market.install_done", name=name, id=plugin_id))
        button = self.query_one("#market-install", Button)
        button.label = self._t("market.installed")
        button.disabled = True
        self._installing = False

    def _reset_install_button(self) -> None:
        button = self.query_one("#market-install", Button)
        button.disabled = False
        self._installing = False

    @on(Button.Pressed, "#market-back")
    def _on_back(self) -> None:
        self.app.pop_screen()


class MigrationScreen(LocalizedScreen, Screen[None]):
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
                yield Static(self._t("migration.title"), classes="screen-title", id="main-title")
                yield Button(self._t("app.language_label", name=LANGUAGES[self.app.language]), id="lang-switch")
                yield Button(self._t("migration.plugins"), id="open-plugins", variant="primary")
                yield Button(self._t("migration.quit"), id="quit-app", variant="error")
            yield Static(self._t("migration.pack_section"), classes="section-title", id="pack-section")
            with Horizontal(classes="field-row"):
                yield Input(
                    placeholder=self._t("migration.pack_placeholder"),
                    id="pack-path-input",
                )
                yield Button(self._t("migration.browse"), id="pack-browse", variant="primary")
            yield Static(self._t("migration.output_section"), classes="section-title", id="output-section")
            with Horizontal(classes="field-row"):
                yield Input(value="dist", id="output-input", placeholder=self._t("migration.output_placeholder"))
                yield Button(self._t("migration.browse"), id="output-browse", variant="primary")
            with Horizontal(classes="field-row"):
                yield Checkbox(self._t("migration.output_subfolder"), id="output-subfolder")
                yield Input(placeholder=self._t("migration.subfolder_placeholder"), id="output-subfolder-name")
            yield Static(self._t("migration.targets_section"), classes="section-title", id="targets-section")
            yield Static(self._t("migration.targets_hint"), classes="hint", id="targets-hint")
            with Horizontal(id="target-columns"):
                with Vertical(id="target-col-a"):
                    for profile in PROFILES[: len(PROFILES) // 2]:
                        yield Checkbox(
                            self._target_label(profile),
                            value=True,
                            id=_target_widget_id(profile.game_version),
                            classes="-textual-compact",
                        )
                with Vertical(id="target-col-b"):
                    for profile in PROFILES[len(PROFILES) // 2 :]:
                        yield Checkbox(
                            self._target_label(profile),
                            value=True,
                            id=_target_widget_id(profile.game_version),
                            classes="-textual-compact",
                        )
            with Horizontal(classes="button-row"):
                yield Button(self._t("migration.targets_all"), id="targets-all", variant="primary")
                yield Button(self._t("migration.targets_none"), id="targets-none")
            yield Static(self._t("migration.policy_section"), classes="section-title", id="policy-section")
            with Horizontal(id="policy-columns"):
                with Vertical(classes="policy-col"):
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            self._t("migration.policy_emulated"),
                            value=True,
                            id="policy-emulated",
                            classes="-textual-compact",
                        )
                        yield Static(
                            self._t("migration.policy_emulated_desc"),
                            classes="policy-desc",
                            id="policy-emulated-desc",
                        )
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            self._t("migration.policy_lossy"),
                            id="policy-lossy",
                            classes="-textual-compact",
                        )
                        yield Static(
                            self._t("migration.policy_lossy_desc"),
                            classes="policy-desc",
                            id="policy-lossy-desc",
                        )
                with Vertical(classes="policy-col"):
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            self._t("migration.policy_unknown"),
                            id="policy-unknown",
                            classes="-textual-compact",
                        )
                        yield Static(
                            self._t("migration.policy_unknown_desc"),
                            classes="policy-desc",
                            id="policy-unknown-desc",
                        )
                    with Vertical(classes="policy-option"):
                        yield Checkbox(
                            self._t("migration.policy_fail_warnings"),
                            id="policy-fail-warnings",
                            classes="-textual-compact",
                        )
                        yield Static(
                            self._t("migration.policy_fail_warnings_desc"),
                            classes="policy-desc",
                            id="policy-fail-warnings-desc",
                        )
            yield Static(self._t("migration.policy_hint"), classes="hint", id="policy-hint")
            with Horizontal(classes="button-row"):
                yield Button(self._t("migration.build"), id="build-start", variant="success")
            yield Static(self._t("migration.log_section"), classes="section-title", id="log-section")
            yield RichLog(id="build-log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def _target_label(self, profile: VersionProfile) -> str:
        return self._t("migration.target_format", version=profile.game_version, format=str(profile.pack_format))

    def refresh_language(self) -> None:
        """Update every label in place after a language switch."""

        self.query_one("#main-title", Static).update(self._t("migration.title"))
        self.query_one("#lang-switch", Button).label = self._t(
            "app.language_label",
            name=LANGUAGES[self.app.language],
        )
        self.query_one("#open-plugins", Button).label = self._t("migration.plugins")
        self.query_one("#quit-app", Button).label = self._t("migration.quit")
        self.query_one("#pack-path-input", Input).placeholder = self._t("migration.pack_placeholder")
        self.query_one("#pack-browse", Button).label = self._t("migration.browse")
        self.query_one("#output-input", Input).placeholder = self._t("migration.output_placeholder")
        self.query_one("#output-browse", Button).label = self._t("migration.browse")
        self.query_one("#output-subfolder", Checkbox).label = self._t("migration.output_subfolder")
        self.query_one("#output-subfolder-name", Input).placeholder = self._t("migration.subfolder_placeholder")
        self.query_one("#targets-all", Button).label = self._t("migration.targets_all")
        self.query_one("#targets-none", Button).label = self._t("migration.targets_none")
        self.query_one("#policy-emulated", Checkbox).label = self._t("migration.policy_emulated")
        self.query_one("#policy-lossy", Checkbox).label = self._t("migration.policy_lossy")
        self.query_one("#policy-unknown", Checkbox).label = self._t("migration.policy_unknown")
        self.query_one("#policy-fail-warnings", Checkbox).label = self._t("migration.policy_fail_warnings")
        self.query_one("#build-start", Button).label = self._t("migration.build")
        self._set_bindings([Binding("p", "open_plugins", self._t("migration.plugins"))])

        # Section titles, hints, and policy descriptions are addressed by stable ids.
        for widget_id, key in (
            ("#pack-section", "migration.pack_section"),
            ("#output-section", "migration.output_section"),
            ("#targets-section", "migration.targets_section"),
            ("#policy-section", "migration.policy_section"),
            ("#log-section", "migration.log_section"),
            ("#targets-hint", "migration.targets_hint"),
            ("#policy-emulated-desc", "migration.policy_emulated_desc"),
            ("#policy-lossy-desc", "migration.policy_lossy_desc"),
            ("#policy-unknown-desc", "migration.policy_unknown_desc"),
            ("#policy-fail-warnings-desc", "migration.policy_fail_warnings_desc"),
            ("#policy-hint", "migration.policy_hint"),
        ):
            self.query_one(widget_id, Static).update(self._t(key))

        for profile in PROFILES:
            self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox).label = self._target_label(profile)

        log = self.query_one("#build-log", RichLog)
        if not log.lines:
            log.write(f"[dim]{self._t('migration.log_hint')}[/dim]")

    def on_mount(self) -> None:
        """Load the optional config, apply its target defaults, and hint at the log."""

        self._config = load_config(self._config_path) if self._config_path else ProjectConfig()
        assert self._config is not None
        if self._config.targets:
            selected = set(self._config.targets)
            for profile in PROFILES:
                checkbox = self.query_one(f"#{_target_widget_id(profile.game_version)}", Checkbox)
                checkbox.value = profile.game_version in selected
        self.query_one("#build-log", RichLog).write(f"[dim]{self._t('migration.log_hint')}[/dim]")

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
                self.notify(self._t("migration.invalid_subfolder"), severity="error")
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
                title_key="picker.pack",
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
            FilePickerScreen(title_key="picker.output", start=start, directories_only=True),
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

    @on(Button.Pressed, "#lang-switch")
    def _on_lang_switch(self) -> None:
        self.app.action_cycle_language()

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
            self.notify(self._t("migration.need_pack"), severity="error")
            return
        targets = self._selected_targets()
        if not targets:
            self.notify(self._t("migration.need_target"), severity="error")
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
        app = self.app

        def write(line: str) -> None:
            app.call_from_thread(log.write, line)

        write(f"[bold cyan]{app.tr('migration.build_started')}[/bold cyan]")
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
            message = app.tr("migration.build_failed", error=exc)
            write(f"[bold red]{escape(message)}[/bold red]")
            app.call_from_thread(self.notify, message, severity="error")
            return

        write(
            app.tr(
                "migration.source_line",
                format=detection.source_format,
                candidates=", ".join(detection.candidates) or "—",
            )
        )
        for diagnostic in detection.diagnostics:
            write(self._diagnostic_line(diagnostic))
        for result in results:
            status = app.tr("migration.status_ok" if result.successful else "migration.status_failed")
            style = "bold green" if result.successful else "bold red"
            artifact = result.archive.name if result.archive else (result.sha256 or "—")
            version_part = app.tr(
                "migration.target_format",
                version=result.profile.game_version,
                format=result.profile.pack_format,
            )
            summary = f"[{style}]{escape(status)}[/{style}] {escape(version_part)}"
            write(f"{summary} {escape(artifact)}")
            for diagnostic in result.diagnostics:
                write(self._diagnostic_line(diagnostic))
        if universal:
            write(f"[bold]{escape(app.tr('migration.universal_line', path=str(universal)))}[/bold]")
        write(app.tr("migration.report_line", path=str(output.resolve() / "compatibility-report.json")))
        app.call_from_thread(self.notify, app.tr("migration.build_done"), severity="information")

    @staticmethod
    def _diagnostic_line(diagnostic: Diagnostic) -> str:
        location = diagnostic.path or ""
        if diagnostic.line:
            location += f":{diagnostic.line}"
        style = "red" if diagnostic.severity.value >= 30 else "yellow" if diagnostic.severity.value >= 20 else "cyan"
        return f"[{style}]{escape(diagnostic.code)}[/{style}] {escape(location)} {escape(diagnostic.message)}"


class DpCompatApp(App[None]):
    """Textual application tying the migration and plugin screens together."""

    TITLE = "DPCompat"
    SUB_TITLE = "数据包兼容性迁移工具"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "退出"),
        Binding("l", "cycle_language", "语言"),
    ]
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
    #lang-switch {
        width: 22;
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
    .market-category {
        width: 1fr;
        margin-top: 1;
        margin-bottom: 1;
    }
    .market-meta-line {
        color: $text-muted;
    }
    #market-doc {
        height: 1fr;
        border: round $primary 40%;
        padding: 1;
    }
    #template-root Input, #template-root Checkbox {
        margin-bottom: 1;
    }
    """

    def __init__(self, config_path: Path | None = None, language: str | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        from ..i18n import resolve_language

        self._language = resolve_language(language)

    @property
    def language(self) -> str:
        """The currently selected UI language code."""

        return self._language

    def tr(self, key: str, **kwargs: object) -> str:
        """Translate ``key`` into the current UI language."""

        return tr(self._language, key, **kwargs)

    async def action_cycle_language(self) -> None:
        """Switch to the next UI language, persist it, and re-render every screen."""

        codes = list(LANGUAGES)
        self._language = codes[(codes.index(self._language) + 1) % len(codes)]
        with suppress(OSError):  # Read-only home directory must not break language switching.
            save_preferred_language(self._language)
        self.sub_title = self.tr("app.subtitle")
        self._bindings = BindingsMap(
            [
                Binding("q", "quit", self.tr("app.quit")),
                Binding("l", "cycle_language", self.tr("app.language")),
            ]
        )
        self.notify(self.tr("app.language_switched", name=LANGUAGES[self._language]))
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_language", None)
            if refresh is not None:
                result = refresh()
                if inspect.isawaitable(result):
                    await result
        self.refresh_bindings()

    def on_mount(self) -> None:
        """Push the migration screen as the default view."""

        self.sub_title = self.tr("app.subtitle")
        self.push_screen(MigrationScreen(self._config_path))
