# 发布检查清单

## 代码、模型和来源

- [ ] release/feature 只包含已核验正式版并附 Mojang URL；
- [ ] 每个有效规则有唯一 ID、边界、priority 和至少一个一手来源；
- [ ] 双向规则覆盖 upgrade/downgrade/no-op/conflict/default/non-default；
- [ ] Pydantic schema 拒绝 extra key、重复和非法路径；
- [ ] 未实现语义为 lossy/unsupported/unknown，不扩大 snapshot 承诺；
- [ ] README、CHANGELOG、`__version__`、pyproject 和 tag 一致。

## 可复现门禁

```bash
uv lock --check
uv sync --locked --all-groups
make check
make smoke
make build
make clean
uv run dpcompat build examples/simple_pack \
  --target 1.21.4 --target 1.21.9 --target 26.2 --output dist
python -m zipfile -t dist/datapack-universal-1.21.4-plus.zip
```

- [ ] 连续构建两次 ZIP 字节与 SHA-256 相同；
- [ ] 失败目标无 ZIP；报告含 evidence、policy、effective rule registry、diagnostics 和 hashes；
- [ ] 通用包基础 guard 与每个唯一 format overlay 正确；
- [ ] fallback 无 unused resolution；
- [ ] sdist/wheel 在干净临时环境安装并运行 `dpcompat version`。

## 原版服务端与真实包

- [ ] format 61、71、80、81、88.0、94.1、101.1、107.1 分别用匹配正式版 server JAR 加载；
- [ ] 26.1+ 使用 Java 25；
- [ ] 保存 server 版本、Java、命令、日志和报告哈希；
- [ ] 真实数据包的作者行为/GameTest 在每个发布目标通过；
- [ ] 不把仓库 synthetic fixture 或“能加载”描述为真实玩法等价。

## 发布内容

- [ ] 不含 `.venv`、cache、日志、server JAR、世界、秘密、个人绝对路径；
- [ ] `git diff --check` 与 CI 通过；
- [ ] 发布说明列出新增规则、配置/API 变化、不支持项和真实验证范围；
- [ ] source archive 能执行 locked sync 和完整检查。
