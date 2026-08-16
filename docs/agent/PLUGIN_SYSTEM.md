# 插件系统（AI 版）

插件 = 可安装、可开关的迁移规则打包单元。核心文件 `dpcompat/plugins.py`。

## 概念

- **内置插件**：13 个规则分组（`text-components@71`、`gamerules@94.1` 等），元数据在 `_BUILTIN_PLUGIN_DEFS`（中文）与 `_BUILTIN_PLUGIN_L10N`（英文本地化）。`_builtin_plugins()` 校验目录与 `BUILTIN_RULES` 完全一致（missing/duplicates 直接抛错）——新增内置规则必须同步更新目录。
- **文件插件**：`.py`（Python 规则）或 `.json`（声明式规则），通过 CLI/TUI 安装到插件目录。
- **插件目录**：`default_plugin_dir()` = `DPCOMPAT_PLUGIN_DIR` 环境变量，否则包旁边 `dpcompat/plugins/`（每个 Python 环境独立）。
- **启用状态**：目录下 `plugins.toml`，**只有被禁用的插件才列出**（缺失 = 启用）。
- **规则 id 全局唯一**：安装时校验内置 + 其他已安装插件的规则 id 冲突。

## 元数据契约

```python
PLUGIN = {
    "id": "my-pack-rules@94.1",       # 必填 [a-z0-9._@-]
    "name": "...",                     # 必填，去首尾空格
    "description": "...",              # 必填；支持 Markdown（TUI 详情页渲染）
    "version": "1.0.0",
    "target_version": "1.21.11",       # 必填，必须已在 releases.json 注册
    "readme": "...",                   # 可选 Markdown 文档
    "localizations": {                 # 可选多语言
        "en": {"name": "...", "description": "...", "readme": "..."},
    },
    "official_sources": ["https://..."],  # 规则无自带来源时兜底
}
```

- `PluginMeta`（Pydantic，extra=forbid）校验上述字段；`PluginLocalization` 校验本地化条目。
- Python 插件必须暴露 `RULES` 或 `dpcompat_rules()`；JSON 插件用 `JsonPluginFile` 包装（可含多条规则）或裸 `DeclarativeRuleSpec`。
- 保持模块顶层无副作用：插件文件会被多次加载（安装校验 + 构建注册）。

## PluginInfo 与本地化

- `PluginInfo` 是浏览记录（CLI/TUI 显示用），含 `localizations` 与 `localized(language)` 方法：
  - 命中语言 → 用本地化 name/description/readme（readme 为空则保留规范 readme）；
  - 未命中 → 返回自身（规范语言 = 顶层字段）。
- 内置插件的本地化 readme 由 `_builtin_readme(..., language=...)` 生成（标题与说明随语言）。
- CLI `plugin list --json` 排除 `readme` 与 `localizations`。

## 存储与操作

`PluginStore`：

- `list_plugins()`：内置 + 已安装，应用持久化状态。
- `enabled_rule_ids()`：启用的插件贡献的规则 id 集合（构建过滤用）。
- `set_enabled(id, bool)`：持久化开关（禁用写 `plugins.toml`，启用删除条目）。
- `install(path, force=False)`：校验 + 拷贝；冲突（内置同名、规则撞车、文件已存在）抛 `ValueError`。
- `uninstall(id)`：删文件 + 状态条目。
- `_inspect_file` / `_inspect_python` / `_inspect_json`：解析校验，返回 `PluginInfo`。

## 注册表接线

- `create_effective_registry(config=None, store=None)`：项目 `[rules]`（modules/files/entry points）+ 启用的文件插件。
- 构建路径：`cli._registry` / `ui.MigrationScreen._build_task` 调用 `create_effective_registry`；`compile_pack(rules=registry.rules())`。
- 禁用插件 → 不贡献任何规则（`enabled_rule_ids` 过滤 + 跳过加载）。

## 模板

