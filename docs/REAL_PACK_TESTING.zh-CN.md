# 真实数据包移植验证流程

仓库单测证明“给定输入形状时转换器按设计运行”；原版 server 证明“输出可加载”；作者定义的行为测试才证明“玩法仍符合预期”。三者不能互相替代。

## 1. 准备基线

1. 保留原始 ZIP，只在副本上工作；记录 SHA-256。
2. 用源版本原版 server 加载原包，保存 `latest.log`、`/datapack list`、关键 scoreboard/storage/实体/战利品结果。
3. 列出作者关心的可观察行为，例如 load/tick 初始化、触发器、物品组件、实体装备、结构生成、配方和 loot。
4. 若数据包已有 overlay，记录源版本实际加载哪些层。

## 2. 识别与规划

```bash
uv run dpcompat inspect RealPack.zip --json > inspect.json
uv run dpcompat plan RealPack.zip --target 1.21.4 --target 1.21.11 --json > plan.json
```

如果 ZIP 是同时包含数据包和资源包的 bundle，单一 `data/` 根会被自动选择；多个数据包并存时使用 `--pack-root relative/datapack`，并把同一值写入 `[build].pack_root` 以保证 CI 可复现。该选项不能绕过 ZIP 路径穿越检查。

若 metadata 与内容证据冲突，不要直接强制覆盖。人工确认真正的源语法后才使用 `--source-format`。逐条审查 report 中的 rule origin、compatibility、path 和 source evidence；禁止以 `--allow-unknown` 作为批量消警工具。

## 3. 处理不可自动迁移内容

对每个失败目标选择：缩小发布范围、补一条有来源且可复用的规则，或提供目标 fallback。fallback 必须给出精确文件/删除和 `code`、`path`、`reason`；替代实现也要单测和 server 测试。

## 4. 构建与静态复核

```bash
uv run dpcompat build RealPack.zip --config dpcompat.toml --output dist
python -m zipfile -t dist/my-pack-1.21.11.zip
```

检查失败目标没有 ZIP、报告哈希与文件一致、连续两次构建字节一致、通用包每个唯一 format 都有目标层。抽样 diff 解压后的有效来源与目标，重点检查命令、NBT、loot/recipe 和删除项。

## 5. 匹配原版服务端

为 61、71、80、81、88.0、94.1、101.1、107.1 各准备匹配的合法 server JAR；26.1+ 使用 Java 25。逐个运行：

```bash
uv run dpcompat server-check dist/my-pack-1.21.11.zip \
  --server-jar /local/server-1.21.11.jar \
  --java /local/java-21/bin/java \
  --accept-eula --keep evidence/server-1.21.11
```

保留命令、JAR 版本、Java 版本和日志。server-check 的错误 marker 不是完整日志解析器，仍需人工搜索 function/tag/recipe/loot/predicate/registry 解析错误。

## 6. 行为与回归

把第 1 步的行为清单写成 GameTest 或确定的命令断言，并在每个目标运行。比较输出时要允许版本本身的预期差异，但不能把未解释差异记为成功。特别验证定时行为：1.21.11 worldborder 的 tick 语义在暂停、低 TPS 和正常 TPS 下与旧 real-time 行为可能不同。

## 7. 证据记录模板

| 字段 | 内容 |
| --- | --- |
| Source archive SHA-256 |  |
| Source Minecraft / format |  |
| Target Minecraft / format / Java |  |
| DPCompat version / config commit |  |
| Build report SHA-256 |  |
| Vanilla server load | pass/fail + log path |
| GameTest/behavior result | pass/fail + report path |
| Approved lossy/emulated items | code/path/reviewer/reason |

没有真实包、匹配 server JAR 或行为断言时，只能报告“代码级夹具通过”，不能报告“真实移植成功”。

数据包和资源包必须分别验证。DPCompat 当前不会改写 `assets/`、资源包 `pack_format`、模型、字体、shader 或声音；一个 bundle 的数据包成功不代表其配套资源包在目标客户端可加载。
