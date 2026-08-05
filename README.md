# DPCompat

DPCompat 是一个保守的 Minecraft Java Edition 数据包兼容性编译器。作者维护一份源数据包，工具识别其 pack format，跨越已登记的正式版边界执行有来源、可审查的规则，并输出各目标版本的独立 ZIP；需要时也可把这些结果组装为 pack overlays 通用包。

> 当前为 0.3 Alpha。DPCompat 只在能说明语义边界时自动转换；不能证明等价的内容默认以 `lossy`、`unsupported` 或 `unknown` 阻断目标，而不是生成一个“可能能加载”的包。

## 为什么不是全局替换

Minecraft 的变化同时涉及 JSON、命令、文本组件、物品组件、实体 SNBT、二进制 structure NBT、注册表和运行时语义。同一个 `value`、`id` 或 `equipment` 可能是 Mojang 字段，也可能只是作者 storage 中的数据。DPCompat 因此采用：

1. 根据 `pack.mcmeta` 与内容证据识别来源语法；
2. 展平来源版本实际会加载的已有 overlay；
3. 仅在明确的文件类型与语法位置执行方向化规则；
4. 对结果重新静态扫描并应用严格策略；
5. 只为通过门禁的目标写入确定性 ZIP；
6. 用完整目标结果自动组装 overlays，而不要求作者重复维护它们。

## 已登记的正式版

| Minecraft | 数据包格式 | Java |
| --- | ---: | ---: |
| 1.21.4 | 61 | 21 |
| 1.21.5 | 71 | 21 |
| 1.21.6 | 80 | 21 |
| 1.21.7 / 1.21.8 | 81 | 21 |
| 1.21.9 / 1.21.10 | 88.0 | 21 |
| 1.21.11 | 94.1 | 21 |
| 26.1 | 101.1 | 25 |
| 26.2 | 107.1 | 25 |

每条记录都在 `dpcompat/data/releases.json` 中携带 Mojang 正式版链接。登记版本不等于“该版本的所有语义都可移植”；准确覆盖范围见 [`docs/VERSION_MATRIX.md`](docs/VERSION_MATRIX.md)。

## 安装与快速开始

要求 Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --all-groups
uv run dpcompat versions
uv run dpcompat inspect examples/simple_pack
```

只规划，不写 ZIP：

```bash
uv run dpcompat plan examples/simple_pack \
  --target 1.21.4 --target 1.21.11 --target 26.2
```

构建指定版本与自动 overlay 通用包：

```bash
uv run dpcompat build examples/simple_pack \
  --target 1.21.4 --target 1.21.11 --target 26.2 \
  --output dist
```

ZIP 同时打包了数据包、资源包或其他目录时，若只有一个候选目录含 `data/`，DPCompat 会自动选择它；存在多个数据包时必须明确指定：

```bash
uv run dpcompat inspect Bundle.zip --pack-root path/to/datapack
uv run dpcompat build Bundle.zip --pack-root path/to/datapack --output dist
```

也可在 `[build]` 中设置安全的相对 POSIX 路径 `pack_root`。DPCompat 只迁移所选数据包，不迁移同一 bundle 内的资源包。

不传 `--target` 时使用配置中的目标，配置也未指定时构建所有登记正式版。`inspect`、`versions`、`rules` 和 `plan` 支持 `--json`；JSON 模式的标准输出不混入 Rich 进度信息。

## 配置

```bash
cp dpcompat.example.toml dpcompat.toml
uv run dpcompat build MyPack.zip --config dpcompat.toml
```

核心策略默认值：

```toml
[policy]
allow_emulated = true
allow_lossy = false
allow_unknown = false
fail_on_warnings = false
```

配置、manifest、诊断、报告边界与规则规格由 Pydantic 严格校验；未知 section、拼错的 key、重复 target、无效规则来源会在构建前失败。

## 可扩展规则

第三方开发者无需修改 engine，可通过三种方式追加规则：

- 项目 Python 模块：暴露 `RULES` 或 `dpcompat_rules()`；
- 安装包 entry point：注册到 `dpcompat.rules`；
- 声明式 JSON：用于路径受限的精确 JSON 值或 key 变换。

```toml
[rules]
modules = ["my_pack_rules"]
files = ["rules/example.json"]
load_entry_points = true
```

所有规则 ID 必须唯一，必须声明 pack-format 边界和至少一个一手来源。无损声明式规则必须同时定义 upgrade 与 downgrade；目标字段冲突会报错，不会覆盖。完整接口和例子见 [`docs/RULE_AUTHORING.zh-CN.md`](docs/RULE_AUTHORING.zh-CN.md)。

## 作者 fallback

目标机制不存在或项目无法证明映射时，作者可只为该目标提供审查过的替代文件，不需要手写整套 overlay：

```toml
[fallbacks]
"1.21.4" = "compat/1.21.4"
"94.1" = "compat/format-94.1"
```

fallback 目录按包根组织。`.dpcompat-fallback.toml` 可精确删除路径，或用 `code`、可选 `path` 与非空 `reason` 解决一条已知诊断。通配抑制不受支持；未使用的 resolution 会产生 warning。fallback 记为 `emulated`，合并后仍重新扫描。

## 输出与日志

```text
dist/
├─ my-pack-1.21.4.zip
├─ my-pack-1.21.11.zip
├─ my-pack-26.2.zip
├─ my-pack-universal-1.21.4-plus.zip
└─ compatibility-report.json
```

CLI 使用 Rich 输出表格、诊断和进度。默认日志位于 `logs/`：

- `application.log`：应用总日志；
- `errors.log`：错误日志；
- `engine.log`、`migrations.log`、`rules.log`、`io.log`：按 Python 模块前缀拆分。

可用全局选项 `--verbose`、`--quiet`、`--log-dir PATH` 调整。例如：

```bash
uv run dpcompat --verbose --log-dir build-logs plan MyPack.zip --target 1.21.5
```

## 验证层级

仓库测试和 `examples/research_fixture` 验证转换器行为，但它们不是第三方真实数据包的玩法证明。正式发布应依次完成：

1. `make check`：Ruff、严格 mypy、pytest；
2. 构建并检查 ZIP 与报告；
3. 用每个目标版本匹配的原版 server JAR 执行 `server-check`；
4. 用数据包作者定义的 GameTest 或可观察玩法断言验证行为。

```bash
uv run dpcompat server-check dist/my-pack-1.21.11.zip \
  --server-jar /path/to/server-1.21.11.jar \
  --java /path/to/java \
  --accept-eula
