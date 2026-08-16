# Contributing

感谢参与 DPCompat。兼容工具的错误可能静默改变作者逻辑，因此“能解析”或“服务端能加载”都不足以宣称无损。

## 开发流程

1. issue/PR 写明来源格式、目标格式、最小复现和 Mojang 正式资料；
2. 先补失败测试，再实现最窄的结构化规则；
3. 运行 `make format && make check && make smoke`（Windows 无 GNU Make 时运行 `powershell -ExecutionPolicy Bypass -File scripts/build.ps1 check`）；
4. PR 说明无损前提、冲突、有损/未知输入、降级边界和真实 server/GameTest 结果；
5. 不提交 `dist/`、日志、虚拟环境、server JAR 或世界存档。

## 规则要求

- 规则按 `PackFormat` 边界触发，不比较游戏版本字符串；
- 每条规则有稳定唯一 ID 与至少一个一手来源；
- 不接受无上下文全局替换，也不删除未知字段来换取加载；
- 正向与反向分别推理，lossless 必须覆盖双向测试；
- 目标字段已存在时报告冲突，不能静默覆盖；
- 不可证明时返回 `LOSSY`、`UNSUPPORTED` 或 `UNKNOWN`；
- 新版本和 feature minimum 必须有 Mojang 正式版来源；
- 快照研究不能扩大正式版支持承诺。

详见 `docs/RULE_AUTHORING.zh-CN.md` 和 `docs/SAFETY_MODEL.zh-CN.md`。

## Commit

使用 Conventional Commits。一个提交只做一个可测试目的；正文解释安全边界、证据和验证命令。完整的开发规范见 `docs/agent/`。
