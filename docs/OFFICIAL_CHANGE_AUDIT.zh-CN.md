# 官方变更审计与实现决策

本文记录 0.3 规则使用的一手事实、代码决策与保守边界。链接指向 Mojang 正式版说明；没有正式来源的规则不能进入默认 registry。

## 1.21.4 / format 61

1.21.4 是当前支持区间的基线。DPCompat 不尝试从更旧版本推断变化，也不会把基线之前的内容误标为已支持。来源：[1.21.4 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-4)。

## 1.21.5 / format 71

官方说明记录了命令和实体数据中的 JSON 字符串文本组件改为直接内联、文本组件事件由 camelCase 与通用 `value`/`contents` 迁移到 snake_case 与 action-specific 字段、tooltip 统一到 `tooltip_display`、实体装备结构变化，以及 `horse.saddle` 槽位改名。实现使用 JSON/SNBT/NBT 结构，不做文件级字符串替换；实体上下文会处理 `CustomName`、`text_display.text` 及递归 `Passengers`。宏只在每个占位符都位于已解析的引号标量中时保留并迁移静态外层结构；可以生成结构的未引号宏仍以 `unknown` 阻断。来源：[1.21.5 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5)。

## 1.21.6 / format 80

数据包 JSON 进入严格解析。实现对 80+ 目标重新序列化 JSON，并在所有输入阶段拒绝重复 key；这不会自动 backport 同版新增的 dialog、waypoint 或 custom click event。来源：[1.21.6 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-6)。

## 1.21.7–1.21.8 / format 81

两个正式版共用 81。项目分别登记游戏版本，但 universal overlay 对同一唯一 format 只生成一层，避免重复内容。来源：[1.21.7](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-7)、[1.21.8](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-8)。

## 1.21.9–1.21.10 / format 88.0

官方引入 major/minor pack format 元数据，spawnpoint/setworldspawn 新增 yaw 与 pitch，并把 chain 标识符改为 iron_chain。实现：

- `PackFormat(major, minor)` 永不经 float 比较；
- 元数据按新旧目标生成正确表示；
- spawn 降级只在可证明默认 pitch 时无损；
- chain 只改 JSON 精确 scalar 和完整命令 atom，绝不改 JSON object key、自定义 storage key 或普通文本。

1.21.10 共用 88.0。来源：[1.21.9](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9)、[1.21.10](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-10)。

## 1.21.11 / format 94.1

官方把 gamerule 迁移到 namespaced snake_case，包含特殊重命名、含义反转和 fire rule 替换；worldborder 时间参数从秒改为 tick，插值也按 game tick 推进；filtered loot function 使用 `on_pass`，并新增 `on_fail`。实现：

- 字面量 gamerule 可双向转换；反义名称同时反转显式布尔值；query、宏和无法一一对应的 fire rules 阻断；
- worldborder 数字可换算单位，但每个带时间命令仍为 `unknown`，因为真实时间与 game tick 语义不等价；
- `modifier`/`on_pass` 可逆，向旧版遇到 `on_fail` 为 unsupported；
- Environment Attributes 一般降级只检测和阻断。

来源：[1.21.11 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-11)。

## 26.1 / format 101.1

官方要求 Java 25，并引入 world clocks、time-check clock 以及配方结构变化。实现只为缺省 overworld clock 和少量可证明默认的 recipe 形式转换；自定义 clocks、time markers、新配方类型、trade registry 等要求 fallback。来源：[26.1 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1)。

## 26.2 / format 107.1

官方新增 sulfur-cube archetype。旧版本没有该 registry 的等价物，因此 scanner 在降级时阻断，而不是删除资源。来源：[26.2 正式版说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-2)。

## 证据与测试结论

`examples/research_fixture` 是按上述官方形状构造的仓库夹具，用于复现和回归；它不是任何真实作者数据包，也不能替代原版 server/GameTest。任何新增自动规则都必须同时更新 `sources.py`、正反向测试、冲突测试、版本矩阵和本审计。
