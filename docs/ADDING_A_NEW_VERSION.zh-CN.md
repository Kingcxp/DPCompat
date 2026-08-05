# 为新 Minecraft 版本添加迁移支持

本指南把“支持一个新版本”拆成四个可独立验收的层次：登记版本、登记 feature 事实、添加迁移规则、更新文档与门禁。每层都必须在提交前通过对应测试。

## 1. 登记正式版（releases.json）

在 `dpcompat/data/releases.json` 的 `releases` 数组末尾按发布日期追加：

```json
{
  "game_version": "26.3",
  "pack_format": [113, 1],
  "release_date": "2026-09-01",
  "java_major": 25,
  "note": "短说明",
  "capabilities": ["new_feature_marker"],
  "official_url": "https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3"
}
```

约束（`ReleaseManifest` 校验）：

- `game_version` 唯一，`release_date` 必须按升序排列；
- `pack_format` 不得小于前一条记录；
- `official_url` 必须是 Mojang 正式版说明；
- 同一 pack format 的 hotfix 版本可以并列（如 1.21.9/1.21.10），universal overlay 只会为唯一 format 生成一层。

提交前运行 `uv run pytest tests/test_versions.py tests/test_manifests.py -q` 与 `uv run dpcompat versions --json`。

## 2. 登记 feature 最低版本（features.json）

在 `dpcompat/data/features.json` 的 `features` 数组追加“该版本首次出现的语法事实”：

```json
{
  "id": "example_26_3_resources",
  "min_format": [113, 1],
  "resource_types": ["new_resource_dir"],
  "commands": ["newcommand"],
  "identifiers": ["minecraft:new_item"],
  "downgrade": "unsupported",
  "source": "https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3"
}
```

规则：

- `min_format` 是语法首次进入正式版的 pack format，不是版本字符串；
- 至少填写一种 matcher（`resource_types` / `commands` / `identifiers`），否则 manifest 校验失败；
- `downgrade` 说明降级分类（`lossless` / `emulated` / `lossy` / `unsupported` / `conditional`，`conditional` 会映射为 `unknown`）；
- `source` 必须是一手 Mojang 链接。

scanner 会利用这些记录：资源目录、命令前缀、精确标识符命中时提高推断最低版本，并在目标低于 `min_format` 时产出 `resource-too-new` / `command-too-new` / `identifier-too-new` 错误。

## 3. 添加迁移规则

### 3.1 选择形态

| 形态 | 适用 | 参考实现 |
| --- | --- | --- |
| 声明式 JSON 规则 | 精确值替换、明确 key 重命名、有限文件 glob | `rules/declarative.py` + `docs/RULE_AUTHORING.zh-CN.md` |
| 内置 Python 规则 | 命令、SNBT/NBT、跨文件引用、条件语义 | `migrations/` 下各模块 |

### 3.2 内置 Python 规则步骤

1. 在 `dpcompat/migrations/` 新建模块（或扩展现有同边界模块），实现 `MigrationRule` 协议：

```python
class ExampleRule:
    id = "example.change@113.1"
    boundary = PackFormat(113, 1)
    priority = 450

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        ...
```

2. 在 `migrations/sources.py` 为 `id` 登记 Mojang 一手来源；registry 发现缺来源会直接失败；
3. 在 `migrations/__init__.py` 的 `BUILTIN_RULES` 中按依赖顺序插入；若与既有规则存在语义顺序依赖，必须写注释和集成测试；
4. 在 `tests/test_migrations.py`（或同边界测试文件）补充：正向、反向、no-op、冲突、默认值、不可迁移输入、宏、幂等测试；
5. 用 `uv run dpcompat rules` 检查有效顺序与来源。

### 3.3 声明式规则步骤

1. 在项目 `[rules].files` 指向的 JSON 文件中定义 `DeclarativeRuleSpec`（schema 1）；
2. `lossless` 必须同时定义 `upgrade` 与 `downgrade`；
3. `include` 必须是包内安全 glob；`within_keys` 限制父 key 上下文；
4. 目标 key 冲突会报错而非覆盖。

## 4. 更新文档与门禁

1. `docs/OFFICIAL_CHANGE_AUDIT.zh-CN.md`：记录该版本的官方事实、实现决策与保守边界；
2. `docs/VERSION_MATRIX.md`：补充边界行（自动转换 vs 仅诊断/fallback）；
3. `docs/SOURCES.md`：追加一手来源；
4. `CHANGELOG.md`：登记新规则与验证范围；
5. 若新版本引入了既有版本没有的新 registry/命令，确保 scanner 的 `KNOWN_RESOURCE_TYPES` 与 `features.json` 同步；
6. 运行完整门禁：

```bash
uv lock --check
make check        # Windows: powershell -ExecutionPolicy Bypass -File scripts/build.ps1 check
make smoke
uv run dpcompat plan examples/simple_pack --target <新版本>
```

## 5. 验证层级提醒

- 仓库测试与 fixture 证明“转换器按设计运行”；
- 匹配原版 server JAR 的 `server-check` 证明“输出可加载”；
- 作者定义的 GameTest/行为断言才证明“玩法等价”。

三层不能互相替代；发布前至少完成前两层，并把第三层作为明确后续项写进 changelog 与 release notes。
