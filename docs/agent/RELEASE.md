# 发布流程（AI 版）

## CI/CD 接线

`.github/workflows/`：

- **ci.yml**：push/PR 触发。Linux 矩阵（Python 3.12/3.13）：`uv sync --locked --all-groups` → `make check` → `make smoke` → 样例 `plan`/`build` → `python -m zipfile -t` → `uv build`。Windows 任务：`scripts/build.ps1 check` + `smoke`。
- **release.yml**：`v*` 标签或手动触发。`uv build` → 上传 dist 工件 → `softprops/action-gh-release` 建 GitHub Release → 配置了 `PYPI_TOKEN` 时发布 PyPI（缺 token 只告警）。
- **wiki.yml**：main 分支上 README/CHANGELOG/docs/plugin-development/scripts/sync_wiki.py 变更时同步 GitHub Wiki；release 工作流末尾也会调用。需要 `WIKI_TOKEN`（repo scope PAT），缺省跳过。

## 发布步骤

1. 更新 `CHANGELOG.md`（新版本条目：Added/Changed/Fixed/验证范围）。
2. 本地全量门禁：`make check` + `make smoke` + `uv build`；确认 `uv lock --check`。
3. 打标签推送：`git tag vX.Y.Z && git push origin vX.Y.Z`。
4. release.yml 自动：构建发行包、GitHub Release、PyPI 发布（若有 token）、Wiki 同步（若有 token）。
5. 发布后按 `docs/RELEASE_CHECKLIST.zh-CN.md` 逐项确认（release notes、插件文档位置、server-check 证据等）。

## 版本号

`pyproject.toml` 的 `version` 与 `dpcompat/__init__.py` 的 `__version__` 同步（内置插件用 `__version__` 作为自身版本）。改动两处保持一致。

## 文档同步义务

发布或重大功能变更时检查：

- `docs/VERSION_MATRIX.md`（边界行：自动转换 vs 仅诊断/fallback）
- `docs/OFFICIAL_CHANGE_AUDIT.zh-CN.md`（官方事实与实现决策）
- `docs/SOURCES.md`（一手来源清单）
- `docs/agent/`（子系统行为变化时）
- `plugin-development/PLUGIN_DEVELOPMENT.zh-CN.md`（插件契约变化时）
- `scripts/sync_wiki.py` 的 PAGES 映射（新增/移动文档时）
