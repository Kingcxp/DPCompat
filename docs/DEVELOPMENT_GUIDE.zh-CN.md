# 开发指南速览

本文件用于日常定位；从空目录亲手编写时，以 `FROM_ZERO_FILE_BY_FILE.zh-CN.md` 的 C00–C53 为权威顺序。

## 环境和质量门禁

```bash
make sync
make format
make check
make smoke
make build
```

Windows 无 GNU Make 时使用等价命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 check
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 smoke
```

`make check` 依次运行 Ruff format check、Ruff lint、严格 mypy 和 pytest。新增依赖后先更新 `pyproject.toml`，再执行 `uv lock`；CI 使用 `uv sync --locked --all-groups`。

新增正式版/feature/规则时，按 [`ADDING_A_NEW_VERSION.zh-CN.md`](ADDING_A_NEW_VERSION.zh-CN.md) 的四层步骤执行。

## 改动路由

| 需求 | 首先编辑 | 同步更新 |
| --- | --- | --- |
| 正式版本 | `data/releases.json` | versions test、sources、matrix |
| 新资源/命令最低版本 | `data/features.json` | scanner test、official audit |
| 简单 JSON 变更 | 项目 declarative rule | schema/双向/build tests |
| 命令/SNBT/NBT 语义 | `migrations/` | `sources.py`、registry、双向/冲突 tests |
| 新配置 | Pydantic `config.py` | example TOML、CLI、invalid-input test |
| CLI 呈现 | `cli.py` | Rich 与 JSON stdout tests |
| 构建/overlay | `engine.py` / `packio.py` | deterministic/security/build tests |

## 规则开发循环

1. 从 Mojang 正式说明确定 pack-format 边界。
2. 写最小 old/new fixture 与失败测试。
3. 明确 upgrade、downgrade、冲突、默认值和不可逆输入。
4. 在最窄解析上下文实现；不要跨文件 regex。
5. 登记 `official_sources`，用 `dpcompat rules` 检查有效顺序。
6. 完整 build 后让 scanner 复核目标残留。
7. 更新版本矩阵、官方审计和 changelog。

## 模型约定

外部 schema 与跨模块结果使用 Pydantic，`extra="forbid"`；parser 内部 token/AST 可用 dataclass。不要把未验证 `dict[str, Any]` 从配置、插件或 manifest 直接传进 engine。

## 提交约定

一个 commit 完成一个可独立验证的边界。标题使用 Conventional Commits；正文至少写动机、安全条件、失败条件、来源和实际测试命令。逐提交模板见从零指南。
