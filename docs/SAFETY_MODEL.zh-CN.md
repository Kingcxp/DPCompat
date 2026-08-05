# “不能安全自动迁移”究竟是什么意思

“不能安全自动迁移”不是一句模糊的免责声明，而是一个可以在代码、报告和 CI 中检查的工程判定：**现有信息不足以证明目标输出与来源数据包在目标游戏版本中具有作者期望的等价行为。**

## 1. 五种兼容性结论

### 1.1 `lossless`

转换前后语义可以建立一一对应关系，且反向转换能够恢复原信息。典型例子：

- 精确资源 ID `minecraft:chain` 与 `minecraft:iron_chain` 的版本重命名；
- 旧 `filtered.modifier` 到新 `filtered.on_pass`，前提是没有 `on_fail`；
- timeline 没有自定义 clock 时补上 `minecraft:overworld`；
- 新出生点 pitch 为字面量 `0` 时删除该参数以降级。

### 1.2 `emulated`

旧版没有同一原生结构，但可以生成辅助函数、scoreboard、storage 或使用作者提供的 fallback，使对外行为保持一致。项目目前把目标 fallback 记为 `emulated`。

### 1.3 `lossy`

输出可以加载，但已知至少一部分信息或行为会丢失。例如：

- 26.1 配方设置 `show_notification=false`，目标版本对该配方类型没有对应字段；
- 新出生点命令指定非零 pitch，而旧版只能保存一个角度；
- Pig/Strider 的新 `equipment.saddle` 是完整物品栈，旧 `Saddle` 只有布尔状态，鞍上的组件会丢失；
- tooltip 隐藏的是一个旧格式无法附加 `show_in_tooltip` 的标量组件。

默认策略拒绝 `lossy`。即使用户显式 `--allow-lossy`，报告仍会保留诊断，方便代码审查。

### 1.4 `unsupported`

目标版本没有等价机制，且项目没有可信模拟器。例如：

- 26.1 的 `villager_trade`/`trade_set` 注册表降级到 1.21.11；
- 1.21.6 的 `dialog` 数据驱动 UI 降级到 1.21.5；
- 1.21.11 `filtered.on_fail` 降级到只支持成功分支的版本；
- 26.1 自定义 world clock 和 time marker 降级；
- 26.2 `sulfur_cube_archetype` 降级；
- 新方块、实体或客户端渲染能力在旧版根本不存在。

`unsupported` 永远不会被策略放行；必须提供明确的目标 fallback 或不发布该目标。

### 1.5 `unknown`

不是已经证明“不能”，而是当前解析器或规则无法证明“能”。例如：

- mcfunction 宏在运行时拼出 key、列表、compound、完整文本组件或实体 NBT；
- 命令参数中出现项目尚未实现语法树的复杂物品组件；
- 未登记资源目录；
- JSON 中某个字段名与 Mojang 字段重名，但上下文类型不明确；
- 第三方预处理器在构建后才生成真实命令。

默认拒绝 `unknown`，因为把“不知道”当作“成功”是兼容工具最危险的行为。

## 2. 为什么不能只做字符串替换

### 2.1 同名不等于同语义

`tag`、`id`、`value`、`equipment` 等名称可以同时出现在：

- Mojang 资源 schema；
- storage 中的作者自定义数据；
- 宏参数；
- 文本组件；
- 实体 NBT；
- 方块实体 NBT；
- 物品组件；
- JSON 字符串或翻译文本。

全局替换会修改本不属于迁移对象的内容。DPCompat 只在已确认的资源类型或命令参数位置执行结构化转换。

### 2.2 字段改变常常不是重命名

1.21.5 将多个实体字段合并到 `equipment`：旧的四项 `ArmorItems`、两项 `HandItems`、body armor 和 saddle 并不是一个简单的 `old_key -> new_key`。降级时还要重新构造固定长度列表、默认空物品和掉落概率。

文本组件也不是只把 `hoverEvent` 改成 `hover_event`：不同 action 的 `contents` 会被内联，`show_entity.id/type` 还分别变成 `uuid/id`，click action 的 `value` 会根据 action 变成 `url`、`command` 或 `page`。

