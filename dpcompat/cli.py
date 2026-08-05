"""Rich command-line adapter for inspection, planning, building, and server checks."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
from contextlib import nullcontext
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
from .report import build_report, write_report
from .rules import RuleRegistry, create_rule_registry
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
    with materialize_source(args.source) as root:
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
    config.fallbacks.update(_parse_fallbacks(getattr(args, "fallback", None)))
    config.policy = BuildPolicy(
        allow_emulated=(False if getattr(args, "deny_emulated", False) else config.policy.allow_emulated),
        allow_lossy=(True if getattr(args, "allow_lossy", False) else config.policy.allow_lossy),
        allow_unknown=(True if getattr(args, "allow_unknown", False) else config.policy.allow_unknown),
        fail_on_warnings=(True if getattr(args, "fail_on_warnings", False) else config.policy.fail_on_warnings),
    )
    return config


def _registry(config: ProjectConfig) -> RuleRegistry:
    return create_rule_registry(
        modules=config.rules.modules,
        files=config.rules.files,
        load_entry_points=config.rules.load_entry_points,
    )


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
    with status_context, materialize_source(args.source) as root:
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


def _add_policy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", type=Path, help="Optional dpcompat.toml")
    parser.add_argument("--source-format", help="Override detected source format after review")
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


def main(argv: list[str] | None = None) -> None:
    """Configure queued Rich logging, run the CLI, and release file handlers."""

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
