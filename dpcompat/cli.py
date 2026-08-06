"""Rich command-line adapter for inspection, planning, building, and server checks."""

from __future__ import annotations

import argparse
import io
import json
import logging
import shutil
import sys
import tempfile
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import ProjectConfig, load_config
from .detector import detect_pack
from .engine import compile_pack
from .logging_config import setup_logging
from .models import BuildPolicy, Diagnostic, PackFormat, Severity, VersionProfile
from .packio import materialize_source
from .plugins import PluginStore, create_effective_registry
from .report import build_report, write_report
from .rules import RuleRegistry
from .servercheck import check_with_server
from .versions import PROFILES, resolve_profile

console = Console()
error_console = Console(stderr=True)
logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _diagnostic_style(item: Diagnostic) -> str:
    if item.severity >= Severity.ERROR:
        return "bold red"
    if item.severity >= Severity.WARNING:
        return "yellow"
    return "cyan"


def _print_diagnostics(diagnostics: list[Diagnostic]) -> None:
    """Render diagnostics as a compact Rich table."""

    if not diagnostics:
        return
    table = Table(title="Diagnostics", show_lines=False, header_style="bold")
    table.add_column("Level", width=7)
    table.add_column("Code", style="bold")
    table.add_column("Location")
    table.add_column("Compatibility")
    table.add_column("Message", overflow="fold")
    for item in diagnostics:
        location = item.path or "—"
        if item.line:
            location += f":{item.line}"
        table.add_row(
            Text(item.severity.label.upper(), style=_diagnostic_style(item)),
            item.code,
            location,
            item.compatibility.value if item.compatibility else "—",
            item.message,
        )
    console.print(table)


def _command_version(_args: argparse.Namespace) -> int:
    from . import __version__

    console.print(f"DPCompat [bold cyan]v{__version__}[/bold cyan]")
    return 0


def _command_versions(args: argparse.Namespace) -> int:
    if args.json:
        console.print_json(
            _json(
                [
                    {
                        "game_version": profile.game_version,
                        "pack_format": str(profile.pack_format),
                        "release_date": profile.release_date,
                        "java_major": profile.java_major,
                        "capabilities": sorted(profile.capabilities),
                        "official_url": str(profile.official_url),
                    }
                    for profile in PROFILES
                ]
            )
        )
        return 0
    table = Table(title="Supported stable Minecraft releases", header_style="bold magenta")
    table.add_column("Minecraft", style="cyan")
    table.add_column("Pack format", justify="right")
    table.add_column("Java", justify="right")
    table.add_column("Released")
    table.add_column("Notes")
    for profile in PROFILES:
        table.add_row(
            profile.game_version,
            str(profile.pack_format),
            str(profile.java_major),
            profile.release_date,
            profile.note,
        )
    console.print(table)
    return 0


