# DPCompat 0.3：从空目录逐文件重建

这不是现有 Git 历史的复述，而是一条可以亲手重写项目的教学顺序。每个 `Cxx` 都是一个独立检查点：先按顺序编辑文件，再运行验收命令，最后提交。不要把多个检查点一次性复制进仓库，否则很难确认自己是否真正理解了依赖方向。

项目只处理 Minecraft Java Edition 1.21.4 及以后正式版。版本事实来自 Mojang 正式版 changelog；规则没有正式来源、没有正反向测试或无法说明语义边界时，不进入默认规则集。

## 0. 使用方法

每个检查点都包含四项：

1. `编辑顺序`：同一提交内也要按这个顺序写；
2. `必须实现`：验收时逐项对照；
3. `验收`：提交前执行；
4. `Commit`：标题和正文可以直接采用，但应把你实际运行的测试结果写进 PR。

全项目最终门禁：

```bash
make check
make smoke
make build
```

如果 Windows 没有 GNU Make，可使用等价的 PowerShell 命令（目标与 Makefile 一一对应）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 check
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 smoke
```

## 1. 最终文件与检查点索引

| 文件或目录 | 首次创建 |
| --- | --- |
| `.python-version`、`.gitignore`、`LICENSE` | C00 |
| `pyproject.toml`、`uv.lock` | C01 |
| `.editorconfig`、`Makefile`、`scripts/clean.py`、`scripts/build.ps1` | C02 |
| `dpcompat/__init__.py`、`__main__.py`、`logging_config.py` | C03 |
| `dpcompat/models.py` | C04 |
| `dpcompat/data/releases.json`、`versions.py` | C05 |
| `dpcompat/data/features.json`、`manifests.py` | C06 |
| `jsonutil.py` | C07 |
| `metadata.py` | C08 |
| `packio.py` | C09–C11 |
| `commands.py` | C12 |
| `snbt.py` | C13 |
| `nbt.py` | C14 |
| `text_components.py` | C15 |
| `entity_data.py` | C16 |
| `migrations/base.py` | C17 |
| `migrations/common.py` | C18 |
| `migrations/text.py` | C19 |
| `migrations/items.py` | C20 |
| `migrations/entities.py` | C21 |
| `migrations/structure_nbt.py`、`structures.py` | C22 |
| `migrations/commands.py`（saddle） | C23 |
| `migrations/identifiers.py` | C24 |
| `migrations/commands.py`（spawn rotation） | C25 |
| `migrations/gamerules.py` | C26 |
| `migrations/worldborder.py` | C27 |
| `migrations/resources.py`（filtered） | C28 |
| `migrations/resources.py`、`recipes.py`（clock） | C29 |
| `migrations/recipes.py`（recipes） | C30 |
| `migrations/strict_json.py` | C31 |
| `migrations/sources.py`、`migrations/__init__.py` | C32 |
| `rules/schema.py` | C33 |
| `rules/declarative.py` | C34 |
| `rules/registry.py`、`rules/__init__.py` | C35 |
| `scanner.py` | C36 |
| `detector.py` | C37 |
| `config.py`、`dpcompat.example.toml` | C38 |
| `fallback.py` | C39 |
| `engine.py`（单目标） | C40 |
| `engine.py`（universal overlay） | C41 |
| `report.py` | C42 |
| `cli.py` | C43 |
| `servercheck.py` | C44 |
| `examples/` | C45 |
| `tests/` | C46 |
| `README.md`、`docs/`、社区文件 | C47 |
| `.github/`、`CHANGELOG.md` | C48 |
| `plugins.py`、`ui/`、`docs/PLUGIN_DEVELOPMENT.zh-CN.md` | C54–C55 |

---

## C00：建立仓库边界

### 编辑顺序

1. 创建空目录并运行 `git init`；
2. 写 `.python-version`，固定 `3.12`；
3. 写 `.gitignore`，忽略 `.venv/`、`.cache/`、`dist/`、`logs/*.log` 和 Python 缓存；
4. 加入 MIT `LICENSE`；
5. 只创建空的 `dpcompat/`、`tests/`、`docs/`、`examples/`、`scripts/`。

### 必须理解

- 源数据包是输入，`dist/` 永远是可重建产物；
- 不能把真实世界目录或 server JAR 提交进仓库；
- 最低 Python 3.12 是为了稳定使用 `tomllib`、现代类型语法和 `Path` API。

### 验收

```bash
git status --short
```

### Commit

```bash
git add .python-version .gitignore LICENSE
git commit \
  -m "chore: establish repository and Python boundaries" \
  -m "Target Python 3.12 and keep virtual environments, generated archives, logs, and server worlds outside version control." \
  -m "No executable migration behavior is introduced in this checkpoint."
```

## C01：配置 uv、构建后端与依赖

### 编辑顺序

1. 创建 `pyproject.toml` 的 `[project]`；
2. 添加运行依赖 `pydantic`、`rich`；
3. 添加 dev dependency group：`pytest`、`pytest-cov`、`ruff`、`mypy`；
4. 配置 Hatchling wheel/sdist；
5. 添加 `dpcompat = "dpcompat.cli:main"`；
6. 最后运行 `uv lock` 生成 `uv.lock`。

### 必须实现

- `requires-python = ">=3.12"`；
- Pydantic 限制在 v2 主版本；
- Rich、ruff、mypy、pytest 版本范围有上界，避免未来破坏性升级直接进入 CI；
- 包数据中的 JSON 能进入 wheel。

### 验收

```bash
uv sync --all-groups
uv run python -c "import pydantic, rich; print(pydantic.__version__)"
```

### Commit

```bash
git add pyproject.toml uv.lock
git commit \
  -m "build: bootstrap the uv-managed Python package" \
  -m "Declare Pydantic and Rich as runtime dependencies and pytest, Ruff, mypy, and coverage as reproducible development tools." \
  -m "Use Hatchling for wheel and source-distribution builds."
```

## C02：建立统一开发命令

### 编辑顺序

1. 创建 `.editorconfig`；
2. 在 `pyproject.toml` 中配置 ruff 120 字符、Python 3.12 和选定规则；
3. 配置 mypy `strict = true`；
4. 配置 pytest 的严格 marker/config；
5. 创建 `scripts/clean.py`；
6. 创建 `scripts/build.ps1`，提供与 Makefile 目标一一对应的 Windows 开发命令（`sync/format/lint/typecheck/test/coverage/check/build/smoke/clean`）；
7. 创建 `Makefile`，加入 `sync/format/lint/typecheck/test/coverage/check/build/smoke/clean`。

### 安全要求

`clean.py` 必须先确认当前目录的 `pyproject.toml` 确实声明 `dpcompat`，只删除固定的生成目录。不要在 Makefile 中写依赖当前 shell 变量的递归删除。

### 验收

```bash
make sync
make lint
```

### Commit

```bash
git add .editorconfig pyproject.toml Makefile scripts/clean.py scripts/build.ps1
git commit \
  -m "chore: standardize formatting typing testing and cleanup" \
  -m "Expose one-command quality gates on POSIX via the Makefile and on Windows via scripts/build.ps1 while keeping cleanup restricted to recognized generated paths inside the project." \
  -m "The final check target runs Ruff, strict mypy, and pytest without mutating source files."
```

## C03：创建包入口与分模块日志

### 编辑顺序

1. `dpcompat/__init__.py`：只导出版本号和 latest profile；
2. `dpcompat/__main__.py`：转发给 `cli.main()`；
3. `dpcompat/logging_config.py`：先实现 prefix filter，再实现 rotating handler，再实现 queue listener 生命周期；
4. 预留 `tests/test_logging.py`，先验证 application/error/module 三种文件。

### 必须实现

- 控制台使用 `RichHandler`；
- 文件日志为纯文本、UTF-8、包含 logger 名和线程名；
- `application.log` 接收整体日志，`errors.log` 只收 ERROR+；
- `dpcompat.engine`、`dpcompat.migrations`、`dpcompat.rules`、`dpcompat.packio` 可分别路由；
- `LoggingRuntime.close()` 幂等并停止 listener。

### 验收

```bash
uv run pytest tests/test_logging.py -q
```

### Commit

```bash
git add dpcompat/__init__.py dpcompat/__main__.py dpcompat/logging_config.py tests/test_logging.py
git commit \
  -m "feat(logging): add queued Rich and per-module logging" \
  -m "Render readable console diagnostics while preserving rotating plain-text application, error, engine, migration, rule, and I/O logs." \
  -m "Tests verify routing and listener shutdown."
```

## C04：用 Pydantic 建立核心语义模型

这是从旧 dataclass 方向切换到 0.3 架构的关键提交。先写模型，再让后续模块依赖模型；不要先在 scanner、engine 中散落字典。

### 编辑顺序

1. `FrozenModel`：`extra="forbid"`、`frozen=True`、校验默认值；
2. `PackFormat`：`major/minor >= 0`、解析 int/string/list、显式比较与哈希；
3. `PackFormatRange`：校验 minimum ≤ maximum；
4. `VersionProfile`：版本号、发布日期、Java、官方 URL；
5. `Compatibility`、`Severity`、`BuildPolicy`；
6. `Diagnostic`：允许策略门禁修改 severity，但开启 assignment validation；
7. `DetectionEvidence`、`ScanResult`、`DetectionResult`；
8. `MigrationRecord`、`TargetBuildResult`。

### 设计约束

- Pydantic 用于配置、manifest、规则、诊断、构建结果等模块边界；
- `CommandToken`、SNBT token、NBT tag 等内部语法树仍可使用 dataclass；它们不是外部输入 schema；
- `PackFormat(94, 1)` 的简写保留，是为了让迁移边界在代码中清楚可读；
- `UNSUPPORTED` 永不被 policy 放行；`UNKNOWN`、`LOSSY` 只能显式放行。

### 验收

```bash
uv run pytest tests/test_versions.py -q
uv run mypy
```

### Commit

```bash
git add dpcompat/models.py tests/test_versions.py
git commit \
  -m "feat(core): define strict Pydantic compatibility models" \
  -m "Validate pack formats, release profiles, diagnostics, policies, evidence, migration records, and target results at module boundaries." \
  -m "Keep parser-only syntax nodes separate from externally validated domain models."
```

## C05：创建正式版 release manifest

### 编辑顺序

1. 创建 `dpcompat/data/__init__.py`；
2. 按发布日期写 `releases.json`；
3. 在 `versions.py` 写 `ReleaseManifest`；
4. 写缓存 loader；
5. 写 game version/pack format/latest 解析函数；
6. 添加顺序、重复版本、共享 format 测试。

### 当前登记范围

1.21.4/61、1.21.5/71、1.21.6/80、1.21.7–1.21.8/81、1.21.9–1.21.10/88.0、1.21.11/94.1、26.1/101.1、26.2/107.1。每项必须保存 Mojang 正式版 URL。

### 验收

```bash
uv run pytest tests/test_versions.py -q
uv run dpcompat versions --json
```

### Commit

```bash
git add dpcompat/data/__init__.py dpcompat/data/releases.json dpcompat/versions.py tests/test_versions.py
git commit \
  -m "feat(data): validate stable release profiles from a manifest" \
  -m "Separate human-facing Minecraft releases from migration decisions made on ordered major/minor pack formats." \
  -m "Reject duplicate, out-of-order, or unsourced release entries."
```

## C06：创建 feature minimum manifest

### 编辑顺序

1. 写 `features.json`；
2. 写 `FeatureSpec`、`FeatureManifest`；
3. 建立 resource/identifier 索引；
4. 测试 ID 唯一、至少一个 matcher、官方 URL 合法。

### 边界

feature manifest 只回答“某语法至少需要什么 format”和“降级风险是什么”，不执行变换。实际算法只能放在 migration rule。

### Commit

```bash
git add dpcompat/data/features.json dpcompat/manifests.py tests/test_scanner.py
git commit \
  -m "feat(data): add source-attributed feature minimums" \
  -m "Index new resource types, commands, and identifiers without mixing detection facts with transformation algorithms." \
  -m "Pydantic validation rejects empty and duplicate feature records."
```

## C07：实现严格与宽松 JSON 读取

### 必须实现

- 检测重复键并拒绝；
- 宽松模式只负责注释、尾随逗号规范化；
- strict 模式直接按标准 JSON；
- `dump_path()` 排序、统一缩进和结尾换行；
- 错误包含来源路径。

### Commit

```bash
git add dpcompat/jsonutil.py tests/test_jsonutil.py
git commit \
  -m "feat(json): parse lenient input without accepting duplicate keys" \
  -m "Normalize only comments and trailing commas, preserving a deterministic strict JSON output path for format 80 and newer." \
  -m "Tests cover duplicate-key refusal and normalization."
```

## C08：解析跨时代 pack.mcmeta

### 编辑顺序

1. 旧字段：`pack_format`、`supported_formats`；
2. 新字段：`min_format`、`max_format` 与 minor version；
3. overlay 旧 `formats` 与新 min/max；
4. 单目标 metadata renderer；
5. universal metadata renderer；
6. overlay 匹配测试。

### Commit

```bash
git add dpcompat/metadata.py tests/test_metadata.py
git commit \
  -m "feat(metadata): bridge legacy and minor-version pack metadata" \
  -m "Parse and render both pre-82 supported_formats and post-82 min_format/max_format declarations, including overlays." \
  -m "Single-target outputs never retain source overlay declarations."
```

## C09：安全 materialize 目录与 ZIP

实现 `materialize_source()`：目录输入拒绝 symlink；ZIP 输入拒绝绝对路径、`..`、特殊文件和根目录逃逸。解压到临时目录后再找唯一 `pack.mcmeta` 根。

### Commit

```bash
git add dpcompat/packio.py tests/test_packio.py
git commit \
  -m "feat(io): materialize directory and ZIP inputs safely" \
  -m "Reject path traversal, special ZIP members, ambiguous pack roots, and symlinks before migration reads any source content." \
  -m "Tests include traversal and symlink regressions."
```

## C10：按来源 format 展平已有 overlay

实现 `overlay_directories()`、`merge_tree()`、`flatten_pack()`。必须按 `pack.mcmeta` 声明顺序应用与来源 format 匹配的 overlay；未匹配层绝不能进入有效源码。

### Commit

```bash
git add dpcompat/packio.py tests/test_packio.py
git commit \
  -m "feat(io): flatten the source pack effective overlay view" \
  -m "Materialize exactly what the declared source format would load before any target migration begins." \
  -m "Overlay order and non-matching exclusion are regression tested."
```

## C11：确定性复制、哈希与 ZIP

固定 ZIP 时间、权限、成员顺序和压缩参数；临时文件完成后原子替换输出。测试同一目录两次构建字节完全相同。

### Commit

```bash
git add dpcompat/packio.py tests/test_packio.py
git commit \
  -m "feat(io): create deterministic atomic archives" \
  -m "Stabilize member order, timestamps, permissions, compression, tree hashes, and final publication." \
  -m "Reproducibility tests compare archive bytes and extracted contents."
```

## C12：编写保守的命令 token parser

`commands.py` 只按顶层空白切分，保留引号、JSON/SNBT/组件括号和原始 offset。它不是 Brigadier。规则只识别自己支持的命令形状，不能因为 token 化成功就宣称完整理解命令。

### Commit

```bash
git add dpcompat/commands.py tests/test_migrations.py
git commit \
  -m "feat(parser): tokenize top-level command arguments with offsets" \
  -m "Preserve nested JSON, SNBT, macros, and replacement spans without claiming full Brigadier validation." \
  -m "Migration rules remain responsible for rejecting unsupported command shapes."
```

## C13：实现 SNBT parser

按 tokenizer → recursive parser → typed values → serializer 顺序写 `snbt.py`。覆盖 compound/list/typed array/数字 suffix/quoted string/heterogeneous list，并拒绝重复键。

### Commit

```bash
git add dpcompat/snbt.py tests/test_snbt.py
git commit \
  -m "feat(parser): add strict SNBT parsing and serialization" \
  -m "Represent typed numbers and arrays explicitly so command NBT transformations do not pass through lossy JSON." \
  -m "Round-trip and duplicate-key tests define the supported grammar."
```

## C14：实现二进制结构 NBT

实现 tag 1–12、gzip/裸流、同类型 list、named root。读写必须 round trip；禁止把二进制结构先转 JSON 再写回。

### Commit

```bash
git add dpcompat/nbt.py tests/test_nbt.py
git commit \
  -m "feat(parser): read and write binary Minecraft NBT" \
  -m "Support gzip and raw structures with explicit tag types so entity payloads can be migrated without type erosion." \
  -m "Tests verify binary round trips."
```

## C15：实现文本组件的纯函数变换

`text_components.py` 只接收已由调用方确认的文本组件。实现 1.21.5 的 click/hover event 字段、action-specific value 字段和 show_item/show_entity 结构；冲突字段直接抛错。

### Commit

```bash
git add dpcompat/text_components.py tests/test_migrations.py
git commit \
  -m "feat(text): migrate reviewed text-component event schemas" \
  -m "Handle action-specific click and hover fields in both directions and reject duplicate or ambiguous event representations." \
  -m "Callers must establish text-component context before invoking these functions."
```

## C16：实现实体 NBT 的纯函数变换

覆盖 format 71 的 equipment/drop_chances、saddle、sleeping position、player respawn、item frame 与 phantom 字段。降级 Pig/Strider saddle 会丢物品组件，必须返回 warning，不能假装无损。

### Commit

```bash
git add dpcompat/entity_data.py tests/test_migrations.py
git commit \
  -m "feat(entity): transform reviewed entity NBT fields around format 71" \
  -m "Merge legacy equipment and drop-chance arrays into slot maps while retaining explicit warnings for non-representable saddle data." \
  -m "The transformer operates only after entity context is proven."
```

## C17：定义方向化 migration protocol

`MigrationContext` 保存 root/source/target/policy；`MigrationRule` 要求稳定 ID、`applies()`、`apply()`；`crosses()` 只比较 pack format 边界；`RuleResult` 同时返回记录和诊断。

### Commit

```bash
git add dpcompat/migrations/base.py
git commit \
  -m "feat(migration): define direction-aware rule contracts" \
  -m "Select transformations by crossed pack-format boundaries and return auditable records plus policy-aware diagnostics." \
  -m "Game-version strings stay outside rule selection."
```

## C18：创建 JSON 规则公共遍历器

`migrations/common.py` 负责遍历目标 JSON、汇总 changed file/node、将异常转为 diagnostic；它不能包含任何具体 Minecraft 字段名。

### Commit

```bash
git add dpcompat/migrations/common.py
git commit \
  -m "feat(migration): add deterministic JSON rule helpers" \
  -m "Centralize traversal, error conversion, change accounting, and policy diagnostic creation without embedding version semantics." \
  -m "Rule modules remain small and independently reviewable."
```

## C19：接入文本组件规则

只处理已知 JSON text keys 和 tellraw/title/bossbar/team 等确定参数位置。宏生成组件无法静态解析，返回 `UNKNOWN`。

### Commit

```bash
git add dpcompat/migrations/text.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate text components in proven contexts" \
  -m "Apply format-71 event changes only to known resource keys and command argument positions." \
  -m "Macro-generated and ambiguous components fail closed."
```

## C20：接入物品 tooltip/component 规则

实现 `tooltip_display`、`hidden_components`、`hide_tooltip` 和官方明确简化的 component shapes。命令内尚无完整 item component parser 的形式只诊断，不做正则替换。

### Commit

```bash
git add dpcompat/migrations/items.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate reviewed item tooltip components" \
  -m "Consolidate local show_in_tooltip flags into tooltip_display and reverse only representable scalar and object forms." \
  -m "Unsupported command component syntax is diagnosed instead of rewritten textually."
```

## C21：接入命令中的实体 SNBT

仅支持 summon NBT 和 `data merge entity` 的明确 compound 参数；storage 里的同名字段属于用户数据，不得触碰。

### Commit

```bash
git add dpcompat/migrations/entities.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate entity SNBT in explicit command positions" \
  -m "Parse and rewrite summon and entity-merge payloads while leaving arbitrary storage compounds untouched." \
  -m "Macros and parse failures produce source-located diagnostics."
```

## C22：接入 structure NBT 实体

先写 NBT tag 辅助函数，再写结构 entity list walker，最后写 `StructureEntityNbtRule`。损坏或未知结构不能部分写回。

### Commit

```bash
git add dpcompat/migrations/structure_nbt.py dpcompat/migrations/structures.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate entity data inside structure NBT" \
  -m "Preserve binary tag types and refuse partial output when a structure payload cannot be transformed safely." \
  -m "Gzip structure regressions cover the format-71 boundary."
```

## C23：实现 horse.saddle → saddle

只替换命令 parser 给出的完整 token，不能替换注释、字符串片段或 storage 路径中的子串。

### Commit

```bash
git add dpcompat/migrations/commands.py tests/test_migrations.py
git commit \
  -m "feat(migration): rename the horse saddle slot at format 71" \
  -m "Rewrite only complete command slot tokens in both directions." \
  -m "The rule is sourced to the Mojang 1.21.5 technical changelog."
```

## C24：实现 chain → iron_chain 标识符规则

命令中只替换完整 resource-location atom；JSON 只替换精确 scalar value，不改对象 key。对象 key 可能是作者自定义 map，缺少所属 schema 时不能安全解释。

### Commit

```bash
git add dpcompat/migrations/identifiers.py tests/test_build.py
git commit \
  -m "feat(migration): migrate the iron-chain identifier boundary" \
  -m "Rewrite exact command atoms and JSON scalar values without renaming user-defined object keys or matching substrings." \
  -m "Both upgrade and downgrade are covered by archive tests."
```

## C25：实现 spawnpoint/setworldspawn rotation

旧版 angle 升级时补 pitch=0；新版降级只有 pitch 为零才无损，非零返回 `LOSSY`。不要忽略 1.21.9 的执行维度变化；在安全模型文档中单列它。

### Commit

```bash
git add dpcompat/migrations/commands.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate spawn rotation command grammar" \
  -m "Add zero pitch on upgrade and require explicit lossy policy before discarding non-zero pitch on downgrade." \
  -m "Command shapes outside the reviewed grammar are left untouched."
```

## C26：实现 1.21.11 gamerule registry 名称

### 必须覆盖

- 所有普通 camelCase → `minecraft:snake_case`；
- Mojang 列出的特殊 rename；
- disable* 三项显式赋值时反转 true/false；
- 查询被反转的规则时返回 `UNSUPPORTED`，因为 command result 语义不能靠改名恢复；
- `doFireTick`/`allowFireTicksAwayFromPlayer` 返回 `UNSUPPORTED`，要求作者改写为 fire spread radius；
- 宏和非 Minecraft namespace 返回 `UNKNOWN`。

### Commit

```bash
git add dpcompat/migrations/gamerules.py tests/test_command_boundaries.py
git commit \
  -m "feat(migration): migrate the namespaced game-rule registry" \
  -m "Handle ordinary and special 1.21.11 renames, invert explicit disable-rule assignments, and block removed fire-rule semantics." \
  -m "Queries whose returned boolean changes remain unsupported."
```

## C27：实现 worldborder 时间语法并阻断语义漂移

升级旧数字时补 `s`，降级 `s`/整秒 ticks 时可生成旧语法；但 1.21.11 把插值从 real time 改成 game ticks，因此凡是带时间的命令都产生 `UNKNOWN`，默认不发布。显式 `allow_unknown` 只表示作者接受该差异。

### Commit

```bash
git add dpcompat/migrations/worldborder.py tests/test_command_boundaries.py
git commit \
  -m "feat(migration): expose the world-border timing semantic break" \
  -m "Normalize duration units while refusing to label real-time and game-tick interpolation as equivalent." \
  -m "Default policy blocks timed world-border migrations."
```

## C28：实现 filtered loot function

`modifier` ↔ `on_pass` 可逆；存在 `on_fail` 时不能降级。只在 function ID 明确为 `minecraft:filtered` 时处理。

### Commit

```bash
git add dpcompat/migrations/resources.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate filtered loot branches at format 94.1" \
  -m "Rename modifier to on_pass and reject downgrade when the new on_fail behavior is present." \
  -m "Traversal is limited to typed filtered loot-function objects."
```

## C29：实现 26.1 world clock 边界

覆盖 timeline 默认 overworld clock、time_check predicate 和 test environment time；自定义 clock/time marker 降级均不可表达。

### Commit

```bash
git add dpcompat/migrations/resources.py dpcompat/migrations/recipes.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate representable world-clock defaults" \
  -m "Insert or remove only the vanilla overworld default and block custom clocks, time markers, and non-equivalent test environments." \
  -m "Rules are independently source-attributed to 26.1."
```

## C30：实现 26.1 recipe 的可逆子集

只处理 result short/object form、无 recipe book 的 group、show_notification 默认值和受限 transmute 字段。新 crafting_dye/imbue 无通用旧版等价物，必须阻断。

### Commit

```bash
git add dpcompat/migrations/recipes.py tests/test_migrations.py
git commit \
  -m "feat(migration): migrate the reviewed 26.1 recipe subset" \
  -m "Normalize reversible result and default fields while rejecting new recipe behavior without a general legacy equivalent." \
  -m "No special recipe is guessed from hardcoded vanilla ingredients."
```

## C31：接入 strict JSON normalization

只有跨入 format 80 才统一重写 JSON。重复键之前已由 loader 拒绝，所以 normalization 不会静默覆盖数据。

### Commit

```bash
git add dpcompat/migrations/strict_json.py tests/test_jsonutil.py
git commit \
  -m "feat(migration): normalize JSON at the strict-parser boundary" \
  -m "Emit deterministic standards-compliant JSON for format 80+ after semantic migrations complete." \
  -m "Duplicate keys remain hard errors."
```

## C32：登记内置规则顺序与一手来源

先写 `migrations/sources.py`，确保每个 ID 有 Mojang URL；再写 `migrations/__init__.py` 的 `BUILTIN_RULES` 顺序。registry 创建时缺 source 应立即失败。

### Commit

```bash
git add dpcompat/migrations/sources.py dpcompat/migrations/__init__.py tests/test_rules.py
git commit \
  -m "feat(rules): register ordered built-ins with primary sources" \
  -m "Make rule order and Mojang changelog provenance part of the compiler contract." \
  -m "Tests reject unsourced built-in registrations."
```

## C33：定义声明式规则 schema

`rules/schema.py` 只提供两个窄操作：scoped exact JSON value、scoped JSON key rename。每项必须声明 include glob；路径不能绝对、不能含 `..`；lossless rule 必须双向；至少一个官方 URL。

### Commit

```bash
git add dpcompat/rules/schema.py tests/test_rules.py
git commit \
  -m "feat(rules): define a strict declarative rule schema" \
  -m "Allow only explicitly scoped exact-value and key-rename operations with bidirectional lossless definitions and primary sources." \
  -m "Reject broad, path-escaping, one-way, and unsourced rule files."
```

## C34：执行声明式规则

读取 JSON → Pydantic 校验 → 按 include 选择文件 → 深度复制变换 → destination conflict 报错 → 统一 dump。不能实现任意 regex replacement。

### Commit

```bash
git add dpcompat/rules/declarative.py tests/test_rules.py
git commit \
  -m "feat(rules): execute context-scoped declarative migrations" \
  -m "Apply validated operations only to matching JSON files and fail on rename conflicts or missing directions." \
  -m "Tests prove unrelated JSON remains unchanged."
```

## C35：实现 Python/entry point 规则 registry

支持：内置规则、`[rules].modules` 的 `RULES`/`dpcompat_rules()`、`dpcompat.rules` entry point、`[rules].files`。拒绝重复 ID、无 source、无 applies/apply、非法 priority。

### Commit

```bash
git add dpcompat/rules/__init__.py dpcompat/rules/registry.py tests/test_rules.py
git commit \
  -m "feat(rules): discover validated Python and file extensions" \
  -m "Compose built-ins, opt-in modules, installed entry points, and declarative files under one duplicate-safe ordered registry." \
  -m "Every extension rule must expose a stable id and primary source."
```

## C36：实现静态 scanner

按 namespace/path/JSON/command/resource type/identifier 顺序扫描，产出 evidence 与 diagnostics。scanner 可以提高 inferred minimum，不能证明完整语义等价。

### Commit

```bash
git add dpcompat/scanner.py tests/test_scanner.py
git commit \
  -m "feat(scan): infer feature minimums and target blockers" \
  -m "Collect source-located evidence for known resources, identifiers, commands, strict JSON, and unsupported downgrade semantics." \
  -m "Unknown resource directories are retained but never silently certified."
```

## C37：实现来源版本 detector

优先 metadata 精确值；range 内选择有依据的最新登记 format；内容只能提高 minimum；冲突时 diagnostic。显式 `--source-format` 由 engine 记录 override 证据。

### Commit

```bash
git add dpcompat/detector.py tests/test_build.py
git commit \
  -m "feat(detect): reconcile metadata and content evidence" \
  -m "Select a source syntax from declared ranges and registered formats while surfacing contradictions and confidence." \
  -m "Ambiguity remains visible to callers and reports."
```

## C38：实现严格 TOML 配置

`ProjectConfig`、`RuleSettings` 使用 Pydantic；所有相对路径以 TOML 所在目录解析；未知 section/key 报错；CLI override 后仍触发 assignment validation。

### Commit

```bash
git add dpcompat/config.py dpcompat.example.toml tests/test_config.py
git commit \
  -m "feat(config): validate builds policies fallbacks and rule extensions" \
  -m "Resolve project paths relative to the TOML file and reject duplicate targets, unsafe names, unknown fields, and malformed policy values." \
  -m "CLI overrides preserve Pydantic assignment validation."
```

## C39：实现作者审核 fallback

`.dpcompat-fallback.toml` 只允许精确 delete、精确 diagnostic code/path resolution 和非空 reason；不存在通配 suppression。复制 fallback 前拒绝 symlink。

### Commit

```bash
git add dpcompat/fallback.py tests/test_config.py tests/test_migrations.py
git commit \
  -m "feat(fallback): merge explicit target implementations safely" \
  -m "Support exact deletions and source-located diagnostic resolutions with written author reasons." \
  -m "Unused resolutions and missing delete targets remain visible."
```

## C40：实现隔离的单目标构建事务

顺序固定为：复制 effective source → 运行规则 → 合并 fallback → 重新扫描 → policy 门禁 → 重写 mcmeta → 生成 ZIP。任何 ERROR 都先删临时 target，不得发布半成品。

### Commit

```bash
git add dpcompat/engine.py tests/test_build.py tests/test_migrations.py
git commit \
  -m "feat(build): compile targets in isolated fail-closed transactions" \
  -m "Apply ordered rules, reviewed fallbacks, post-migration scans, policy gates, metadata rendering, and deterministic publication per target." \
  -m "A failed rule or diagnostic never publishes a target archive."
```

## C41：组装带 guard 的 universal overlay

基础层只放警告 load function；每个唯一 pack format overlay 放完整 `data/` 并用 `replace: true` 覆盖 guard load tag。这样未来未登记 format 落进大范围时不会静默加载空功能。

### Commit

```bash
git add dpcompat/engine.py tests/test_build.py
git commit \
  -m "feat(build): package complete guarded universal overlays" \
  -m "Store one complete data tree per successful unique format over a warning-only base layer." \
  -m "Every known overlay disables the unknown-format guard; unbuilt formats remain explicit."
```

## C42：生成机器可读报告

报告保存 detection、policy、target 状态、rule records、diagnostics、archive hash、universal path 和有效 rule registry。所有 URL、Path、enum 必须 JSON 可序列化。

### Commit

```bash
git add dpcompat/report.py tests/test_build.py
git commit \
  -m "feat(report): emit auditable compatibility reports" \
  -m "Record source inference, policies, applied rules, diagnostics, hashes, artifacts, and effective rule provenance for each build." \
  -m "Reports distinguish successful syntax output from author-accepted semantic risk."
```

## C43：实现 Rich CLI

命令：`version`、`versions`、`rules`、`inspect`、`plan`、`validate`、`build`、`server-check`。表格用于人读，`--json` 用于 CI。CLI 只做参数适配，不放 Minecraft 转换逻辑。

### Commit

```bash
git add dpcompat/cli.py tests/test_logging.py
git commit \
  -m "feat(cli): expose Rich inspection planning and builds" \
  -m "Render release, rule, diagnostic, and target tables while retaining JSON output and stable exit codes for automation." \
  -m "Configure queued module logs at the process boundary."
```

## C44：实现原版 server smoke check

只接受用户提供的 server JAR；没有 `--accept-eula` 立即拒绝；临时世界最小化；检测 ready/error markers；超时后 terminate/kill；不宣称 load success 等于玩法正确。

### Commit

```bash
git add dpcompat/servercheck.py tests/test_servercheck.py
git commit \
  -m "feat(validation): smoke-test archives with a supplied vanilla server" \
  -m "Require explicit EULA acknowledgement, enforce timeouts, inspect load errors, and avoid downloading or retaining server files implicitly." \
  -m "Unit tests mock the process boundary; release verification uses matching real JARs."
```

## C45：加入可审查的样例包

先加 `simple_pack` 验证最小 chain/overlay；再加 `research_fixture`，覆盖 text、entity equipment、saddle slot、chain、spawn rotation、gamerule、tooltip。样例必须小到能逐行审查。

### Commit

```bash
git add examples
git commit \
  -m "test(fixtures): add minimal researched migration packs" \
  -m "Exercise representative 1.21.4-to-1.21.11 boundaries without treating synthetic fixtures as proof for an unrelated production pack." \
  -m "Fixtures remain human-readable and source-attributed by the rule catalog."
```

## C46：整理 pytest 测试层次

### 文件顺序

1. `tests/helpers.py`；
2. value/config/manifest tests；
3. parser tests；
4. individual migration tests；
5. rule extension tests；
6. I/O/security tests；
7. logging/CLI tests；
8. research fixture 和 build tests；
9. documentation contract tests。

### 最终验收

```bash
uv run pytest -q
uv run pytest --cov=dpcompat --cov-report=term-missing
```

### Commit

```bash
git add tests pyproject.toml
git commit \
  -m "test: organize unit integration security and fixture coverage" \
  -m "Run all legacy unittest-style cases under pytest and add native pytest coverage for Pydantic schemas, rule discovery, logging, command boundaries, and end-to-end builds." \
  -m "Strict markers and xfail settings prevent accidental silent skips."
```

## C47：编写用户与开发者文档

按以下顺序写：README → architecture → official change audit → rule authoring → safety model → real-pack testing → development guide → release checklist → source list。任何“支持”结论必须能指向代码测试和 Mojang URL。

### Commit

```bash
git add README.md docs CONTRIBUTING.md
git commit \
  -m "docs: document architecture rules safety and reconstruction" \
  -m "Explain user workflows, module ownership, official version evidence, extension contracts, fail-closed policy, production-pack verification, and this file-by-file build path." \
  -m "docs/ keeps only developer-facing material; the reconstruction guide and the new-version guide are the entry points."
```

## C48：配置 CI 与发布门禁

CI 矩阵至少 Python 3.12/3.13，顺序为 `uv sync --locked --all-groups` → ruff format/check → mypy → pytest → smoke build → `uv build`。发布前再用每个唯一 pack format 的 server JAR 和真实数据包测试。

### Commit

```bash
git add .github CHANGELOG.md docs/RELEASE_CHECKLIST.zh-CN.md
git commit \
  -m "ci: enforce typed tested reproducible releases" \
  -m "Run locked dependency sync, Ruff, strict mypy, pytest, fixture builds, archive checks, and package builds across supported Python versions." \
  -m "The release checklist separately requires matching vanilla servers and production-pack behavior evidence."
```

## C49：支持多包 bundle 的数据包根选择

在 `packio.py` 中先列出浅层 `pack.mcmeta` 候选：只有一个候选时直接使用；多个候选但只有一个带 `data/` 时把它识别为数据包；多个数据包时失败并打印候选。`--pack-root` 与 `[build].pack_root` 只接受不含空段、`.`、`..` 或绝对根的相对 POSIX 路径，解析后再次确认没有逃出解压目录。

### 验收

```bash
uv run pytest -q tests/test_packio.py tests/test_config.py tests/test_cli.py
uv run mypy dpcompat/packio.py dpcompat/config.py dpcompat/cli.py
```

### Commit

```bash
git add dpcompat/packio.py dpcompat/config.py dpcompat/cli.py \
  dpcompat.example.toml tests/test_packio.py tests/test_config.py tests/test_cli.py
git commit \
  -m "feat(io): select data packs safely from multi-pack bundles" \
  -m "Prefer the unique pack root containing data and expose a validated explicit selector when multiple data packs coexist." \
  -m "Reject absolute traversal and escaped roots at both the Pydantic and resolved-filesystem boundaries."
```

## C50：让 scanner 区分 schema 类型与文件用途

所有通用 JSON key 都先做类型收窄，不能假设 `type` 一定是可哈希字符串。路径检查按运行时后缀分类：JSON、mcfunction、NBT 的非法路径是 error；README/TXT 等作者说明是 warning。不要因为测试包“能用”就关闭运行时路径门禁。

### 验收

```bash
uv run pytest -q tests/test_scanner.py
uv run ruff check dpcompat/scanner.py tests/test_scanner.py
```

### Commit

```bash
git add dpcompat/scanner.py tests/test_scanner.py
git commit \
  -m "fix(scan): narrow schema values and classify non-runtime files" \
  -m "Treat only string discriminators as text component types and retain strict path errors for JSON functions and NBT." \
  -m "Documentation files under data remain visible as warnings without crashing or blocking otherwise valid builds."
```

## C51：迁移引号宏和递归实体文本组件

先在 `commands.py` 写一个有限状态扫描器，区分占位符是否位于引号标量。只有全部占位符都在引号内时，text/entity 规则才能解析并改写静态外层。实体变换要递归 `Passengers`，upgrade 解包 `CustomName` 与 `text_display.text` 的 legacy JSON string，downgrade 用紧凑 JSON 重包；失败单独放入 `unknowns`，不能混成普通 lossy warning。

### 验收

```bash
uv run pytest -q tests/test_migrations.py tests/test_command_boundaries.py
uv run mypy dpcompat/commands.py dpcompat/entity_data.py dpcompat/migrations
```

至少覆盖：引号宏 upgrade、未引号宏失败、嵌套 Passenger upgrade、嵌套 Passenger downgrade、CustomName、text_display.text。

### Commit

```bash
git add dpcompat/commands.py dpcompat/entity_data.py \
  dpcompat/migrations/text.py dpcompat/migrations/entities.py \
  tests/test_migrations.py tests/test_command_boundaries.py
git commit \
  -m "fix(migration): preserve scalar macros and inline nested entity text" \
  -m "Migrate statically known component structure around quoted placeholders and recurse through passenger entity NBT." \
  -m "Structure-generating macros and unparseable embedded components continue to fail closed with source-located unknown diagnostics."
```

## C52：去重来源诊断并加入真实包验证

原始 source 与展平后的 effective source 都要扫描，但完全相同的 Pydantic diagnostic 只能出现一次。按完整 JSON 表示去重，而不是只按 code 去重，否则会吞掉其他路径上的真实问题。真实包文档必须记录输入哈希、来源层、每边界命中、产物检查和没有执行的验证层级。

### Commit

```bash
git add dpcompat/engine.py tests/test_build.py
git commit \
  -m "fix(report): deduplicate identical source diagnostics" \
  -m "Preserve first-seen diagnostic order while suppressing only exact repeats from source and effective-source scans." \
  -m "Real-pack validation evidence is recorded outside the repository because it references user-provided bundles."
```

## C53：发布真实包反馈版本

同步 README、架构、安全模型、版本矩阵、官方审计、CHANGELOG、pyproject、`__version__` 与 lockfile。运行全量门禁，再连续构建真实包两次并比较所有 ZIP 哈希；归档时排除 `.venv`、cache、日志和临时验证目录。

### 最终验收

```bash
uv lock --check
make check
make smoke
uv build
uv run dpcompat version
```

### Commit

```bash
git add README.md CHANGELOG.md pyproject.toml uv.lock dpcompat/__init__.py docs tests/test_documentation.py
git commit \
  -m "release: publish DPCompat 0.3.1" \
  -m "Integrate multi-pack input hardening, real macro and entity-text regressions, and a source-backed production-pack audit." \
  -m "All repository quality gates and deterministic archive checks pass; vanilla server and gameplay validation remain explicit release follow-ups."
```

---

## C54：插件存储与内置插件目录

`plugins.py` 提供用户级插件存储：`DPCOMPAT_PLUGIN_DIR`（默认 `~/.dpcompat/plugins`）下的 `.py`/`.json` 插件文件，`plugins.toml` 持久化启用状态。内置规则按 `sources.py` 边界分组为 13 个具名插件，目录校验必须覆盖全部 `BUILTIN_RULES`。CLI 增加 `plugin install/remove/enable/disable/list`；`create_rule_registry` 接受 `enabled_rule_ids` 过滤，`cli._registry` 改走 `create_effective_registry`。插件文件格式与安全要求写入 `docs/PLUGIN_DEVELOPMENT.zh-CN.md`。

### Commit

```bash
git add dpcompat/plugins.py dpcompat/rules/registry.py dpcompat/cli.py tests/test_plugins.py tests/test_cli.py
```

## C55：Textual TUI

`ui/` 提供 Textual 界面：主屏选择数据包路径（含文件树浏览）、勾选目标版本与策略、启动迁移并输出日志；插件屏浏览/安装/开关插件。构建在 worker 线程执行，UI 更新经 `call_from_thread` 回主线程。`dpcompat tui` 入口；`tests/test_tui.py` 用 `run_test` 冒烟。

### Commit

```bash
git add dpcompat/ui dpcompat/cli.py tests/test_tui.py pyproject.toml uv.lock
```

## C56：发布 0.4.0

同步版本号、CHANGELOG、README 与文档；pyright 加入 dev 依赖与 `typecheck` 门禁；用 `test_datapack/`（真实包，不入库）做静态验证。

### 最终验收

```bash
uv lock --check
make check
make smoke
uv build
uv run dpcompat plugin list
uv run dpcompat tui
```

### Commit

```bash
git add README.md CHANGELOG.md pyproject.toml uv.lock dpcompat/__init__.py Makefile scripts/build.ps1 docs
```

## 2. 从 C04 继续时的建议

如果你已经按旧文档写到 C04，不要把旧 dataclass 上的业务代码继续往后堆。推荐这样迁移：

1. 保留你已经写好的 `logging_config.py` 和 release JSON；
2. 将 `models.py` 改为本指南 C04 的 Pydantic 结构；
3. 让 C05/C06 的 loader 直接 `model_validate()` 原始 JSON；
4. 先运行 `tests/test_versions.py` 与 mypy；
5. 从 C07 继续，不要从旧 0.2 项目复制完整 engine；
6. 每完成一个边界规则，先补一条正向、一条反向、一条不可迁移测试，再登记到 C32。

## 3. 最终人工审查清单

- [ ] 每个 release 有 pack format、日期、Java 和 Mojang URL；
- [ ] 每个内置/扩展 rule 有唯一 ID、边界和一手来源；
- [ ] lossless 规则有正反向测试；
- [ ] 字段冲突、宏、自定义 namespace 不被猜测；
- [ ] 迁移后重新扫描，而不是只信任规则返回值；
- [ ] failed target 没有 ZIP；
- [ ] universal 的未知 format guard 有回归测试；
- [ ] `make check`、`make smoke`、`make build` 全部通过；
- [ ] 真实数据包逐版本进入匹配原版服务器；
- [ ] GameTest 或玩法断言验证作者真正关心的行为。

静态编译、server 成功加载、玩法语义一致是三个不同层级。DPCompat 可以自动完成前两个层级中的大部分工作，但第三层必须由数据包作者定义可观察行为并提供测试。
