# DPCompat 插件开发指南

插件（plugin）是迁移规则的可安装、可开关的打包单元。每个插件都有稳定的 id、名称、描述和一组规则；插件列表（CLI `dpcompat plugin list` 或 TUI 插件管理页）直接展示名称与描述，让你和用户一眼看懂这个插件负责什么。

## 1. 概念

- **内置插件**：项目自带的 13 个规则分组（如 `gamerules@94.1`、`clocks@101.1`）。它们与用户插件一样可以被启用/禁用，禁用状态持久化。
- **文件插件**：你编写的 `.py`（Python 规则）或 `.json`（声明式规则）文件，通过 CLI 或 TUI 安装。
- **插件目录**：默认位于 **dpcompat 包旁边**（`site-packages/dpcompat/plugins`，editable/源码安装时为 `dpcompat/plugins/`），因此插件只属于当前 Python 环境中的这个 dpcompat——换一个 venv 或 pip 环境互不影响，Windows 与 Linux 行为一致。可用环境变量 `DPCOMPAT_PLUGIN_DIR` 覆盖（CI、容器或包目录只读的环境推荐使用）。启用状态保存在该目录的 `plugins.toml`，只有被禁用的插件会出现在其中（缺失即默认启用）。注意：`pip uninstall dpcompat` 不会删除插件目录，数据保留。
- **规则 id 全局唯一**：插件提供的规则 id 不能与内置规则或其他已安装插件冲突；安装时即校验，冲突直接拒绝。

## 2. Python 插件

一个 `.py` 文件即一个插件。结构：

```python
"""我的插件：演示一个自定义迁移规则。"""

from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat

PLUGIN = {
    "id": "my-pack-rules@94.1",
    "name": "我的数据包规则",
    "description": "把演示命名空间的旧字段改名为新字段，并处理 94.1 边界。",
    "version": "1.0.0",
    # 可选：规则没有自带 official_sources 时，使用这里的一手来源。
    "official_sources": ["https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11"],
}


class DemoRenameRule:
    id = "my-pack.rename@94.1"
    boundary = PackFormat(94, 1)
    priority = 450

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        # 分别处理 context.upgrading 与降级，只修改已证明的上下文。
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (DemoRenameRule(),)
# 也可以提供 dpcompat_rules() 函数返回规则元组。
```

要求：

- `PLUGIN` 字典必填：`id`（小写 `[a-z0-9._@-]`）、`name`、`description` 都非空且去首尾空格；
- 必须暴露 `RULES` 或 `dpcompat_rules()`；
- 每条规则的 `id` 稳定唯一，与内置规则不冲突；
- 每条规则要么自带 `official_sources`（HTTP(S) 一手来源），要么由 `PLUGIN["official_sources"]` 统一提供——注册表拒绝无来源规则；
- 保持模块无副作用：插件文件会被加载多次（安装时校验 + 构建时注册），不要在模块顶层执行 I/O 或修改全局状态。

规则协议与安全要求的完整说明见 [`RULE_AUTHORING.zh-CN.md`](RULE_AUTHORING.zh-CN.md) 与 [`SAFETY_MODEL.zh-CN.md`](SAFETY_MODEL.zh-CN.md)。

## 3. JSON 声明式插件

推荐使用带插件元数据的包装形式（可包含多条规则）：

```json
{
  "schema": 1,
  "plugin": {
    "id": "my-json-rules@88",
    "name": "我的声明式规则",
    "description": "把 demo:old 精确替换为 demo:new。",
    "version": "1.0.0"
  },
  "rules": [
    {
      "schema": 1,
      "id": "my-json.rename@88",
      "description": "精确值替换",
      "boundary": [88, 0],
      "compatibility": "lossless",
      "official_sources": [
        "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"
      ],
      "upgrade": [
        {
          "type": "json_exact_value",
          "include": ["data/**/*.json"],
          "old": "demo:old",
          "new": "demo:new"
        }
      ],
      "downgrade": [
        {
          "type": "json_exact_value",
          "include": ["data/**/*.json"],
          "old": "demo:new",
          "new": "demo:old"
        }
      ]
    }
  ]
}
```