def _command_rules(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config else ProjectConfig()
    registry = _registry(config)
    info = registry.info()
    if args.json:
        console.print_json(_json([item.model_dump(mode="json") for item in info]))
        return 0
    table = Table(title="Effective migration rules", header_style="bold magenta")
    table.add_column("Priority", justify="right")
    table.add_column("Rule id", style="cyan")
    table.add_column("Boundary")
    table.add_column("Origin", overflow="fold")
    for item in info:
        table.add_row(str(item.priority), item.id, str(item.boundary or "—"), item.origin)
    console.print(table)
    return 0


def _command_inspect(args: argparse.Namespace) -> int:
    with materialize_source(args.source, pack_root=args.pack_root) as root:
        result = detect_pack(root)
    if args.json:
        console.print_json(
            _json(
                {
                    "source_format": str(result.source_format),
                    "declared_range": {
                        "minimum": str(result.declared_range.minimum),
                        "maximum": str(result.declared_range.maximum),
                    },
                    "inferred_format": str(result.inferred_format),
                    "candidate_versions": result.candidates,
                    "confidence": result.confidence,
                    "evidence": [item.as_dict() for item in result.evidence],
                    "diagnostics": [item.as_dict() for item in result.diagnostics],
                }
            )
        )
    else:
        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bold")
        summary.add_column()
        summary.add_row("Source syntax", str(result.source_format))
        summary.add_row(
            "Declared range",
            f"{result.declared_range.minimum}..{result.declared_range.maximum}",
        )
        summary.add_row("Content minimum", str(result.inferred_format))
        summary.add_row("Candidate releases", ", ".join(result.candidates) or "unknown")
        summary.add_row("Confidence", f"{result.confidence:.0%}")
        console.print(Panel(summary, title="Data-pack inspection", border_style="cyan"))
        _print_diagnostics(result.diagnostics)
    return 2 if any(item.severity >= Severity.ERROR for item in result.diagnostics) else 0


def _parse_fallbacks(values: list[str] | None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError("--fallback must use TARGET=PATH")
        target, raw_path = value.split("=", 1)
        result[target.strip()] = Path(raw_path).expanduser().resolve()
    return result


def _load_effective_config(args: argparse.Namespace) -> ProjectConfig:
    config = load_config(args.config) if getattr(args, "config", None) else ProjectConfig()
    if getattr(args, "source_format", None):
        config.source_format = PackFormat.parse(args.source_format)
    if getattr(args, "output_name", None):
        config.output_name = args.output_name
    if getattr(args, "pack_root", None):
        config.pack_root = args.pack_root
    config.fallbacks.update(_parse_fallbacks(getattr(args, "fallback", None)))
    config.policy = BuildPolicy(
        allow_emulated=(False if getattr(args, "deny_emulated", False) else config.policy.allow_emulated),
        allow_lossy=(True if getattr(args, "allow_lossy", False) else config.policy.allow_lossy),
        allow_unknown=(True if getattr(args, "allow_unknown", False) else config.policy.allow_unknown),
        fail_on_warnings=(True if getattr(args, "fail_on_warnings", False) else config.policy.fail_on_warnings),
    )
    return config


def _registry(config: ProjectConfig) -> RuleRegistry:
    return create_effective_registry(config)


def _profiles(args: argparse.Namespace, config: ProjectConfig) -> list[VersionProfile]:
    selected = list(getattr(args, "target", None) or config.targets)
    return [resolve_profile(item) for item in selected] if selected else list(PROFILES)


def _compile(args: argparse.Namespace, *, emit_archives: bool) -> tuple[int, dict[str, Any]]:
    config = _load_effective_config(args)
    profiles = _profiles(args, config)
    registry = _registry(config)
    output = getattr(args, "output", Path("dist")).expanduser().resolve()
    universal = config.universal and not getattr(args, "no_universal", False)
    if emit_archives and config.clean_output and output.exists():
        prefix = config.output_name + "-"
        for path in output.iterdir():
            if path.is_file() and (path.name.startswith(prefix) or path.name == "compatibility-report.json"):
                logger.debug("Removing stale generated artifact %s", path)
                path.unlink()

    status_text = f"Compiling {len(profiles)} target release(s) with {len(registry.rules())} rules"
    status_context = nullcontext() if getattr(args, "json", False) else console.status(status_text, spinner="dots")
    with status_context, materialize_source(args.source, pack_root=config.pack_root) as root:
        detection, results, universal_archive = compile_pack(
            root,
            profiles,
            output,
            universal,
            policy=config.policy,
            source_format=config.source_format,
            fallbacks=config.fallbacks,
            output_name=config.output_name,
            emit_archives=emit_archives,
            rules=registry.rules(),
        )

    json_mode = bool(getattr(args, "json", False))
    if not json_mode:
        _print_diagnostics(detection.diagnostics)
    table = Table(title="Target results", header_style="bold magenta")
    table.add_column("Target", style="cyan")
    table.add_column("Format", justify="right")
    table.add_column("Status")
    table.add_column("Rules", justify="right")
    table.add_column("Artifact / SHA-256", overflow="fold")
    for result in results:
        status = Text("OK", style="bold green") if result.successful else Text("FAILED", style="bold red")
        artifact = result.archive.name if result.archive else (result.sha256 or "—")
        changed = sum(1 for migration in result.migrations if migration.changed_files or migration.changed_nodes)
        table.add_row(
            result.profile.game_version,
            str(result.profile.pack_format),
            status,
            str(changed),
            artifact,
        )
        if result.diagnostics and not json_mode:
            _print_diagnostics(result.diagnostics)
    if not json_mode:
        console.print(table)

    report = build_report(detection, results, universal_archive, policy=config.policy)
    report["rule_registry"] = [item.model_dump(mode="json") for item in registry.info()]
    if emit_archives:
        report_path = output / "compatibility-report.json"
        write_report(report_path, report)
        if not json_mode:
            console.print(f"Report: [cyan]{report_path}[/cyan]")
            if universal_archive:
                console.print(f"Universal pack: [cyan]{universal_archive}[/cyan]")
    failed = not results or any(not result.successful for result in results)
    return (2 if failed else 0), report


def _command_plan(args: argparse.Namespace) -> int:
    code, report = _compile(args, emit_archives=False)
    if args.json:
        console.print_json(_json(report))
    return code


def _command_validate(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="dpcompat-validate-") as temp_dir:
        args.output = Path(temp_dir)
        args.no_universal = True
        return _compile(args, emit_archives=False)[0]


def _command_build(args: argparse.Namespace) -> int:
    return _compile(args, emit_archives=True)[0]


def _command_plugin_list(args: argparse.Namespace) -> int:
    store = PluginStore()
    infos = store.list_plugins()
    if args.json:
        console.print_json(_json([item.model_dump(mode="json", exclude={"readme"}) for item in infos]))
        return 0
    table = Table(title="Migration rule plugins", header_style="bold magenta")
    table.add_column("Enabled", justify="center")
    table.add_column("Plugin", style="cyan")
    table.add_column("Name")
    table.add_column("Target", justify="center")
    table.add_column("Rules", justify="right")
    table.add_column("Origin")
    table.add_column("Description", overflow="fold")
    for item in infos:
        table.add_row(
            "on" if item.enabled else "off",
            item.id,
            item.name,
            item.target_version,
            str(len(item.rules)),
            item.origin,
            item.description,
        )
    console.print(table)
    return 0


def _command_plugin_install(args: argparse.Namespace) -> int:
    store = PluginStore()
    info = store.install(args.path, force=args.force)
    console.print(f"Installed plugin [bold cyan]{info.name}[/bold cyan] ({info.id})")
    return 0


def _command_plugin_remove(args: argparse.Namespace) -> int:
    store = PluginStore()
    store.uninstall(args.plugin_id)
    console.print(f"Removed plugin [bold cyan]{args.plugin_id}[/bold cyan]")
    return 0


def _command_plugin_enable(args: argparse.Namespace) -> int:
    store = PluginStore()
    store.set_enabled(args.plugin_id, True)
    console.print(f"Enabled plugin [bold cyan]{args.plugin_id}[/bold cyan]")
    return 0


def _command_plugin_disable(args: argparse.Namespace) -> int:
    store = PluginStore()
    store.set_enabled(args.plugin_id, False)
    console.print(f"Disabled plugin [bold cyan]{args.plugin_id}[/bold cyan]")
    return 0


def _command_tui(args: argparse.Namespace) -> int:
    from .ui import DpCompatApp

    DpCompatApp(config_path=args.config).run()
    return 0


def _command_server_check(args: argparse.Namespace) -> int:
    result = check_with_server(
        args.pack,
        args.server_jar,
        java=args.java,
        timeout=args.timeout,
        keep_directory=args.keep,
        accept_eula=args.accept_eula,
    )
    console.print("[bold green]PASS[/bold green]" if result.success else "[bold red]FAIL[/bold red]")
    if result.returncode is not None:
        console.print(f"Server exit code: {result.returncode}")
    if result.matched_errors:
        _print_diagnostics([Diagnostic(Severity.ERROR, "server-load-error", line) for line in result.matched_errors])
    if not result.success and result.output_tail:
        console.print(Panel("\n".join(result.output_tail), title="Server output tail"))
    if result.log_path:
        console.print(f"Log: [cyan]{result.log_path}[/cyan]")
    return 0 if result.success else 2


def _add_policy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", type=Path, help="Optional dpcompat.toml")
    parser.add_argument("--source-format", help="Override detected source format after review")
    parser.add_argument("--pack-root", help="Relative pack directory inside a multi-pack folder or ZIP")
    parser.add_argument("--deny-emulated", action="store_true")
    parser.add_argument("--allow-lossy", action="store_true")
    parser.add_argument("--allow-unknown", action="store_true")
    parser.add_argument("--fail-on-warnings", action="store_true")
    parser.add_argument(
        "--fallback",
        action="append",
        help="Merge a reviewed target implementation using TARGET=PATH",
    )


def _add_target_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        "-t",
        action="append",
        help="Repeat for multiple targets; defaults to every registered release",
    )
    parser.add_argument("--output-name", help="Artifact filename prefix")
    _add_policy_options(parser)


def build_parser() -> argparse.ArgumentParser:
    """Construct the complete parser without reading process-global arguments."""

    parser = argparse.ArgumentParser(
        prog="dpcompat",
        description=("Conservative Minecraft Java data-pack compatibility compiler for stable 1.21.4+ releases."),
    )
    parser.add_argument("--verbose", action="store_true", help="Show debug logs on the console")
    parser.add_argument("--quiet", action="store_true", help="Only show errors on the console")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version", help="Display the DPCompat version")
    version_parser.set_defaults(handler=_command_version)

    versions_parser = subparsers.add_parser("versions", help="List registered stable targets")
    versions_parser.add_argument("--json", action="store_true")
    versions_parser.set_defaults(handler=_command_versions)

    rules_parser = subparsers.add_parser("rules", help="List effective built-in and extension rules")
    rules_parser.add_argument("--config", "-c", type=Path)
    rules_parser.add_argument("--json", action="store_true")
    rules_parser.set_defaults(handler=_command_rules)

    inspect_parser = subparsers.add_parser("inspect", help="Detect source format and scan features")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--pack-root", help="Relative pack directory inside a multi-pack folder or ZIP")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_command_inspect)

    plan_parser = subparsers.add_parser("plan", help="Run migrations without writing ZIP files")
    plan_parser.add_argument("source", type=Path)
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.add_argument("--no-universal", action="store_true")
    _add_target_options(plan_parser)
    plan_parser.set_defaults(handler=_command_plan)

    validate_parser = subparsers.add_parser("validate", help="Validate one or more target builds")
    validate_parser.add_argument("source", type=Path)
    validate_parser.add_argument("--no-universal", action="store_true")
    _add_target_options(validate_parser)
    validate_parser.set_defaults(handler=_command_validate)

    build_parser_ = subparsers.add_parser("build", help="Build per-release and universal ZIPs")
    build_parser_.add_argument("source", type=Path)
    build_parser_.add_argument("--output", "-o", type=Path, default=Path("dist"))
    build_parser_.add_argument("--no-universal", action="store_true")
    _add_target_options(build_parser_)
    build_parser_.set_defaults(handler=_command_build)

    server_parser = subparsers.add_parser(
        "server-check",
        help="Boot a local vanilla server JAR and inspect data-pack load logs",
    )
    server_parser.add_argument("pack", type=Path)
    server_parser.add_argument("--server-jar", type=Path, required=True)
    server_parser.add_argument("--java", default="java")
    server_parser.add_argument("--timeout", type=float, default=120.0)
    server_parser.add_argument("--keep", type=Path, help="Keep the temporary server directory")
    server_parser.add_argument("--accept-eula", action="store_true")
    server_parser.set_defaults(handler=_command_server_check)

    plugin_parser = subparsers.add_parser("plugin", help="Manage migration rule plugins")
    plugin_subparsers = plugin_parser.add_subparsers(dest="plugin_action", required=True)

    plugin_list = plugin_subparsers.add_parser("list", help="List built-in and installed plugins")
    plugin_list.add_argument("--json", action="store_true")
    plugin_list.set_defaults(handler=_command_plugin_list)

    plugin_install = plugin_subparsers.add_parser("install", help="Install a .py or .json plugin file")
    plugin_install.add_argument("path", type=Path)
    plugin_install.add_argument("--force", action="store_true", help="Replace an already installed plugin")
    plugin_install.set_defaults(handler=_command_plugin_install)

    plugin_remove = plugin_subparsers.add_parser("remove", help="Uninstall an installed plugin")
    plugin_remove.add_argument("plugin_id")
    plugin_remove.set_defaults(handler=_command_plugin_remove)

    plugin_enable = plugin_subparsers.add_parser("enable", help="Enable a built-in or installed plugin")
    plugin_enable.add_argument("plugin_id")
    plugin_enable.set_defaults(handler=_command_plugin_enable)

    plugin_disable = plugin_subparsers.add_parser("disable", help="Disable a built-in or installed plugin")
    plugin_disable.add_argument("plugin_id")
    plugin_disable.set_defaults(handler=_command_plugin_disable)

    tui_parser = subparsers.add_parser("tui", help="Open the interactive Textual interface")
    tui_parser.add_argument("--config", "-c", type=Path, help="Optional dpcompat.toml")
    tui_parser.set_defaults(handler=_command_tui)
    return parser


def run_application(argv: list[str] | None = None) -> int:
    """Execute one CLI command and translate expected failures to stable exit codes."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose and args.quiet:
        parser.error("--verbose and --quiet cannot be combined")
    try:
        return int(args.handler(args))
    except (OSError, ValueError, ValidationError, shutil.Error) as exc:
        logger.exception("Command failed")
        error_console.print(f"[bold red]ERROR[/bold red] {exc}")
        return 2
    except KeyboardInterrupt:
        logger.warning("Execution interrupted by user")
        error_console.print("[bold yellow]Interrupted by user[/bold yellow]")
        return 130


def _ensure_utf8_stdio() -> None:
    """Reconfigure non-UTF-8 stdio streams so Unicode output never crashes.

    Windows pipes and files inherit the ANSI codepage (e.g. cp1252 on English
    systems), which cannot encode plugin names or diagnostics in Chinese; real
    Windows console streams already use UTF-8 and are left untouched.  The CLI
    therefore writes UTF-8 everywhere, matching Python's UTF-8 mode, so piped
    and file output is predictable on every locale.
    """

    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        encoding = stream.encoding
        if encoding is None or encoding.lower().replace("-", "") == "utf8":
            continue
        with suppress(ValueError, OSError):  # Unusual stream types must not break the CLI.
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    """Configure queued Rich logging, run the CLI, and release file handlers."""

    _ensure_utf8_stdio()
    preview = build_parser().parse_args(argv)
    if getattr(preview, "json", False):
        console_level = logging.CRITICAL
    else:
        console_level = logging.DEBUG if preview.verbose else logging.ERROR if preview.quiet else logging.INFO
    log_dir = preview.log_dir.expanduser().resolve()
    with setup_logging(
        log_dir=log_dir,
        application_name="dpcompat",
        console_level=console_level,
        package_files={
            "dpcompat.engine": log_dir / "engine.log",
            "dpcompat.migrations": log_dir / "migrations.log",
            "dpcompat.rules": log_dir / "rules.log",
            "dpcompat.packio": log_dir / "io.log",
        },
    ):
        raise SystemExit(run_application(argv))
