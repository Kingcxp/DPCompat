# 测试与验证（AI 版）

## 测试布局

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_versions.py` / `test_manifests.py` / `test_metadata.py` | releases.json / features.json 严格校验、版本解析、pack.mcmeta 渲染 |
| `tests/test_config.py` | dpcompat.toml 解析、未知 section/key 拒绝 |
| `tests/test_scanner.py` / `test_build.py` | 扫描器行为、engine 端到端构建 |
| `tests/test_packio.py` | 输入物化、overlay 展平、ZIP 确定性/安全 |
| `tests/test_migrations.py` | 命令 token 化 + 每个边界规则（规则级 apply）+ 实体/文本组件转换 |
| `tests/test_command_boundaries.py` | 命令语法边界规则（gamerule/worldborder/spawnpoint 等） |
| `tests/test_snbt.py` / `test_nbt.py` / `test_jsonutil.py` | 解析器与 JSON 工具 |
| `tests/test_rules.py` | 声明式规则与注册表 |
| `tests/test_plugins.py` | 插件存储、安装/开关/卸载、注册表接线、本地化 |
| `tests/test_cli.py` | argparse、Rich 输出、JSON 模式、退出码 |
| `tests/test_tui.py` | Textual pilot 冒烟：启动、插件屏、详情页、模板、语言切换 |
| `tests/test_i18n.py` | 翻译查找/回退、语言偏好持久化、全语言覆盖 |
| `tests/test_documentation.py` | 模块 docstring 完整性、docs/agent 覆盖、README 链接可解析 |
| `tests/test_wiki_sync.py` | scripts/sync_wiki.py 生成与链接改写 |
| `tests/test_logging.py` | 日志配置 |
| `tests/test_servercheck.py` | server-check 参数与输出 |
| `tests/test_research_fixture.py` | examples/research_fixture 端到端回归 |

新增测试文件时在 `tests/helpers.py` 复用 `make_pack` / `write`。

## 规则测试怎么写

```python
with tempfile.TemporaryDirectory() as temp_dir:
    root = make_pack(Path(temp_dir), 61)               # pack_format 或 [major, minor]
    write(root, "data/demo/function/test.mcfunction", "gamerule doFireTick true\n")
    rule = GameRuleRegistryRule()
    result = rule.apply(MigrationContext(root, PackFormat(61), PackFormat(94, 1), BuildPolicy()))
    # 断言文件内容与 result.diagnostics 的 code/severity/compatibility
```

要点：

- 直接调 `rule.apply` 定位单条规则；端到端另有 test_build / research_fixture。
- 每条双向规则至少覆盖：边界内 no-op、upgrade、downgrade、冲突、默认值、非默认有损/不支持、宏/未知语法、二次执行幂等、无效输入、完整 build 后无残留。
- 策略联动测试（`BuildPolicy(allow_unknown=True)` 之类）验证 `policy_diagnostic` 的 WARNING/ERROR 切换。
- `test_research_fixture.py` 的夹具在 `examples/research_fixture`，代表各边界的官方形状；规则行为变化时同步夹具。

## 门禁顺序

1. `make format`（自动格式化）
2. `make check` = ruff format --check + ruff check + mypy(strict) + pyright + pytest
3. `make smoke`（CLI 冒烟）
4. 涉及构建输出时：`uv run dpcompat plan/build examples/simple_pack --target ...` + `python -m zipfile -t` 校验 ZIP
5. `uv lock --check`（依赖变更后）

Windows 用 `scripts/build.ps1` 等价目标；CI 在 Linux（3.12/3.13）与 Windows（3.12）执行同一套。

## 端到端验证层级（发布语义）

1. 仓库测试 + fixture 证明"转换器按设计运行"；
2. 匹配原版 server JAR 的 `server-check` 证明"输出可加载"（`dpcompat server-check dist/x.zip --server-jar server.jar --java java --accept-eula`；不自动下载 JAR、不替用户接受 EULA）；
3. 作者 GameTest/行为断言才证明"玩法等价"。

三层不可互相替代；发布前至少前两层。

## 常见陷阱

- TUI pilot 点击视口外 widget 抛 `OutOfBounds`：先 `widget.scroll_visible()`。
- 路径比较用 `resolve()`（Windows 8.3 短名）。
- symlink 相关测试在 Windows 跳过（`test_packio.py`）。
- 顺序敏感的 flaky 测试：把互不依赖的断言拆开，或改为 focus+enter 代替坐标点击。
- 修改 `_BUILTIN_PLUGIN_DEFS`/`BUILTIN_RULES` 后，`_builtin_plugins()` 的目录一致性校验会立即失败——先跑 `tests/test_plugins.py`。
