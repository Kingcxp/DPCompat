# 迁移规则（AI 版）

这是本仓库最核心、最容易改错的部分。规则是**方向化的、有来源的、失败关闭的结构化转换**。

## 协议

```python
class SomeRule:
    id = "example.change@94.1"          # 全局唯一，小写 [a-z0-9._@-]
    boundary = PackFormat(94, 1)        # 语义首次进入支持正式版的 pack format
    priority = 450                       # registry 排序；100+ 内置，500 声明式

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        # context: root(临时目标树), source, target, policy
        # 返回 RuleResult(MigrationRecord(self.id, Compatibility.X, changed_files, changed_nodes), diagnostics)
        ...
```

- `crosses(source, target, boundary)` = 转换确实跨过该边界（任意方向）。
- 单次 `apply` 收到的是**临时副本目录**；改文件直接写回 `context.root`。
- `MigrationContext.relative(path)` 给出包根相对路径用于诊断。

## 边界与语义事实（已核验）

| 边界 | 官方变更（一手来源） | 本仓库规则 |
| --- | --- | --- |
| 61 → 71（1.21.5） | 文本组件 clickEvent/hoverEvent → snake_case + action 专用字段；show_text `contents`→`value`；show_item/show_entity contents 内联（id→uuid、type→id）；实体 ArmorItems/HandItems/body_armor_item → `equipment`（slots: head/chest/legs/feet/mainhand/offhand/body/saddle），SaddleItem/布尔 Saddle → equipment.saddle；ArmorDropChances/HandDropChances/body_armor_drop_chance → drop_chances（默认 0.085f）；FallDistance→fall_distance；SleepingX/Y/Z→sleeping_pos；TileX/Y/Z→block_pos（item_frame/glow_item_frame/painting/leash_knot）；phantom Size→size、AX/AY/AZ→anchor_pos；player Spawn*/SpawnAngle/SpawnDimension/SpawnForced→respawn、enteredNetherPosition→entered_nether_pos；tooltip_display={hide_tooltip,hidden_components} 取代 hide_tooltip/hide_additional_tooltip/show_in_tooltip；attribute_modifiers→modifiers、dyed_color→rgb、can_place_on/can_break→predicates、enchantments/stored_enchantments→levels 内联；item 命令槽位 horse.saddle→saddle | `text.py`、`items.py`、`entities.py`、`structures.py`、`commands.py`(HorseSaddleSlotRule)、`entity_data.py`、`text_components.py` |
| 71 → 80（1.21.6） | 所有 JSON 严格模式解析 | `strict_json.py`（归一化 + 重复 key 拒绝） |
| 80/81 → 88（1.21.9） | pack.mcmeta 用 `min_format`/`max_format`（`[major, minor]`）取代 pack_format/supported_formats（<82 仍需要旧字段）；`chain`→`iron_chain` 硬改名；spawnpoint/setworldspawn 新增可选 pitch（yaw=angle 早已存在） | `identifiers.py`、`commands.py`(SpawnRotationRule)、`metadata.py`、`models.PackFormat` |
| 88 → 94.1（1.21.11） | 全部 gamerule 改 namespaced snake_case，含特殊改名表与 3 条反义规则（disableElytraMovementCheck→elytra_movement_check、disablePlayerMovementCheck→player_movement_check、disableRaids→raids）；doFireTick/allowFireTicksAwayFromPlayer 移除 → fire_spread_radius_around_player；worldborder set/add/warning time 时间参数秒→tick（s/d 后缀）；filtered.modifier→on_pass（新增 on_fail）；Environment Attributes（dimension/biome 的 attributes、timelines、skybox、cardinal_light、has_fixed_time） | `gamerules.py`（含完整特殊改名表）、`worldborder.py`、`resources.py`(FilteredLootRule)、`scanner.py`（环境属性阻断） |
| 94.1 → 101.1（26.1） | world clock 注册表（data/ns/world_clock/）；/time [of clock]；timeline 文件 + clock 字段；time_check + clock；test_environment time_of_day→clock_time{clock,time}；dimension_type + default_clock/has_ender_dragon_fight；配方：result 短形式字符串、烹饪类 result 支持 count、stonecutting/smithing 移除 group、show_notification 扩展到多类型、crafting_dye/crafting_imbue 新增、crafting_special_mapcloning 移除（并入 crafting_transmute）、transmute + material_count/add_material_count_to_result | `resources.py`(TimelineClockRule/TestEnvironmentClockRule)、`recipes.py`(Recipe26Rule/TimeCheckClockRule)、`scanner.py`（default_clock 阻断） |
| 101.1 → 107.1（26.2） | sulfur_cube_archetype 注册表；实体谓词改组件映射风格；HurtByTimestamp 移除 | `scanner.py`（resource-too-new 阻断）、`features.json` |

