"""Installable rule plugins with persistent enable/disable state.

A plugin bundles one or more migration rules with browsable metadata (name and
description).  Built-in plugins are the default rule groups; users can install
additional Python or declarative JSON plugin files from the CLI or the TUI, and
toggle any plugin on or off.  Disabled plugins contribute no rules to builds.

The store directory is resolved from ``DPCOMPAT_PLUGIN_DIR`` when set, otherwise
``~/.dpcompat/plugins``.  Enable state lives in ``plugins.toml`` inside that
directory; only disabled plugins are listed there, so a missing entry means
``enabled = true``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, HttpUrl, field_validator

from .config import ProjectConfig
from .migrations import BUILTIN_RULES
from .models import FrozenModel
from .rules import DeclarativeMigrationRule, RuleRegistry, create_rule_registry
from .rules.schema import DeclarativeRuleSpec

PLUGIN_DIR_ENV = "DPCOMPAT_PLUGIN_DIR"
STATE_FILE_NAME = "plugins.toml"

_RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]*$")
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_TEMPLATE_SOURCE = '''"""{name}: DPCompat 插件模板.

安装: dpcompat plugin install {name}.py
在 TUI 插件管理页也可以直接安装本文件。
完整插件开发说明见 docs/PLUGIN_DEVELOPMENT.zh-CN.md。
"""

from dpcompat.migrations.base import MigrationContext, RuleResult, crosses
from dpcompat.models import Compatibility, MigrationRecord, PackFormat

PLUGIN = {{
    "id": "{name}@88",
    "name": "{name}",
    "description": "在这里描述这个插件负责什么迁移。",
    "version": "0.1.0",
    "official_sources": [
        "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9"
    ],
}}


class ExampleRule:
    id = "{name}.example@88"
    boundary = PackFormat(88)
    priority = 450

    def applies(self, source: PackFormat, target: PackFormat) -> bool:
        return crosses(source, target, self.boundary)

    def apply(self, context: MigrationContext) -> RuleResult:
        # 在这里实现迁移：先确认资源/命令上下文，再分别处理 upgrade 与降级。
        # 不可证明等价时返回 LOSSY / UNSUPPORTED / UNKNOWN，而不是猜测。
        return RuleResult(MigrationRecord(self.id, Compatibility.LOSSLESS, 0))


RULES = (ExampleRule(),)
'''

_TEMPLATE_README = """# {name} 插件模板

- `{name}.py` 是插件本体：`PLUGIN` 元数据 + `RULES` 规则元组。
- 安装：`dpcompat plugin install {name}.py`（或 TUI 插件管理页 -> 安装插件文件）。
- 安装后可用 `dpcompat plugin list` 查看，`dpcompat plugin disable/enable {name}@88` 开关。
- 开发前请阅读 `docs/PLUGIN_DEVELOPMENT.zh-CN.md` 与 `docs/RULE_AUTHORING.zh-CN.md`。
- 规则 id 全局唯一；规则必须有一手来源；不可证明等价时失败关闭。
"""


class PluginMeta(FrozenModel):
    """Author-declared metadata at the top of a plugin file."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._@-]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(default="1.0.0", pattern=r"^[0-9][0-9A-Za-z.+-]*$")
    official_sources: tuple[HttpUrl, ...] = ()

    @field_validator("name", "description")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Plugin name and description must be trimmed")
        return value


class JsonPluginFile(FrozenModel):
    """Wrapper schema for a JSON plugin file containing one or more rules."""

    schema_version: Literal[1] = Field(alias="schema")
    plugin: PluginMeta
    rules: tuple[DeclarativeRuleSpec, ...] = Field(min_length=1)


