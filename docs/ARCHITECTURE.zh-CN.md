# 架构说明

## 数据流

```text
materialize_source → detect_pack → flatten_pack
                         ↓
             create_rule_registry
       built-ins + modules + entry points + JSON files
                         ↓
        每个目标 build_target：规则 → fallback → 重扫 → 策略 → ZIP
                         ↓
               report + universal overlays
```

输入包自身若含 overlay，会先按识别出的来源 format 展平为游戏实际可见视图。每个目标都从同一份有效来源复制，按 registry 的确定顺序执行规则；目标之间不串联，避免先迁移到中间版本造成累积损伤。

## 模块边界

| 模块 | 单一职责 |
| --- | --- |
| `models.py` | Pydantic 核心值对象、诊断、策略和构建结果 |
| `versions.py` / `manifests.py` | 严格加载带来源的 release/feature 数据 |
| `packio.py` / `metadata.py` | 安全输入、bundle 数据包根选择、overlay 展平、元数据、原子确定性 ZIP |
| `commands.py`、`snbt.py`、`nbt.py` | 内部语法树；它们可保留 dataclass，因为不接收外部 schema |
| `scanner.py` / `detector.py` | 只读内容证据、来源识别与目标残留检查 |
| `migrations/` | 内置方向化语义规则；不负责发现或 I/O 编排 |
| `rules/schema.py` | Pydantic 声明式规则与 registry 元数据契约 |
| `rules/declarative.py` | 执行受限 JSON 操作并拒绝 key 冲突 |
| `rules/registry.py` | 组合内置、模块、entry point、规则文件并排序去重 |
| `fallback.py` | 严格 Pydantic fallback 模型、精确删除/替换/诊断解决 |
| `engine.py` | 目标编排与通用包，不包含具体 Mojang 字段知识 |
| `report.py` | 将模型转换为稳定 JSON 报告 |
| `logging_config.py` | QueueListener、Rich console、旋转总日志与模块日志 |
| `cli.py` | argparse/Rich 适配；JSON 模式保持机器可读 stdout |
| `servercheck.py` | 用户提供的原版 server 外部进程边界 |

## 模型策略

外部或跨模块数据采用 Pydantic：配置、版本档案、feature manifest、声明式规则、诊断、检测/构建结果、fallback 与 server-check 结果都拒绝额外字段并验证范围。命令 token、SNBT/NBT 节点和单次规则运行 context 是解析器内部对象，使用 dataclass 可减少开销且不会削弱外部输入校验。

## 规则顺序

registry 按 `(priority, id)` 排序。内置规则从 100 开始；声明式规则默认 500。重复 ID、非法协议、无一手来源会在编译前失败。规则不能依赖“恰好的类导入顺序”；若存在语义顺序依赖，必须固定 priority、在文档说明并写集成测试。

## 不变量

1. 不修改用户输入；所有迁移在临时副本执行。
2. 每个目标从同一来源独立构建。
3. 规则崩溃转为 error，失败目标不写 ZIP。
4. fallback 后重新扫描，不能用 replacement 隐藏新残留。
5. 未识别内容不归类为 lossless。
6. ZIP 拒绝路径穿越/特殊条目，目录和 fallback 拒绝 symlink。
7. ZIP 原子替换且内容、时间戳、权限确定。
8. 通用包的 overlay 使用完整 `data/`，基础层 guard 防止未来未知格式静默空载。
9. 报告记录来源证据、有效规则来源、规则结果、策略、诊断、哈希。
10. 来源层与展平层产生的完全相同诊断只记录一次，不隐藏有不同内容或位置的发现。

## 扩展原则

简单、精确、可逆的 JSON 字段变化优先用声明式规则；命令、SNBT、NBT、跨文件引用或条件语义必须使用 Python 规则。完整开发契约见 `RULE_AUTHORING.zh-CN.md`。