也接受单个 `DeclarativeRuleSpec` 作为裸文件（此时插件名取规则 id，描述取规则 description）。

声明式语言只有两种操作：`json_exact_value`（节点与 `old` 完全相等才替换）与 `json_rename_key`（精确 key 改名，目标 key 已存在则报错）。`include` 必须是包内安全 glob；`lossless` 必须同时定义 upgrade 与 downgrade。没有 regex、没有任意代码、不能删除未知字段——需要这些能力的规则请写成 Python 插件。

## 4. 安装与开关（CLI）

```bash
# 安装（校验失败会给出具体原因）
dpcompat plugin install path/to/my_plugin.py
dpcompat plugin install path/to/my_plugin.json --force   # 覆盖已安装的同 id 插件

# 浏览（名称、描述、规则数、来源、启用状态）
dpcompat plugin list
dpcompat plugin list --json

# 启用/禁用（内置与已安装插件都支持）
dpcompat plugin disable gamerules@94.1
dpcompat plugin enable gamerules@94.1

# 卸载（只能卸载文件插件）
dpcompat plugin remove my-pack-rules@94.1
```

插件状态影响 `rules`、`plan`、`build`、`validate` 与 TUI 构建：被禁用的插件不提供任何规则。`dpcompat rules` 只显示当前生效的规则。

## 5. 安装与开关（TUI）

```bash
dpcompat tui
```

- 主界面按 `p`（或点击右上角“插件管理”）进入插件页；
- 每个插件一张卡片：名称、id、描述、内置/文件徽标、规则列表、启用勾选框；
- 点击“安装插件文件...”用文件树选择 `.py`/`.json` 文件，安装后立即出现在列表；
- 点击“创建插件模板...”选择位置（可勾选创建同名子文件夹）生成可直接编辑的模板项目，适合快速开始开发；
- 文件插件卡片上有“卸载”按钮；
- 所有开关即时持久化。

模板项目包含 `插件名.py`（`PLUGIN` 元数据 + 示例规则骨架）与 `README.md`，生成后即可用 `dpcompat plugin install` 或 TUI 安装。

## 6. 开发与测试建议

1. 先写最小 old/new fixture 与失败测试，再实现规则；
2. 正向与反向分别推理：升级通常简单，降级要检查默认值、冲突与信息丢失；
3. 每条规则覆盖：no-op、upgrade、downgrade、冲突、默认值、不可迁移输入、宏、二次执行幂等；
4. 安装前用 `dpcompat plugin install` 校验，再用 `dpcompat rules --json` 确认生效顺序；
5. 完整 `build` 后让 scanner 复核目标残留——规则自报成功不算数；
6. 需要对外发布的插件库，也可通过 `pyproject.toml` 的 `dpcompat.rules` entry point 注册（见 `RULE_AUTHORING.zh-CN.md`），但 entry point 插件不参与插件列表与开关管理。

## 7. 安全底线（为什么这些限制存在）

- **规则必须有一手来源**：没有 Mojang 官方依据的迁移不允许进入任何插件的默认路径；
- **不做全局替换**：同名 `id`/`value`/`equipment` 可能是作者 storage 数据，只能在证明过的上下文转换；
- **不可证明时失败关闭**：返回 `LOSSY`/`UNSUPPORTED`/`UNKNOWN` 而不是“猜一个能加载的写法”；
- **id 冲突即拒绝**：安装时检测，避免两个插件静默竞争同一条规则；
- **插件文件可被多次加载**：请保持模块顶层无副作用，插件也请不要要求“安装后修改内置规则”。

有问题或想贡献插件时，请先阅读 `RULE_AUTHORING.zh-CN.md`、`SAFETY_MODEL.zh-CN.md` 与 `CONTRIBUTING.md`。
