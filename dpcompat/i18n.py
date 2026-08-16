"""UI localization: language registry, translation table, and preference persistence.

The TUI is the only consumer of this module today.  Every user-facing string in
``dpcompat/ui`` must be looked up through :func:`tr` so a language switch re-renders
the whole interface.  The preferred language comes from ``DPCOMPAT_LANG`` (highest
priority), then the user preference file ``~/.dpcompat/prefs.toml``, then the default.

Plugin-provided text (names, descriptions, readmes) is *not* translated here; plugins
declare their own ``localizations`` map (see ``dpcompat.plugins``) and the TUI resolves
it against the currently selected language.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULT_LANGUAGE = "zh-CN"
ENV_LANGUAGE = "DPCOMPAT_LANG"
PREFS_DIR = Path.home() / ".dpcompat"
PREFS_FILE = PREFS_DIR / "prefs.toml"

#: Language code -> native display name, in the order the TUI cycles through them.
LANGUAGES: dict[str, str] = {
    "zh-CN": "简体中文",
    "en": "English",
}

#: Translation table.  Missing entries fall back to ``zh-CN`` and then to the key itself.
TRANSLATIONS: dict[str, dict[str, str]] = {
    # -- file picker -----------------------------------------------------------
    "picker.cancel": {"zh-CN": "取消", "en": "Cancel"},
    "picker.up": {"zh-CN": "上级目录", "en": "Up one level"},
    "picker.pick": {"zh-CN": "选择当前项", "en": "Choose current item"},
    "picker.select_first": {"zh-CN": "请先在目录树中选择一项", "en": "Select an item in the tree first"},
    "picker.directory_required": {"zh-CN": "这里需要选择一个文件夹", "en": "A directory is required here"},
    "picker.suffix_only": {"zh-CN": "此处只能选择 {suffixes} 文件", "en": "Only {suffixes} files can be chosen here"},
    "picker.plugin_file": {
        "zh-CN": "选择插件文件（.py 或 .json）",
        "en": "Choose plugin file (.py or .json)",
    },
    "picker.template_location": {
        "zh-CN": "选择插件模板位置（文件夹）",
        "en": "Choose plugin template location (folder)",
    },
    "picker.pack": {"zh-CN": "选择数据包目录或 ZIP 文件", "en": "Choose data pack directory or ZIP file"},
    "picker.output": {"zh-CN": "选择输出文件夹", "en": "Choose output folder"},
    # -- plugin rows -----------------------------------------------------------
    "plugin.origin_builtin": {"zh-CN": "内置", "en": "built-in"},
    "plugin.origin_file": {"zh-CN": "文件", "en": "file"},
    "plugin.section_suffix": {
        "zh-CN": "{count} 个插件 · {enabled}/{total} 已启用",
        "en": "{count} plugin(s) · {enabled}/{total} enabled",
    },
    "plugin.format": {"zh-CN": "格式 {format}", "en": "format {format}"},
    "plugin.unregistered_version": {"zh-CN": "未注册版本", "en": "unregistered version"},
    # -- plugin detail ----------------------------------------------------------
    "plugin.kind_python": {"zh-CN": "Python", "en": "Python"},
    "plugin.kind_declarative": {"zh-CN": "声明式", "en": "declarative"},
    "plugin.kind_builtin": {"zh-CN": "内置", "en": "built-in"},
    "plugin.detail_origin_file": {"zh-CN": "文件插件（{kind}）", "en": "file plugin ({kind})"},
    "plugin.detail_origin_builtin": {"zh-CN": "内置插件", "en": "built-in plugin"},
    "plugin.detail_meta": {
        "zh-CN": "{id} · v{version} · {origin} · 目标 {target}",
        "en": "{id} · v{version} · {origin} · target {target}",
    },
    "plugin.enable": {"zh-CN": "启用", "en": "Enable"},
    "plugin.disable": {"zh-CN": "禁用", "en": "Disable"},
    "plugin.uninstall": {"zh-CN": "卸载", "en": "Uninstall"},
    "plugin.back": {"zh-CN": "返回", "en": "Back"},
    "plugin.no_readme_hint": {
        "zh-CN": "该插件没有提供 Markdown 说明文档，只有上面的简短描述。",
        "en": "This plugin ships no Markdown documentation, only the short description above.",
    },
    "plugin.enabled_notify": {"zh-CN": "已启用插件：{name}", "en": "Enabled plugin: {name}"},
    "plugin.disabled_notify": {"zh-CN": "已禁用插件：{name}", "en": "Disabled plugin: {name}"},
    "plugin.uninstalled_notify": {"zh-CN": "已卸载插件：{id}", "en": "Uninstalled plugin: {id}"},
    # -- template screen ---------------------------------------------------------
    "template.title": {"zh-CN": "创建插件模板", "en": "Create plugin template"},
    "template.location": {"zh-CN": "位置：{path}", "en": "Location: {path}"},
    "template.name_placeholder": {
        "zh-CN": "插件名称（小写字母/数字/._-）",
        "en": "Plugin name (lowercase letters/digits/._-)",
    },
    "template.subfolder": {
        "zh-CN": "在所选文件夹下创建同名子文件夹",
        "en": "Create a subfolder with the same name",
    },
    "template.create": {"zh-CN": "创建", "en": "Create"},
    "template.invalid_name": {
        "zh-CN": "插件名称只能包含小写字母、数字、'.'、'_'、'-'，且不能以数字开头",
        "en": "Plugin name may only contain lowercase letters, digits, '.', '_', '-', and must not start with a digit",
    },
    # -- plugins screen -------------------------------------------------------------
    "plugins.title": {"zh-CN": "插件管理", "en": "Plugin Manager"},
    "plugins.hint": {
        "zh-CN": "插件按目标版本分组：点击版本行展开该版本的插件列表，点击插件行查看它的完整文档、启用/禁用或卸载。",
        "en": "Plugins are grouped by target version: click a version row to expand its plugins; "
        "click a plugin row for its full documentation, enable/disable, or uninstall.",
    },
    "plugins.install": {"zh-CN": "安装插件文件...", "en": "Install plugin file..."},
    "plugins.template": {"zh-CN": "创建插件模板...", "en": "Create plugin template..."},
    "plugins.refresh": {"zh-CN": "刷新", "en": "Refresh"},
    "plugins.back": {"zh-CN": "返回", "en": "Back"},
    "plugins.installed_notify": {"zh-CN": "已安装插件：{name} ({id})", "en": "Installed plugin: {name} ({id})"},
    "plugins.template_created": {"zh-CN": "插件模板已创建：{path}", "en": "Plugin template created: {path}"},
    # -- migration screen ---------------------------------------------------------------
    "migration.title": {"zh-CN": "数据包兼容性迁移", "en": "Data-pack Compatibility Migration"},
    "migration.plugins": {"zh-CN": "插件管理", "en": "Plugin Manager"},
    "migration.quit": {"zh-CN": "退出", "en": "Quit"},
    "migration.pack_section": {"zh-CN": "数据包", "en": "Data pack"},
    "migration.pack_placeholder": {"zh-CN": "数据包目录或 ZIP 文件路径", "en": "Data pack directory or ZIP file path"},
    "migration.browse": {"zh-CN": "浏览...", "en": "Browse..."},
    "migration.output_section": {"zh-CN": "输出", "en": "Output"},
    "migration.output_placeholder": {
        "zh-CN": "输出目录（相对当前目录）",
        "en": "Output directory (relative to current directory)",
    },
    "migration.output_subfolder": {"zh-CN": "创建子文件夹", "en": "Create subfolder"},
    "migration.subfolder_placeholder": {
        "zh-CN": "子文件夹名称（字母/数字/._-）",
        "en": "Subfolder name (letters/digits/._-)",
    },
    "migration.targets_section": {
        "zh-CN": "目标版本（勾选要迁移到的版本）",
        "en": "Target versions (check the ones to migrate to)",
    },
    "migration.targets_hint": {
        "zh-CN": "目标版本由已注册的正式发布自动生成；每个版本对应的迁移插件可在“插件管理”中按版本查看与开关。",
        "en": "Targets are generated from registered stable releases; per-version migration plugins "
        "can be browsed and toggled in Plugin Manager.",
    },
    "migration.target_format": {"zh-CN": "{version}（格式 {format}）", "en": "{version} (format {format})"},
    "migration.targets_all": {"zh-CN": "全选", "en": "Select all"},
    "migration.targets_none": {"zh-CN": "全不选", "en": "Select none"},
    "migration.policy_section": {"zh-CN": "迁移策略", "en": "Migration policy"},
    "migration.policy_emulated": {
        "zh-CN": "允许模拟迁移（allow_emulated）",
        "en": "Allow emulated migration (allow_emulated)",
    },
    "migration.policy_emulated_desc": {
        "zh-CN": "模拟迁移：按等价规则改写结构，结果与目标版本行为一致但不逐字相同。",
        "en": "Emulation rewrites structures with equivalent rules; behavior matches the target "
        "without being byte-identical.",
    },
    "migration.policy_lossy": {
        "zh-CN": "允许有损迁移（allow_lossy）",
        "en": "Allow lossy migration (allow_lossy)",
    },
    "migration.policy_lossy_desc": {
        "zh-CN": "有损迁移：接受会丢失部分信息的改写结果，例如精度舍入或字段移除。",
        "en": "Lossy migration accepts rewrites that drop information, such as precision rounding or field removal.",
    },
    "migration.policy_unknown": {
        "zh-CN": "允许未知迁移（allow_unknown）",
        "en": "Allow unknown migration (allow_unknown)",
    },
    "migration.policy_unknown_desc": {
        "zh-CN": "未知迁移：接受无法确认是否等价的改写，需要事后人工复核。",
        "en": "Unknown migration accepts rewrites whose equivalence cannot be confirmed; review them "
        "manually afterwards.",
    },
    "migration.policy_fail_warnings": {
        "zh-CN": "警告即失败（fail_on_warnings）",
        "en": "Fail on warnings (fail_on_warnings)",
    },
    "migration.policy_fail_warnings_desc": {
        "zh-CN": "警告即失败：构建中出现任何警告都视为失败，常用于严格校验。",
        "en": "Any warning in a build counts as failure; useful for strict validation.",
    },
    "migration.policy_hint": {
        "zh-CN": "默认只允许无损与模拟迁移；有损/未知迁移需要显式勾选，unsupported（目标版本没有等价机制）永远拒绝。",
        "en": "By default only lossless and emulated migrations are allowed; lossy/unknown require "
        "explicit opt-in, and unsupported is always rejected.",
    },
    "migration.build": {"zh-CN": "开始迁移", "en": "Start migration"},
    "migration.log_section": {"zh-CN": "构建日志", "en": "Build log"},
    "migration.log_hint": {
        "zh-CN": "构建日志将显示在这里；点击“开始迁移”开始。",
        "en": 'Build output will appear here; press "Start migration" to begin.',
    },
    "migration.build_started": {"zh-CN": "开始构建…", "en": "Building…"},
    "migration.build_failed": {"zh-CN": "构建失败：{error}", "en": "Build failed: {error}"},
    "migration.build_done": {"zh-CN": "迁移完成", "en": "Migration complete"},
    "migration.source_line": {
        "zh-CN": "来源格式：{format}  候选版本：{candidates}",
        "en": "Source format: {format}  Candidates: {candidates}",
    },
    "migration.status_ok": {"zh-CN": "OK", "en": "OK"},
    "migration.status_failed": {"zh-CN": "FAILED", "en": "FAILED"},
    "migration.universal_line": {"zh-CN": "通用 overlay 包：{path}", "en": "Universal overlay pack: {path}"},
    "migration.report_line": {"zh-CN": "报告：{path}", "en": "Report: {path}"},
    "migration.need_pack": {"zh-CN": "请先填写数据包路径", "en": "Enter a data pack path first"},
    "migration.need_target": {"zh-CN": "请至少勾选一个目标版本", "en": "Select at least one target version"},
    "migration.invalid_subfolder": {
        "zh-CN": "子文件夹名称只能包含字母、数字、'.'、'_'、'-'",
        "en": "Subfolder name may only contain letters, digits, '.', '_', '-'",
    },
    # -- application shell --------------------------------------------------------
    "app.subtitle": {"zh-CN": "数据包兼容性迁移工具", "en": "Data-pack compatibility migration tool"},
    "app.quit": {"zh-CN": "退出", "en": "Quit"},
    "app.plugins": {"zh-CN": "插件管理", "en": "Plugins"},
    "app.language": {"zh-CN": "语言", "en": "Language"},
    "app.language_label": {"zh-CN": "语言：{name}", "en": "Language: {name}"},
    "app.language_switched": {"zh-CN": "已切换语言：{name}", "en": "Language switched: {name}"},
    # -- plugin marketplace --------------------------------------------------------
    "market.open": {"zh-CN": "插件市场...", "en": "Plugin Marketplace..."},
    "market.title": {"zh-CN": "插件市场", "en": "Plugin Marketplace"},
    "market.hint": {
        "zh-CN": "浏览已注册插件仓库中的插件：输入关键词后回车搜索，点击插件查看详情并安装。"
        "仓库用 `dpcompat plugin repo add/remove/list` 管理。",
        "en": "Browse plugins from registered repositories: type a keyword and press Enter to search, "
        "click a plugin for details and install. Manage repositories with `dpcompat plugin repo add/remove/list`.",
    },
    "market.search_placeholder": {
        "zh-CN": "搜索插件（id/名称/简介/标签）",
        "en": "Search plugins (id/name/description/tags)",
    },
    "market.search": {"zh-CN": "搜索", "en": "Search"},
    "market.refresh": {"zh-CN": "刷新", "en": "Refresh"},
    "market.back": {"zh-CN": "返回", "en": "Back"},
    "market.category_all": {"zh-CN": "全部分类", "en": "All categories"},
    "market.installed_mark": {"zh-CN": "（已安装）", "en": " (installed)"},
    "market.load_failed": {
        "zh-CN": "无法加载插件市场：{error}",
        "en": "Failed to load the plugin marketplace: {error}",
    },
    "market.empty": {"zh-CN": "没有匹配的插件", "en": "No matching plugins"},
    "market.row": {
        "zh-CN": "{name}  ·  {target}  ·  {repo}",
        "en": "{name}  ·  {target}  ·  {repo}",
    },
    "market.detail_meta": {
        "zh-CN": "{id} · v{version} · target {target} · {repo}",
        "en": "{id} · v{version} · target {target} · {repo}",
    },
    "market.detail_author": {"zh-CN": "作者：{author}", "en": "Author: {author}"},
    "market.detail_license": {"zh-CN": "许可证：{license}", "en": "License: {license}"},
    "market.detail_homepage": {"zh-CN": "主页：{homepage}", "en": "Homepage: {homepage}"},
    "market.detail_tags": {"zh-CN": "标签：{tags}", "en": "Tags: {tags}"},
    "market.install": {"zh-CN": "安装", "en": "Install"},
    "market.installed": {"zh-CN": "已安装", "en": "Installed"},
    "market.install_done": {"zh-CN": "已安装插件：{name} ({id})", "en": "Installed plugin: {name} ({id})"},
    "market.install_failed": {"zh-CN": "安装失败：{error}", "en": "Install failed: {error}"},
    "market.installed_hint": {
        "zh-CN": "该插件已安装，可在插件管理页启用/禁用或卸载。",
        "en": "This plugin is installed; enable/disable or uninstall it in Plugin Manager.",
    },
}


def tr(language: str, key: str, **kwargs: object) -> str:
    """Look up ``key`` for ``language``, falling back to the default language, then the key."""

    entry = TRANSLATIONS.get(key)
    text = key if entry is None else entry.get(language) or entry.get(DEFAULT_LANGUAGE) or key
    return text.format(**kwargs) if kwargs else text


def resolve_language(override: str | None = None) -> str:
    """Return a valid language code from an explicit override, env, prefs, or the default."""

    for candidate in (override, os.environ.get(ENV_LANGUAGE)):
        if candidate in LANGUAGES:
            return candidate
    return load_preferred_language()


def load_preferred_language() -> str:
    """Read the persisted UI language (``~/.dpcompat/prefs.toml`` ``[ui] language``)."""

    try:
        with PREFS_FILE.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_LANGUAGE
    ui = raw.get("ui")
    if not isinstance(ui, dict):
        return DEFAULT_LANGUAGE
    language = ui.get("language")
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def save_preferred_language(language: str) -> None:
    """Persist the UI language for the next launch."""

    if language not in LANGUAGES:
        raise ValueError(f"Unknown language {language!r}")
    PREFS_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(f'[ui]\nlanguage = "{language}"\n', encoding="utf-8")
