"""Global configuration for logging."""

from __future__ import annotations

import logging
import queue
import sys
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass, field
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import TextIO

from rich.console import Console
from rich.logging import RichHandler


class LoggerPrefixFilter(logging.Filter):
    """Include or exclude records according to the prefix of the logger."""

    def __init__(self, prefix: str, *, include: bool = True) -> None:
        super().__init__()
        self.prefix = prefix
        self.include = include

    def filter(self, record: logging.LogRecord) -> bool:
        matched = record.name == self.prefix or record.name.startswith(f"{self.prefix}.")
        return matched if self.include else not matched


class LocalQueueHandler(QueueHandler):
    """Queue for log in the same process."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy(record)


@dataclass
class LoggingRuntime:
    """Manage resources during the runtime."""

    listener: QueueListener
    queue_handler: QueueHandler
    output_handlers: tuple[logging.Handler, ...]
    _closed: bool = field(default=False, init=False)

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        root = logging.getLogger()
        if self.queue_handler in root.handlers:
            root.removeHandler(self.queue_handler)

        self.listener.stop()

        for handler in self.output_handlers:
            handler.flush()
            handler.close()

    def __enter__(self) -> LoggingRuntime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _create_rotating_file_handler(
    path: Path,
    *,
    level: int,
    formatter: logging.Formatter,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=path, mode="a", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8", delay=True
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)

    return handler


def setup_logging(
    *,
    log_dir: str | Path = "logs",
    application_name: str = "DPCompat",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    console_output: TextIO | None = None,
    package_files: Mapping[str, str | Path] | None = None,
    exclusive_package_files: bool = False,
    max_file_bytes: int = 20 * 1024 * 1024,
    backup_count: int = 10,
) -> LoggingRuntime:
    """Initialize logging configuration for the application.

    Args:
        log_dir (str | Path): Directory to store log files.
        application_name (str): Name of the application for logging purposes.
        console_level (int): Logging level for console output.
        file_level (int): Logging level for file output.
        console_output (TextIO | None): Output stream for console logs. Defaults to sys.stderr if None.
        package_files (Mapping[str, str | Path] | None): Mapping of package names to log file paths.
        exclusive_package_files (bool): If True, only log specified packages to their respective files.
        max_file_bytes (int): Maximum size of log files before rotation.
        backup_count (int): Number of backup log files to keep.
    """

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    package_files = package_files or {}

    detailed_formatter = logging.Formatter(
        fmt=("%(asctime)s | %(levelname)-8s | %(name)s | %(processName)s/%(threadName)s | %(message)s"),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = console_output if console_output is not None else sys.stderr

    rich_console = Console(
        file=stream,
        force_terminal=False if console_output is not None else None,
        no_color=console_output is not None,
    )

    rich_handler = RichHandler(
        console=rich_console,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        show_path=True,
        markup=False,
    )
    rich_handler.setLevel(console_level)
    rich_handler.setFormatter(logging.Formatter("%(message)s"))

    application_handler = _create_rotating_file_handler(
        log_dir / "application.log",
        level=file_level,
        formatter=detailed_formatter,
        max_bytes=max_file_bytes,
        backup_count=backup_count,
    )

    error_handler = _create_rotating_file_handler(
        log_dir / "errors.log",
        level=logging.ERROR,
        formatter=detailed_formatter,
        max_bytes=max_file_bytes,
        backup_count=backup_count,
    )

    output_handlers: list[logging.Handler] = [
        rich_handler,
        application_handler,
        error_handler,
    ]

    for logger_prefix, file_path in package_files.items():
        package_handler = _create_rotating_file_handler(
            Path(file_path),
            level=file_level,
            formatter=detailed_formatter,
            max_bytes=max_file_bytes,
            backup_count=backup_count,
        )
        package_handler.addFilter(LoggerPrefixFilter(logger_prefix, include=True))
        output_handlers.append(package_handler)

        if exclusive_package_files:
            application_handler.addFilter(LoggerPrefixFilter(logger_prefix, include=False))

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()

    queue_handler = LocalQueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)

    listener = QueueListener(
        log_queue,
        *output_handlers,
        respect_handler_level=True,
    )

    root_logger = logging.getLogger()

    for old_handler in root_logger.handlers[:]:
        root_logger.removeHandler(old_handler)
        old_handler.close()

    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(queue_handler)

    logging.getLogger(application_name).setLevel(logging.DEBUG)

    logging.captureWarnings(True)

    listener.start()

    return LoggingRuntime(
        listener=listener,
        queue_handler=queue_handler,
        output_handlers=tuple(output_handlers),
    )
