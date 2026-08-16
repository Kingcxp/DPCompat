# 架构说明（AI 版）

模块职责、数据流、不变量与扩展点。面向"改某个子系统需要知道什么"。

## 数据流

```text
materialize_source → detect_pack → flatten_pack
                         ↓
             create_effective_registry(config, store)
       built-ins + 项目模块 + entry points + 声明式文件 + 插件
                         ↓
        每个目标 build_target：规则 → fallback → 重扫 → 策略 → ZIP
                         ↓
               report + universal overlays
```

- 输入包自身含 overlay 时，先按识别出的来源 format 展平为游戏实际可见视图（`packio.flatten_pack`）。
- 每个目标从**同一份有效来源副本**出发，按 registry 的确定顺序执行规则；目标之间不串联。
- 构建事务在 `engine.compile_pack` 的 `tempfile.TemporaryDirectory` 内完成，临时目录用后即毁。

## 模块边界

| 模块 | 单一职责 | 关键符号 |
| --- | --- | --- |
| `models.py` | Pydantic 核心值对象 | `PackFormat`、`PackFormatRange`、`Diagnostic`、`Severity`、`Compatibility`、`BuildPolicy`、`MigrationRecord`、`TargetBuildResult`、`VersionProfile` |
| `versions.py` | releases.json 加载与版本解析 | `PROFILES`、`resolve_profile`、`profiles_for_format`、`unique_format_profiles` |
| `manifests.py` | features.json（feature 最低版本） | `feature_specs`、`resource_minimums`、`identifier_minimums` |
| `config.py` | dpcompat.toml 严格校验 | `ProjectConfig`、`RuleSettings`、`load_config` |
| `detector.py` | 来源格式识别 | `detect_pack` → `DetectionResult`（`source_format`、`candidates`、`confidence`、`evidence`） |
| `scanner.py` | 只读扫描 | `scan_pack(root, target)` → `ScanResult`；产出证据 + 诊断（`resource-too-new`、`command-too-new`、`identifier-too-new` 等） |
| `metadata.py` | pack.mcmeta 渲染 | `detect_format_range`、`render_single_target_metadata`、`render_universal_metadata`、`overlay_matches` |
| `packio.py` | 输入/输出 | `materialize_source`、`flatten_pack`、`copy_pack`、`create_deterministic_zip`、`tree_sha256` |
| `commands.py` | mcfunction 顶层 token 化 | `parse_command_line` → `ParsedCommandLine`、`iter_execute_segments`、`macro_placeholders_are_quoted`、`is_zero_rotation` |
| `snbt.py` / `nbt.py` | SNBT / 二进制 NBT | `loads`/`dumps`（snbt）、`NbtDocument`/`NbtTag`（nbt） |
| `text_components.py` | 文本组件事件转换 | `upgrade_component`/`downgrade_component`、`TextComponentMigrationError` |
| `entity_data.py` | 实体 NBT 字段转换 | `upgrade_entity_nbt`/`downgrade_entity_nbt` → `EntityTransformResult` |
| `jsonutil.py` | JSON 读写 | `loads_lenient`/`loads_strict`、`load_path`/`dump_path`、`JsonNormalizationError`、`DuplicateJsonKeyError` |
| `migrations/` | 内置方向化规则 | `MigrationContext`、`RuleResult`、`crosses`；`BUILTIN_RULES` 有序元组 |
| `rules/` | 注册表 + 声明式规则 | `create_rule_registry`、`RuleRegistry`、`DeclarativeMigrationRule`、`DeclarativeRuleSpec` |
| `fallback.py` | 作者 fallback | `load_fallback`、`apply_fallback_files`、`resolve_with_fallback` |
| `engine.py` | 构建编排 | `compile_pack`、`build_target`、`build_universal` |
| `report.py` | JSON 报告 | `build_report`、`write_report` |
| `i18n.py` | TUI 多语言 | `tr`、`LANGUAGES`、`TRANSLATIONS`、`resolve_language`、`save_preferred_language` |
| `ui/app.py` | Textual TUI | `DpCompatApp`、五个 Screen |
| `plugins.py` | 插件系统 | `PluginStore`、`PluginMeta`、`PluginInfo`、`BUILTIN_PLUGINS`、`create_effective_registry`、`scaffold_plugin_template` |
| `cli.py` | CLI 适配 | `build_parser`、`run_application`、`main` |
| `servercheck.py` | server JAR 验证 | `check_with_server` |

## 模型策略

- **Pydantic + `extra="forbid"`**：配置、版本档案、feature manifest、声明式规则、诊断、检测/构建结果、fallback、server-check 结果、插件元数据。拒绝未知字段、校验范围。
- **dataclass**：命令 token、SNBT/NBT 节点、单次规则运行 context（`MigrationContext`/`RuleResult`/`EntityTransformResult`）——解析器内部对象，不接收外部 schema。
- `FrozenModel`（frozen Pydantic）用于不可变值对象（`PackFormat` 等），哈希化可进 set。

## 规则顺序与 registry

- registry 按 `(priority, id)` 排序；内置规则 priority 从 100 起，声明式默认 500。
- `BUILTIN_RULES` 的元组顺序是**编译契约**：结构化解析/模式迁移必须先于宽泛标识符改写。新增规则若有顺序依赖，必须写注释与集成测试。
- 重复 ID、非法协议、缺一手来源在编译前失败。
- `create_effective_registry(config, store)` = 项目 `[rules]` 模块/文件/entry point + 启用的插件规则。

## 不变量（全局）

1. 不修改用户输入；所有迁移在临时副本执行。
2. 每个目标从同一来源独立构建。
3. 规则崩溃转为 error，失败目标不写 ZIP。
4. fallback 后重新扫描，不能用 replacement 隐藏新残留。
5. 未识别内容不归类为 lossless。
6. ZIP 拒绝路径穿越/特殊条目；目录和 fallback 拒绝 symlink。
7. ZIP 原子替换，内容/时间戳/权限确定。
8. universal 包 overlay 使用完整 `data/`；基础层 guard 防止未来未知格式静默空载。
9. 报告记录来源证据、有效规则来源、规则结果、策略、诊断、哈希。
10. 来源层与展平层产生的相同诊断只记录一次（`_extend_unique_diagnostics`）。

## 扩展原则

- 精确、可逆的 JSON 字段变化 → 声明式规则（`rules/declarative.py`，只有 `json_exact_value`/`json_rename_key` 两种操作）。
- 命令、SNBT/NBT、跨文件引用、条件语义 → Python 规则（`migrations/`）。
- 需要随包分发、用户可开关的规则集 → 插件（见 PLUGIN_SYSTEM.md）。

## 已知的保守边界（不要试图"顺手修掉"）

- 位于已解析引号标量内的宏占位符可保留并迁移静态外层结构；能生成 key/list/compound/整个命令参数的宏仍 `unknown` 阻断。
- 任意物品组件命令语法、一般 Environment Attributes/worldgen 降级、新实体/方块/客户端能力可能需要 fallback。
- 完整 Brigadier / DataFixerUpper / 每版本 registry 镜像**不是**目标。