class PluginInfo(FrozenModel):
    """Browsable plugin record shown by ``dpcompat plugin list`` and the TUI."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._@-]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]*$")
    origin: Literal["builtin", "file"]
    kind: Literal["builtin", "python", "declarative"]
    enabled: bool
    rules: tuple[str, ...] = ()
    path: str | None = None

    @classmethod
    def from_meta(
        cls,
        meta: PluginMeta,
        *,
        origin: Literal["builtin", "file"],
        kind: Literal["builtin", "python", "declarative"],
        rules: tuple[str, ...],
        path: str | None = None,
    ) -> Self:
        return cls(
            id=meta.id,
            name=meta.name,
            description=meta.description,
            version=meta.version,
            origin=origin,
            kind=kind,
            enabled=True,
            rules=rules,
            path=path,
        )


_BUILTIN_PLUGIN_DEFS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "text-components@71",
        "文本组件事件迁移",
        "将 1.21.5 之前的 clickEvent/hoverEvent 事件字段迁移为 snake_case 与 action 专用字段，"
        "覆盖已知 JSON 文本位置与命令内的 SNBT 组件。",
        ("text-component.events-and-inline-snbt@71",),
    ),
    (
        "item-components@71",
        "物品 tooltip 组件迁移",
        "把各物品组件局部的 show_in_tooltip 合并进 minecraft:tooltip_display，并转换官方明确简化的组件形态。",
        ("item-components.tooltip-display-and-simplification@71",),
    ),
    (
        "entity-nbt@71",
        "实体 NBT 字段迁移",
        "迁移 summon 与 data merge entity 中的实体 NBT：装备槽位合并、fall_distance、"
        "睡眠坐标、item_frame/phantom/player 字段，以及 CustomName 等内嵌文本组件。",
        ("entity-nbt-equipment-and-fields@71",),
    ),
    (
        "structure-nbt@71",
        "结构文件实体迁移",
        "改写 data/<命名空间>/structure/*.nbt 中 entities[].nbt 的 1.21.5 字段，保持二进制 NBT 类型不丢失。",
        ("structure.entity-nbt@71",),
    ),
    (
        "command-slots@71",
        "马鞍槽位改名",
        "把 item 命令中的 horse.saddle 槽位改名为 saddle；不改写 data 命令中的 NBT 路径。",
        ("command.slot-horse-saddle-to-saddle@71",),
    ),
    (
        "strict-json@80",
        "严格 JSON 归一化",
        "面向 1.21.6 及以上目标时把所有 JSON 资源重写为标准严格 JSON；重复键始终拒绝。",
        ("json.strict-normalization@80",),
    ),
    (
        "identifiers@88",
        "标识符重命名",
        "把精确的 minecraft:chain 标识符改为 minecraft:iron_chain，"
        "仅处理 JSON 标量与完整命令 token，不改写对象 key 或子串。",
        ("identifier.chain-to-iron-chain@88",),
    ),
    (
        "spawn-rotation@88",
        "出生点旋转语法",
        "为 spawnpoint/setworldspawn 补充 yaw/pitch；降级时仅在 pitch 为零时无损。",
        ("command.spawn-rotation-pitch@88",),
    ),
    (
        "gamerules@94.1",
        "gamerule 注册表改名",
        "把 1.21.11 的 gamerule 迁移为 minecraft:snake_case，处理特殊改名与反义规则"
        "显式赋值的反转；被移除的火规则双向阻断。",
        ("command.gamerule-registry-names@94.1",),
    ),
    (
        "worldborder@94.1",
        "worldborder 时间单位",
        "换算秒/天/刻的时间单位，但真实时间与游戏刻的推进语义不等价，带时间命令默认按 unknown 阻断。",
        ("command.worldborder-tick-time@94.1",),
    ),
    (
        "loot@94.1",
        "filtered 战利品分支",
        "把 filtered 战利品函数的 modifier 改为 on_pass；存在 on_fail 时禁止降级。",
        ("loot.filtered-on-pass-on-fail@94.1",),
    ),
    (
        "clocks@101.1",
        "world clock 默认值",
        "为 timeline、test_environment 与 time_check 补上或移除原版 overworld 时钟默认值；"
        "自定义时钟与 time marker 阻断。",
        (
            "timeline.clock-and-time-markers@101.1",
            "predicate.time-check-clock@101.1",
            "test-environment.time-of-day-to-clock-time@101.1",
        ),
    ),
    (
        "recipes@101.1",
        "26.1 配方子集",
        "转换可逆的 result 形式与默认字段；crafting_dye/imbue 等新配方类型阻断。",
        ("recipe.syntax-and-types@101.1",),
    ),
)


def _builtin_plugins() -> tuple[PluginInfo, ...]:
    """Build the built-in plugin catalog and verify it covers every built-in rule."""

    from . import __version__

    infos: list[PluginInfo] = []
    for plugin_id, name, description, rule_ids in _BUILTIN_PLUGIN_DEFS:
        infos.append(
            PluginInfo(
                id=plugin_id,
                name=name,
                description=description,
                version=__version__,
                origin="builtin",
                kind="builtin",
                enabled=True,
                rules=rule_ids,
            )
        )
    covered = {rule_id for info in infos for rule_id in info.rules}
    builtin_ids = {rule.id for rule in BUILTIN_RULES}
    if covered != builtin_ids:
        missing = sorted(builtin_ids - covered)
        duplicates = sorted(covered - builtin_ids)
        raise ValueError(
            f"Built-in plugin catalog is out of sync with BUILTIN_RULES: missing={missing} unknown={duplicates}"
        )
    if len({info.id for info in infos}) != len(infos):
        raise ValueError("Built-in plugin ids must be unique")
    return tuple(infos)


BUILTIN_PLUGINS = _builtin_plugins()


def default_plugin_dir() -> Path:
    """Resolve the plugin store directory for this dpcompat installation.

    Plugins live next to the installed package (``site-packages/dpcompat/plugins``)
    so a plugin installed in one Python environment never affects another
    dpcompat installation.  ``DPCOMPAT_PLUGIN_DIR`` overrides the location for
    CI, containers, and environments whose package directory is read-only.
    """

    override = os.environ.get(PLUGIN_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "plugins"


def scaffold_plugin_template(name: str, location: Path, *, subfolder: bool = False) -> Path:
    """Create a starter plugin project and return the generated plugin file.

    ``name`` becomes the plugin id and the file name; it must match the plugin id
    pattern.  With ``subfolder`` the project is created in ``location/name``.
    """

    name = name.strip()
    if not _TEMPLATE_NAME_RE.fullmatch(name):
        raise ValueError("插件名称只能包含小写字母、数字、'.'、'_'、'-'，且不能为空")
    location = location.expanduser().resolve()
    if not location.is_dir():
        raise ValueError(f"模板位置不存在或不是文件夹：{location}")
    target_dir = location / name if subfolder else location
    target_dir.mkdir(parents=True, exist_ok=True)
    plugin_file = target_dir / f"{name}.py"
    if plugin_file.exists():
        raise ValueError(f"插件文件已存在：{plugin_file}")
    plugin_file.write_text(_TEMPLATE_SOURCE.format(name=name), encoding="utf-8")
    readme = target_dir / "README.md"
    if not readme.exists():
        readme.write_text(_TEMPLATE_README.format(name=name), encoding="utf-8")
    return plugin_file


def _load_python_module(path: Path) -> Any:
    module_name = "dpcompat_plugin_" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load plugin module: {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginStore:
    """Filesystem store for plugin files plus persistent enable state."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory if directory is not None else default_plugin_dir()).expanduser().resolve()

    # -- state -----------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.directory / STATE_FILE_NAME

    def _load_state(self) -> dict[str, bool]:
        """Return {plugin_id: enabled} overrides; missing ids default to enabled."""
        path = self._state_path()
        if not path.is_file():
            return {}
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        table = raw.get("plugin")
        if table is None:
            return {}  # An empty state file means every plugin is enabled.
        if not isinstance(table, dict):
            raise ValueError(f"{STATE_FILE_NAME}: [plugin] must be a table")
        state: dict[str, bool] = {}
        for key, value in table.items():
            if not isinstance(value, dict) or set(value) != {"enabled"} or not isinstance(value.get("enabled"), bool):
                raise ValueError(f"{STATE_FILE_NAME}: invalid entry for plugin {key!r}")
            state[str(key)] = bool(value["enabled"])
        return state

    def _save_state(self, state: dict[str, bool]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        lines = [
            "# DPCompat plugin enable state. Missing plugins are enabled by default.",
            "# Managed by `dpcompat plugin` and the TUI; edit with care.",
        ]
        for plugin_id in sorted(state):
            lines.append(f'[plugin."{plugin_id}"]')
            lines.append(f"enabled = {'true' if state[plugin_id] else 'false'}")
        self._state_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Persist an enable/disable decision for a known plugin."""

        known = {info.id for info in self.list_plugins()}
        if plugin_id not in known:
            raise ValueError(f"Unknown plugin {plugin_id!r}")
        state = self._load_state()
        if enabled:
            state.pop(plugin_id, None)
        else:
            state[plugin_id] = False
        self._save_state(state)

    # -- listing ----------------------------------------------------------------

    def _installed_infos(self) -> tuple[PluginInfo, ...]:
        if not self.directory.is_dir():
            return ()
        infos: list[PluginInfo] = []
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and path.suffix.lower() in {".py", ".json"}:
                infos.append(self._inspect_file(path))
        return tuple(infos)

    def list_plugins(self) -> tuple[PluginInfo, ...]:
        """Return built-in and installed plugins with the persisted state applied."""

        state = self._load_state()
        all_infos = [*BUILTIN_PLUGINS, *self._installed_infos()]
        return tuple(info.model_copy(update={"enabled": state.get(info.id, True)}) for info in all_infos)

    def enabled_rule_ids(self) -> frozenset[str]:
        """Return every rule id contributed by currently enabled plugins."""

        return frozenset(rule_id for info in self.list_plugins() if info.enabled for rule_id in info.rules)

    # -- install / uninstall ----------------------------------------------------

    def install(self, path: Path, *, force: bool = False) -> PluginInfo:
        """Validate and copy a Python or JSON plugin file into the store."""

        path = path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Plugin file does not exist: {path}")
        suffix = path.suffix.lower()
        if suffix not in {".py", ".json"}:
            raise ValueError("A plugin file must end with .py or .json")
        info = self._inspect_file(path)

        for existing in self.list_plugins():
            if existing.id != info.id:
                continue
            if existing.origin == "builtin":
                raise ValueError(f"Plugin id {info.id!r} conflicts with a built-in plugin")
            if not force:
                raise ValueError(f"Plugin {info.id!r} is already installed; use --force to replace it")

        # Rule-id collisions are checked against built-ins and other installed plugins.
        owned = {rule_id for plugin in self.list_plugins() if plugin.id != info.id for rule_id in plugin.rules}
        collision = owned & set(info.rules)
        if collision:
            rendered = ", ".join(sorted(collision))
            raise ValueError(f"Rules provided by another plugin: {rendered}")

        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{info.id}{suffix}"
        if destination.exists() and not force:
            raise ValueError(f"A plugin file already exists at {destination.name}; use --force to replace it")
        shutil.copy2(path, destination)
        return info.model_copy(update={"path": str(destination)})

    def uninstall(self, plugin_id: str) -> None:
        """Remove an installed plugin file and its state entry."""

        installed = {info.id: info for info in self._installed_infos()}
        info = installed.get(plugin_id)
        if info is None or info.path is None:
            raise ValueError(f"No installed plugin with id {plugin_id!r}")
        Path(info.path).unlink(missing_ok=True)
        state = self._load_state()
        state.pop(plugin_id, None)
        self._save_state(state)

    # -- file inspection ---------------------------------------------------------

    def _inspect_file(self, path: Path) -> PluginInfo:
        if path.suffix.lower() == ".py":
            return self._inspect_python(path)
        return self._inspect_json(path)

    def _inspect_python(self, path: Path) -> PluginInfo:
        module = _load_python_module(path)
        raw = getattr(module, "PLUGIN", None)
        if raw is None:
            raise ValueError(f"Python plugin {path.name} must define a PLUGIN dict")
        meta = PluginMeta.model_validate(raw)
        value = getattr(module, "dpcompat_rules", None)
        if value is None:
            value = getattr(module, "RULES", None)
        if value is None:
            raise ValueError(f"Python plugin {path.name} must expose RULES or dpcompat_rules()")
        rules = RuleRegistry._rules_from_object(value, origin=meta.id)
        rule_ids = tuple(_validated_rule_id(rule.id, meta.id) for rule in rules)
        return PluginInfo.from_meta(meta, origin="file", kind="python", rules=rule_ids, path=str(path))

    def _inspect_json(self, path: Path) -> PluginInfo:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON plugin {path.name}: {exc}") from exc
        if isinstance(raw, dict) and "plugin" in raw:
            model = JsonPluginFile.model_validate(raw)
            meta = model.plugin
            rule_ids = tuple(_validated_rule_id(spec.id, meta.id) for spec in model.rules)
        else:
            spec = DeclarativeRuleSpec.model_validate(raw)
            meta = PluginMeta(
                id=spec.id,
                name=spec.id,
                description=spec.description,
                version="1.0.0",
            )
            rule_ids = (_validated_rule_id(spec.id, meta.id),)
        return PluginInfo.from_meta(meta, origin="file", kind="declarative", rules=rule_ids, path=str(path))


def _validated_rule_id(value: Any, plugin_id: str) -> str:
    if not isinstance(value, str) or not _RULE_ID_RE.fullmatch(value):
        raise ValueError(f"Plugin {plugin_id!r} declares an invalid rule id: {value!r}")
    return value


def _register_declarative_file(registry: RuleRegistry, path: Path, plugin_id: str) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "plugin" in raw:
        model = JsonPluginFile.model_validate(raw)
        specs: tuple[DeclarativeRuleSpec, ...] = model.rules
    else:
        specs = (DeclarativeRuleSpec.model_validate(raw),)
    for spec in specs:
        registry.register(
            DeclarativeMigrationRule(spec, source_path=path),
            origin=f"plugin:{plugin_id}",
        )


def create_effective_registry(config: ProjectConfig | None = None, store: PluginStore | None = None) -> RuleRegistry:
    """Build the registry from enabled built-in and installed plugins.

    Project ``[rules]`` modules, files, and entry points declared in ``config``
    remain active on top of the plugin state.
    """

    config = config or ProjectConfig()
    store = store or PluginStore()
    registry = create_rule_registry(
        modules=config.rules.modules,
        files=config.rules.files,
        load_entry_points=config.rules.load_entry_points,
        enabled_rule_ids=store.enabled_rule_ids(),
    )
    for info in store.list_plugins():
        if not info.enabled or info.origin != "file" or info.path is None:
            continue
        path = Path(info.path)
        if info.kind == "python":
            registry.load_module_file(
                path,
                origin=f"plugin:{info.id}",
                default_sources=tuple(str(url) for url in _plugin_meta_sources(info)),
            )
        else:
            _register_declarative_file(registry, path, info.id)
    return registry


def _plugin_meta_sources(info: PluginInfo) -> tuple[HttpUrl, ...]:
    """Return the plugin file's declared official sources, if any."""

    path = Path(info.path) if info.path else None
    if path is None or not path.is_file():
        return ()
    try:
        if info.kind == "python":
            module = _load_python_module(path)
            raw = getattr(module, "PLUGIN", None)
            return PluginMeta.model_validate(raw).official_sources if isinstance(raw, dict) else ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "plugin" in raw:
            return JsonPluginFile.model_validate(raw).plugin.official_sources
        return ()
    except (OSError, ValueError):
        return ()
