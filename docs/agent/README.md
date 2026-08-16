# docs/agent — 给 AI 协作者的开发文档

本目录是 **AI 代理 / 协作者**在修改 DPCompat 前必须阅读的权威说明：仓库布局、子系统职责、不变量、质量门禁、验证流程与常见陷阱。它与面向人的文档（`docs/*.zh-CN.md`、`plugin-development/`）互补：人读的文档解释"为什么"，这里解释"代码在哪、改什么、怎么验证"。

## 阅读顺序

1. **README.md（本文件）** — 仓库布局、核心概念、第一性约束。
2. **ARCHITECTURE.md** — 模块地图、数据流、贯穿全局的不变量。
3. **CODING_CONVENTIONS.md** — 代码风格、类型契约、门禁命令。
4. **MIGRATION_RULES.md** — 迁移规则的协议、边界、安全模型与新增规则流程。
5. **PLUGIN_SYSTEM.md** — 插件存储、元数据契约、注册表接线。
6. **UI_I18N.md** — TUI 结构、多语言系统、插件本地化。
7. **TESTING.md** — 测试布局、约定、覆盖要求。
8. **RELEASE.md** — 发布流程与 CI/CD 接线。

## 项目是什么

DPCompat 是一个**保守的** Minecraft Java Edition 数据包兼容性编译器：

- 识别输入数据包的 `pack.mcmeta` 声明的 pack format（61 → 107.1）；
- 对每个已登记的目标正式版，从**同一份有效来源**独立构建，应用跨边界的迁移规则；
- 规则**失败关闭**：不能证明等价的转换被记为 `lossy`/`unsupported`/`unknown` 并默认阻断目标，而不是生成"可能能加载"的包；
- 通过门禁的目标输出确定性 ZIP；多目标时自动组装 pack overlay 通用包。

核心哲学：**不做全局字符串替换，只在证明过的语法上下文做结构化转换**。同名 `value`、`id`、`equipment` 可能是 Mojang 字段，也可能是作者 storage 数据。

## 仓库布局

```text
dpcompat/
├─ models.py          # Pydantic 核心值对象（PackFormat、Diagnostic、BuildPolicy、结果）
├─ versions.py        # releases.json 加载与版本解析
├─ manifests.py       # features.json（feature 最低版本事实）
├─ config.py          # dpcompat.toml 严格校验
├─ detector.py        # 来源格式识别（元数据 + 内容证据）
├─ scanner.py         # 只读静态扫描：证据、目标残留、unknown 阻断
├─ metadata.py        # pack.mcmeta 新旧格式渲染（pack_format ↔ min_format/max_format）
├─ packio.py          # 输入物化、overlay 展平、确定性 ZIP
├─ engine.py          # 构建编排：规则 → fallback → 重扫 → 策略 → ZIP → universal
├─ commands.py        # mcfunction 顶层 token 化（保守，非 Brigadier）
├─ snbt.py / nbt.py   # SNBT 解析 / 二进制 NBT
├─ text_components.py # 文本组件事件 upgrade/downgrade
├─ entity_data.py     # 实体 NBT 字段 upgrade/downgrade
├─ jsonutil.py        # 宽松/严格 JSON + 重复 key 拒绝
├─ migrations/        # 内置迁移规则（每个模块一个语义边界）
├─ rules/             # 规则注册表 + 声明式规则
├─ fallback.py        # 作者 fallback 机制
├─ report.py          # JSON 报告
├─ servercheck.py     # 原版 server JAR 外部进程验证
├─ i18n.py            # TUI 多语言表与偏好持久化
├─ cli.py             # argparse/Rich 适配
├─ ui/app.py          # Textual TUI（全部界面文本走 i18n）
└─ plugins.py         # 插件存储、元数据、内置插件目录、注册表接线

plugin-development/   # 面向插件开发者的文档（TUI 外的独立位置）
docs/                 # 面向维护者的文档 + docs/agent/（本目录）
data/releases.json    # 已登记正式版清单（game_version、pack_format、来源）
data/features.json    # feature 最低 pack format 事实
tests/                # pytest 测试
examples/             # simple_pack（冒烟样例）、research_fixture（规则回归夹具）
```

## 第一性约束（改代码前必须内化）

1. **外部/跨模块数据一律 Pydantic 且 `extra="forbid"`**；解析器内部 token/AST 用 dataclass。不要把未验证 `dict[str, Any]` 传进 engine。
2. **不修改用户输入**：所有迁移在临时副本上执行。
3. **每个目标从同一来源独立构建**，目标之间不串联。
4. **规则崩溃转为 error，失败目标不写 ZIP**。
5. **兼容性分类是门禁**：`lossless` 默认放行，`emulated` 默认放行但记录，`lossy`/`unknown` 默认拒绝，`unsupported` 永远拒绝。
6. **规则必须有 Mojang 一手来源**（`migrations/sources.py` / `official_sources`），注册表缺来源直接失败。
7. **方向化规则**：upgrade 与 downgrade 分开实现，`crosses(source, target, boundary)` 决定是否触发。
8. **扫描器是最后防线**：规则自报成功不算数，fallback/规则引入的目标残留由 scanner 复核。
9. **确定性输出**：ZIP 内容、时间戳、权限、JSON 序列化全部确定。

## 质量门禁（提交前必须全绿）

```bash
make sync        # uv sync --all-groups
make format      # ruff format + ruff check --fix
make check       # ruff format --check + ruff check + mypy(strict) + pyright + pytest
make smoke       # dpcompat versions + dpcompat inspect examples/simple_pack
make build       # check + uv build
```

Windows 无 GNU Make 时用 `scripts/build.ps1`（目标一一对应）。CI（`.github/workflows/ci.yml`）在 Linux（Python 3.12/3.13）与 Windows 上跑 `make check`、`make smoke`、样例 plan/build、ZIP 校验与 `uv build`。

## 常用命令

```bash
uv run dpcompat versions --json        # 已登记版本
uv run dpcompat rules --json           # 当前生效规则
uv run dpcompat inspect examples/simple_pack
uv run dpcompat plan examples/simple_pack --target 1.21.4 --target 26.2
uv run dpcompat build examples/simple_pack --target 1.21.4 --target 1.21.11 --output dist
uv run dpcompat plugin list
uv run pytest -q                       # 全量测试
```

## 不要做的事

- 不要加全局替换/正则改写迁移——写结构化规则或明确阻断。
- 不要为"看起来合理"的转换发明 Mojang 事实——每个边界变更先查官方说明（见 MIGRATION_RULES.md 的查证流程）。
- 不要绕过 `extra="forbid"` 或把 `**kwargs` 漏进 Pydantic 模型。
- 不要改 `BUILTIN_RULES` 顺序而不写注释与集成测试（顺序是编译契约的一部分）。
- 不要在迁移规则里做文件级字符串替换；`commands.py`/`snbt.py`/`nbt.py`/`jsonutil.py` 提供结构化解析。