### 2.3 新结构可能表达更多信息

新格式到旧格式通常是“多对一”：

- yaw + pitch → 单一角度；
- 物品栈 saddle → 布尔 saddle；
- `on_pass` + `on_fail` → 只有一个 modifier；
- 多 world clock → 固定 overworld 时间；
- Environment Attribute modifiers/timeline/biome 叠加 → 旧版固定字段。

除非额外信息等于旧版默认值，否则不可逆。

## 3. 世界生成为什么特别保守

1.21.11 把许多 dimension/biome 字段迁移到 Environment Attributes，并明确指出新属性的形式“可能不与原字段相同”。例如一个旧 `ultrawarm` 同时影响 water evaporation、fast lava 和 dripstone particle；旧 `natural` 又拆分成多个玩法属性。反向合并时，不同属性可以被 biome、timeline 和 modifier 独立覆盖，无法唯一推回一个布尔值。

因此 DPCompat 当前做法是：

1. 检测 `attributes`、`timelines`、`skybox`、`cardinal_light`、`has_fixed_time`；
2. 向 94.1 以前降级时标为 `unsupported`；
3. 要求作者提供目标 dimension/biome fallback；
4. fallback 仍需静态扫描和真实服务端测试。

未来可以加入“受限子集”规则，例如只有 override、没有 timeline、所有拆分属性严格组成某个旧字段时才允许无损降级，但不能把一般情况误判为可逆。

## 4. 宏和动态数据

`$()` 宏的实际文本只能在运行时由调用参数决定，但并非所有宏都有同样风险。构建器看到：

```mcfunction
$tellraw @s $(message)
```

无法知道 `message` 最终是旧 JSON、现代 SNBT、普通字符串还是恶意/非法片段，因此必须阻断。另一种形状：

```mcfunction
$tellraw @s {"text":"$(message)"}
```

占位符位于已解析的引号标量内，只能改变该字符串的值，不能生成新的 key/list/compound；DPCompat 可以迁移外层组件并原样保留占位符。判定是语法位置而不是“包含 `$(` 就放行”。对于结构生成宏，安全选择是：

- 迁移调用点和宏契约（未来功能）；或
- 要求作者提供目标 fallback；或
- 报告 `unknown` 并停止目标构建。

静态工具不应“猜一个最常见格式”。

## 5. 新内容与语法迁移是两回事

即使语法可以改写，目标版本也可能没有内容 ID。例如把一个 26.2 新实体的 `summon` 命令转换成旧语法仍然没有意义，因为旧版注册表不存在该实体。兼容器必须同时验证：

- 语法结构；
- 资源/注册表 ID；
- 数据字段；
- 行为语义；
- 客户端可见效果。

当前项目内置的是一份保守的特征清单，不是完整的每版本 registry dump。真实发布建议增加官方 server reports 验证。

## 6. 策略门禁如何工作

每条迁移规则返回 `MigrationRecord`，每条问题返回 `Diagnostic`。构建器在写 ZIP 前执行：

1. 规则诊断；
2. 目标资源扫描；
3. `BuildPolicy` 判定；
4. 任一 ERROR 时删除临时目标目录，不写部分 ZIP；
5. 成功后才原子替换最终 ZIP。

默认：

```text
lossless     允许
emulated     允许，但记录
lossy        拒绝
unsupported  永远拒绝
unknown      拒绝
```

## 7. 代码审查问题清单

审查每条新规则时必须回答：

1. 规则只在正确版本边界触发吗？
2. 它如何确认资源/命令上下文，而不是凭字段名猜测？
3. 正向与反向是否分别实现？
4. 哪些输入使转换不可逆？
5. 默认值依据是什么？
6. 宏、未知字段和重复字段如何处理？
7. 改动是否保留来源文件和行号诊断？
8. 是否有正向、反向、无变化、冲突和有损测试？
9. 是否在目标 scanner 中检查迁移残留？
10. 是否有真实服务端或 GameTest 证据？
