# TUI 与多语言（AI 版）

TUI 在 `dpcompat/ui/app.py`（Textual 3.x），约 1000 行，五个 Screen。多语言系统在 `dpcompat/i18n.py`。

## 屏幕结构

| 类 | 职责 | 关键 widget id |
| --- | --- | --- |
| `MigrationScreen` | 主表单：数据包路径、输出、目标版本、策略、构建日志 | `#pack-path-input`、`#output-input`、`#output-subfolder(-name)`、`#target-<safe-version>`、`#policy-*`、`#build-start`、`#build-log`、`#lang-switch`、`#open-plugins`、`#quit-app` |
| `PluginsScreen` | 插件管理（按目标版本分组、安装、模板） | `#plugin-list`、`#plugins-install/template/refresh/back` |
| `PluginDetailScreen` | 插件详情（元数据、Markdown 文档、开关/卸载） | `#detail-doc`（Markdown）、`#detail-toggle`、`#detail-remove`、`#detail-back` |
| `FilePickerScreen` | 模态文件树选择 | `#picker-tree`、`#picker-up/pick/cancel` |
| `TemplateScreen` | 插件模板名称表单 | `#template-name`、`#template-subfolder`、`#template-create/cancel` |

导航：`MigrationScreen` 是根；`p` 打开 `PluginsScreen`；其余是 push 的模态屏。构建在 worker 线程（`run_worker(thread=True, exclusive=True, group="build")`），通过 `app.call_from_thread` 回写日志与通知。

## 多语言系统（i18n.py）

- `LANGUAGES = {"zh-CN": "简体中文", "en": "English"}` —— 顺序即 TUI 切换循环顺序。
- `TRANSLATIONS[key][lang]` 扁平表；`tr(lang, key, **fmt)` 查找，缺语言回退 `zh-CN`，再回退 key 本身；支持 `{param}` 格式化。
- 语言来源优先级：`--lang`（CLI `dpcompat tui --lang en`）> 环境变量 `DPCOMPAT_LANG` > `~/.dpcompat/prefs.toml` 的 `[ui] language` > `zh-CN`。
- `resolve_language(override)` / `load_preferred_language()` / `save_preferred_language(lang)`。

### 规则：改任何 TUI 文本必须走 i18n

1. 所有可见字符串放进 `TRANSLATIONS`，key 形如 `section.meaning`（如 `migration.need_pack`、`plugin.enabled_notify`）。
2. Screen 内用 `self._t(key, **fmt)`（来自 `LocalizedScreen` mixin）或 App 级 `app.tr(key, **fmt)`。
3. 每个 Screen 实现 `refresh_language()`：就地更新 Static/Button/Input placeholder/Checkbox label，并调 `self._set_bindings([...])` 更新 Footer 绑定描述（`self._bindings` 是实例级副本，赋值 + `app.refresh_bindings()` 即可）。
4. App 级 `action_cycle_language()`（绑定 `l`）切换语言、持久化、遍历 `screen_stack` 调用各屏 `refresh_language`。`#lang-switch` 按钮与 `l` 键等价。
5. 新增语言 = `LANGUAGES` 加条目 + `TRANSLATIONS` 全表补该语言（`tests/test_i18n.py::test_every_registered_language_has_translations` 强制）。
6. **不要**在 Screen 类 BINDINGS 里依赖实例赋值（`_merged_bindings` 是类级缓存的）；本地化绑定用 `_set_bindings`。

### 插件本地化跟随 TUI

- `PluginInfo.localized(app.language)` 解析插件的 `localizations`；`PluginsScreen._refresh` 与 `PluginDetailScreen._display_info` 在渲染时解析。
- `PluginDetailScreen` 保存**规范（未本地化）info**，显示时再 `localized`，保证语言来回切换正确。

## 安全与健壮性

- **富文本注入**：插件名/描述/诊断文本来自外部，进入 `Button(label)`/`Static` 的 Rich markup 前必须 `rich.markup.escape(...)`。诊断行与插件行已处理。
- **Markdown 简介**：`PluginInfo.description` 支持 Markdown——详情页无 readme 时用 `Markdown(description)` 渲染；列表行用 `_strip_markdown` 压成纯文本（避免行内 markdown 噪音）。
- **构建 worker**：widget 引用必须在主线程捕获；worker 只通过 `call_from_thread` 写日志/通知；异常写入日志而非崩溃 UI。
- 插件/版本 id 做 widget id 时用 `_widget_safe`（非 `[a-zA-Z0-9_-]` 字符替换为 `-`）。

## TUI 测试（tests/test_tui.py）

- `DpCompatApp().run_test()` + `pilot`；`DPCOMPAT_PLUGIN_DIR` 指向 tmp 防止污染真实插件目录。
- 关键模式：`pilot.press("p")` 开插件屏、`pilot.click("#fold-1-21-5")` 展开分组（**先 `scroll_visible`**，折叠行可能在视口外导致 OutOfBounds）、`pilot.press("escape")` 返回。
- 语言切换测试把 `i18n.PREFS_DIR/PREFS_FILE` monkeypatch 到 tmp，按 `l` 后断言标题/按钮/插件行本地化与持久化。
- 新增屏幕或改动 widget id 时同步更新这些测试。

## CLI 接线

- `dpcompat tui --lang <code>`：`_command_tui` 用 `resolve_language(args.lang)` 构造 `DpCompatApp(language=...)`。
- 注意 `DpCompatApp.__init__` 在模块内延迟 import `resolve_language`，避免 cli ↔ i18n 循环。
