# 迁移规则编写规范

## 先选择扩展层级

| 规则形态 | 适用 | 不适用 |
| --- | --- | --- |
| 声明式 JSON | 精确值替换、明确对象 key 重命名、有限文件 glob | 命令、SNBT/NBT、跨文件语义、条件默认值 |
| 项目 Python 模块 | 单个数据包的复杂规则，和项目配置一起审查 | 需要发布复用但未打包的代码 |
| entry-point 包 | 多项目复用、独立版本和测试的规则库 | 临时一次性修复 |

## 声明式规则

示例 `rules/example.json`：

```json
{
  "schema": 1,
  "id": "example.rename-mode@94.1",
  "description": "Rename one key only below known parent keys.",
  "boundary": [94, 1],
  "compatibility": "lossless",
  "priority": 500,
  "official_sources": [
    "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11"
  ],
  "upgrade": [
    {
      "type": "json_rename_key",
      "include": ["data/*/loot_table/**/*.json"],
      "within_keys": ["filtered"],
      "old_key": "modifier",
      "new_key": "on_pass"
    }
  ],
  "downgrade": [
    {
      "type": "json_rename_key",
      "include": ["data/*/loot_table/**/*.json"],
      "within_keys": ["filtered"],
      "old_key": "on_pass",
      "new_key": "modifier"
    }
  ]
}
```

可用 operation 只有：

- `json_rename_key`：精确 key，目标 key 已存在则 error；
- `json_exact_value`：节点与 `old` 完全相等才替换为 `new`。

`include` 必须是包内安全 glob；`within_keys` 为空表示不限制父 key。lossless 必须实现双向，规则至少有一个 HTTP(S) 一手来源。加载方式：

```toml
[rules]
files = ["rules/example.json"]
```

声明式 DSL 故意不提供 regex、任意 Python 表达式、删除未知字段或全项目字符串替换。

## Python 规则协议

```python
from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat


class ExampleRule:
    id = "example.change@94.1"
    boundary = PackFormat(94, 1)
    priority = 450
    official_sources = ("https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11",)

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        # 分别处理 context.upgrading 与降级，并只修改已解析的上下文。
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (ExampleRule(),)
```

项目模块在 `[rules].modules` 中列出。可复用包在 `pyproject.toml` 中注册：

```toml
[project.entry-points."dpcompat.rules"]
example = "my_package.rules:dpcompat_rules"
```

provider 可返回单条规则或 iterable；也可直接导出 `RULES`。registry 会验证 ID、协议、priority、来源与重复项。

## 安全推理

1. `boundary` 是新语义首次进入支持正式版的 pack format，不是版本字符串。
2. upgrade 与 downgrade 分开实现；降级通常要检查默认值、冲突和信息丢失。
3. 只修改类型明确的位置，例如 summon 的 compound 参数或特定 recipe 类型。
4. 新旧字段同时存在时报告冲突，不决定谁覆盖谁。
5. 宏或未知语法返回 `UNKNOWN`；无目标机制返回 `UNSUPPORTED`。
6. `MigrationRecord.compatibility` 描述正常路径；单个异常输入可产生更差的 Diagnostic。
7. 规则运行后 scanner 仍会检查目标残留，因此不要依赖规则“自报成功”。

## 最低测试矩阵

每条双向规则至少覆盖：边界内 no-op、upgrade、downgrade、冲突、默认值、非默认有损/不支持、宏/未知语法、二次执行幂等、无效输入、完整 build 后无残留。声明式规则还要测 schema extra key、危险 glob、缺失方向和重复 ID。

提交正文应回答：为什么安全、证据是什么、哪些输入仍失败。C00–C53 的 commit 模板见 `FROM_ZERO_FILE_BY_FILE.zh-CN.md`。