上表是 2026-08 复核结果（对照 minecraft.wiki 与 Mojang 正式版说明）。**新增规则前必须重新查证**，流程见下文。

## 安全模型（为什么某些迁移必须失败）

- **lossless**：双向可恢复，例如精确 id 改名、无 on_fail 的 modifier→on_pass、pitch=0 的降级。
- **emulated**：用 fallback/辅助机制保持对外行为（项目 fallback 记为 emulated）。
- **lossy**：能加载但丢信息（非零 pitch 降级、鞍组件丢失、show_notification=false 降级）——默认拒绝。
- **unsupported**：目标版本无等价机制（villager_trade 降级、on_fail 降级、自定义 clock 降级、sulfur_cube_archetype 降级）——永远拒绝。
- **unknown**：解析器无法证明（宏生成结构、未知资源目录、重名但上下文不明的字段）——默认拒绝。

判定是**语法位置**而非"包含某关键字"：`$(message)` 在引号标量内可保留并迁移外层结构；未引号的 `$(component)` 可能生成任何结构，必须阻断。

## 新增/修改规则的流程

1. **查证官方事实**：读 Mojang 正式版说明（https://www.minecraft.net/en-us/article/minecraft-java-edition-<版本>）与 minecraft.wiki 对应版本页。确定：精确的旧/新语法、触发条件、默认值、反义/内联/移除关系、边界 pack format。
2. **写失败测试**：在 `tests/test_migrations.py`（规则级，直接 `rule.apply(MigrationContext(...))`）或 `tests/test_command_boundaries.py`（命令边界）写最小 fixture。
3. **实现**：在 `migrations/` 新建模块（或扩展现有同边界模块）；只修改已解析上下文；upgrade 与 downgrade 分别推理。
4. **登记来源**：`migrations/sources.py` 的 `BUILTIN_RULE_SOURCES` 添加 `rule_id → (官方URL,)`；registry 缺来源会失败。
5. **接入目录**：`migrations/__init__.py` 的 `BUILTIN_RULES` 按依赖顺序插入；**同时更新 `plugins.py` 的内置插件目录**（`_BUILTIN_PLUGIN_DEFS` + `_BUILTIN_PLUGIN_L10N`），`_builtin_plugins()` 会校验目录与 `BUILTIN_RULES` 完全一致，不一致直接抛错。
6. **测试矩阵**：no-op（边界内）、upgrade、downgrade、冲突、默认值、非默认有损/不支持、宏/未知语法、二次执行幂等、无效输入、完整 build 后无残留。
7. **门禁**：`make check` + `make smoke` + 样例 `plan/build`；必要时更新 `docs/OFFICIAL_CHANGE_AUDIT.zh-CN.md`、`docs/VERSION_MATRIX.md`、`docs/SOURCES.md`、`CHANGELOG.md`。

## 规则测试约定

- 规则级测试用 `make_pack(root, pack_format)`（`tests/helpers.py`）建包 + `write()` 写文件，直接构造 `MigrationContext(root, PackFormat(61), PackFormat(71), BuildPolicy())` 调 `rule.apply`，断言文件内容与诊断 code/severity。
- 端到端行为在 `tests/test_build.py`（engine 级）与 `tests/test_research_fixture.py`（examples/research_fixture 回归）。
- 策略联动：`policy_diagnostic` 在 `allow_*` 关闭时是 ERROR、打开时是 WARNING——测试两种策略。

## 已知缺口（2026-08 复核，未修，勿静默扩 scope）

- `show_item` hover 事件旧格式的 `text` 字段（1.21.5 改名 value）未处理——罕见，失败方式为遗留字段。
- 26.2 实体谓词新格式（组件映射风格）未检测，降级时可能静默残留——属"保守特征清单"边界。
- 烹饪配方 result 含 id/count 以外字段 → 已加 `cooking-result-fields-cannot-downgrade`（UNKNOWN）。
- 旧版 `hide_additional_tooltip` 组件 → 已加 `hide-additional-tooltip-cannot-upgrade`（UNKNOWN）。
- 新增规则前复查这些是否已成为常见输入。