```

DPCompat 不下载 server JAR，也不替用户默示接受 EULA。`server-check` 证明“服务端启动且日志未匹配已知加载错误”，不证明玩法完全等价。详见 [`docs/REAL_PACK_TESTING.zh-CN.md`](docs/REAL_PACK_TESTING.zh-CN.md)。

## 开发

```bash
make sync
make format
make check
make smoke
make build
```

Windows（无 GNU Make 时）使用等价的 PowerShell 命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 check
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 smoke
```

`build.ps1` 支持 `sync/format/lint/typecheck/test/test-verbose/coverage/check/build/smoke/clean`，与 Makefile 目标一一对应；`clean` 与 Makefile 共用 `scripts/clean.py` 的受控删除清单。

最重要的维护文档：

- [`docs/FROM_ZERO_FILE_BY_FILE.zh-CN.md`](docs/FROM_ZERO_FILE_BY_FILE.zh-CN.md)：C00–C53，从空目录逐文件编辑、验收与严密 commit；
- [`docs/ADDING_A_NEW_VERSION.zh-CN.md`](docs/ADDING_A_NEW_VERSION.zh-CN.md)：登记新正式版、新 feature 与新迁移规则的完整步骤；
- [`docs/OFFICIAL_CHANGE_AUDIT.zh-CN.md`](docs/OFFICIAL_CHANGE_AUDIT.zh-CN.md)：官方变更与实现结论；
- [`docs/ARCHITECTURE.zh-CN.md`](docs/ARCHITECTURE.zh-CN.md)：模块职责与数据流；
- [`docs/RULE_AUTHORING.zh-CN.md`](docs/RULE_AUTHORING.zh-CN.md)：Python、entry point 与声明式规则；
- [`docs/SAFETY_MODEL.zh-CN.md`](docs/SAFETY_MODEL.zh-CN.md)：为什么某些迁移必须失败关闭；
- [`docs/RELEASE_CHECKLIST.zh-CN.md`](docs/RELEASE_CHECKLIST.zh-CN.md)：发布门禁；
- [`docs/REAL_PACK_TESTING.zh-CN.md`](docs/REAL_PACK_TESTING.zh-CN.md)：真实数据包的移植验证流程。

## 硬边界

DPCompat 不是完整 Brigadier、DataFixerUpper 或每版本 registry/schema 镜像。位于已解析引号字符串中的宏占位符可以保留并迁移其静态外层结构；能在运行时生成 key、列表、compound 或整个命令参数的宏仍无法静态转换。任意物品组件命令语法、一般 Environment Attributes/worldgen 降级、新实体/方块/客户端能力也可能需要 fallback。遇到这些情况时，正确结果是精确诊断、作者 fallback 或放弃该目标，而不是猜测。

## 许可证

MIT。Minecraft 和 Mojang 是其各自权利人的商标；本项目与 Mojang Studios 或 Microsoft 无隶属关系。