`scaffold_plugin_template(name, location, subfolder=False)` 生成 `{name}.py`（`_TEMPLATE_SOURCE`）与 README.md（`_TEMPLATE_README`）。模板已含 `localizations` 示例。**改模板时同步更新 `tests/test_plugins.py::test_scaffold_plugin_template_creates_a_working_project` 的断言**（它检查 `"localizations"` 等字符串）。

## 插件文档

插件开发者文档位于 **`plugin-development/PLUGIN_DEVELOPMENT.zh-CN.md`**（仓库根，不在 docs/）。改动契约时同步更新它，以及 `docs/ADDING_A_NEW_VERSION.zh-CN.md`、`docs/DEVELOPMENT_GUIDE.zh-CN.md` 中的引用与 `scripts/sync_wiki.py` 的 PAGES 映射。

## 插件市场（marketplace）

`dpcompat/market.py` 实现远程插件仓库客户端（仓库契约见官方仓库 `docs/`）：

- **仓库** = 任意静态文件服务器，布局：根 `index.json`（name/schema/categories，`CategoryInfo` 含 id/path/display_name）→ 分类目录 `INDEX.json`（category + plugins 列表）→ 插件文件夹（`<id>.py` 或 `<id>.json` + 可选 `plugin.json`）。GitHub 用 raw base URL（`https://raw.githubusercontent.com/<owner>/<repo>/main`）。
- **注册**：`~/.dpcompat/repos.toml`（`DPCOMPAT_REPOS_FILE` 覆盖）；官方仓库内置（`DEFAULT_REPO_URL`）。`add_repo` 会先拉取目录校验可达性。
- **获取**：`urllib.request`（无新依赖），`fetch_json`/`fetch_catalog`/`fetch_category_index`；插件文件下载后经 `PluginStore._inspect_file` 解析 → `PluginInfo`（与本地安装同一套校验），`plugin.json` 解析为 `MarketPluginMeta`（author/license/homepage/tags）。
- **列表/搜索**：`list_market_plugins(repo_name/category/query)`；单仓库失败只跳过不拖垮全部（`except MarketError: continue`）。
- **安装**：`install_market_plugin(id, store, repo_name=...)` 下载 → `store.install`。
- **MarketPlugin** dataclass = PluginInfo + repo + category + meta；TUI 预览用 `info.localized(app.language)`。

CLI：`dpcompat plugin repo add/remove/list`、`plugin market list/show/install`、`plugin template`（模板 CLI 化）。

TUI：`MarketScreen`（搜索框 + 分类 Select + 插件行 + 详情安装）与 `MarketDetailScreen`。要点：

- 网络获取全部在 `run_worker(thread=True, exclusive=True, group="market")`；widget 状态必须在主线程捕获后传入 worker（`_reload` 捕获 query/category）。
- `Select` 的初始值是 `Select.BLANK` 哨兵，必须显式处理（`"" if selected in (None, Select.BLANK) else str(selected)`）。
- 行/详情刷新用 `on_screen_resume`（pop 返回时触发）而不是 `push_screen` 回调——Textual 只在 `dismiss` 时调用 push 回调，`pop_screen` 不调用。**这是插件详情页返回后列表刷新的正确机制**。
- 带 id 的直接子节点列表重渲染必须 `await box.remove_children()` 后再 `await box.mount(...)`：Textual 的 prune 是异步的，同一同步轮次 remove+mount 会抛 `DuplicateIds`（`call_from_thread` 会 await 协程，所以 worker → async `_apply_loaded` → `await _render_list()` 可行）。

## 测试

- `tests/test_market.py`：本地 `ThreadingHTTPServer`（`tests/helpers.py::repo_server`）伺服 fixture 仓库；覆盖仓库增删、搜索、本地化、安装、错误路径、CLI 子命令、不可达仓库容错。
- `tests/test_tui.py::test_tui_marketplace_browses_and_installs_plugins`：pilot 驱动浏览→详情→安装→返回标记。
- 新仓库目录/字段变更时同步官方仓库的 `tools/validate_plugin.py` 与 `docs/`。
