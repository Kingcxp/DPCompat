"""Verify queued Rich logging and per-module rotating files."""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

from dpcompat.logging_config import setup_logging


def test_logging_routes_module_records_to_dedicated_files(tmp_path: Path) -> None:
    stream = StringIO()
    with setup_logging(
        log_dir=tmp_path,
        console_output=stream,
        package_files={"dpcompat.migrations": tmp_path / "migrations.log"},
    ):
        logging.getLogger("dpcompat.migrations.test").warning("migration detail")
        logging.getLogger("dpcompat.engine").error("engine failure")

    assert "migration detail" in (tmp_path / "migrations.log").read_text(encoding="utf-8")
    assert "engine failure" in (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "migration detail" in stream.getvalue()
