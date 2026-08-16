# 编码约定与质量门禁（AI 版）

## 风格

- Python 3.12+，`from __future__ import annotations`，类型注解完整（mypy `strict`）。
- 行宽 120；格式化由 `ruff format`（双引号、空格缩进）强制执行。
- ruff lint 选择集：`E, F, I, UP, B, SIM, RUF`。允许全角标点（`：，；（）。`）在中文 UI/诊断文案中。
- 提交前运行 `ruff format .` 与 `ruff check --fix .`，不要手排导入。

## 类型契约

- 外部 schema / 跨模块结果：Pydantic `BaseModel`，`model_config = ConfigDict(extra="forbid", ...)`。
- 不可变值对象：`FrozenModel`（frozen + extra forbid），如 `PackFormat`、`Diagnostic` 之外的模型子集。
- 解析器内部：`@dataclass(slots=True, frozen=True)` 之类，避免 Pydantic 开销。
- 不要把 `dict[str, Any]` 从配置/插件/manifest 直接传进 engine；先在边界校验。
- 特殊构造签名：`PackFormat(71)` 紧凑写法、`Diagnostic(Severity.ERROR, "code", "message")` 位置参数写法、`MigrationRecord("id", Compatibility.X, 0)` 写法——这些 `__init__` 重载已在 models.py 定义，新代码沿用，不要绕过。

## 诊断约定

- `Diagnostic.severity`：`INFO(10)` / `WARNING(20)` / `ERROR(30)`。`policy_diagnostic`（`migrations/common.py`）按 `context.policy.permits(compatibility)` 决定 WARNING 还是 ERROR。
- `code` 必须小写 `[a-z0-9-]`，语义唯一（如 `resource-too-new`、`filtered-on-fail-cannot-downgrade`）。
- 每条诊断尽量带 `path`（相对包根）、`line`、`compatibility`、`rule_id`、`details`。
- 规则内诊断用 `policy_diagnostic`；扫描器诊断用 `_feature_diagnostic`（带 `source` URL）。

## 日志

- `logging_config.py` 提供 QueueListener + Rich console + 旋转总日志与按模块拆分日志（`engine.log`、`migrations.log`、`rules.log`、`io.log`）。
- 规则内用 `logger.debug` 级别；不要 print。

## 门禁命令（Windows 用 `scripts/build.ps1` 同名目标）

| 命令 | 内容 | 失败意味着 |
| --- | --- | --- |
| `make lint` | `ruff format --check` + `ruff check` | 格式/风格违规 |
| `make typecheck` | `mypy`（strict）+ `pyright` | 类型不健全 |
| `make test` | `pytest -q` | 行为回归 |
| `make check` | lint + typecheck + test | 提交不可合入 |
| `make smoke` | `dpcompat versions` + `inspect examples/simple_pack` | 安装/启动破坏 |
| `make build` | check + `uv build` | 包不可构建 |

CI 额外执行：`uv sync --locked --all-groups`（锁文件一致性）、样例 `plan`/`build`、`python -m zipfile -t`（ZIP 校验）、Windows 上的 PowerShell 等价门禁。

## 提交约定

- Conventional Commits 标题；正文写：动机、安全条件、失败条件、证据来源、实际测试命令。
- 一个 commit 一个可独立验证的边界。
- 新增依赖：先改 `pyproject.toml` 再 `uv lock`；CI 用 `--locked`，锁文件必须与 pyproject 一致。

## 常见陷阱

- `pydantic` 的 `Field` 与自定义 `__init__` 混用：新模型若加字段，检查 `__init__` 签名（如 `PackFormat`、`Diagnostic`、`MigrationRecord` 有显式位置参数重载）。
- `frozenset` 字段（`VersionProfile.capabilities`）在 model_dump(mode="json") 时自动转 list，无需手动处理。
- Windows：`Path.resolve()` 会产生 8.3 短路径差异，测试比较路径时统一 `resolve()`（见 `tests/helpers.py` 注释）。
- 不要在规则模块顶层做 I/O；插件文件会被多次加载。
- TUI 中所有用户可见文本必须走 `i18n.tr`，见 UI_I18N.md。
