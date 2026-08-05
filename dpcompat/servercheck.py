"""Boot a user-supplied vanilla server JAR to smoke-test a built archive.

This check verifies loading and selected log errors; it does not download Minecraft, accept the
EULA implicitly, or prove gameplay equivalence.  Callers must supply the matching server and
explicitly acknowledge the EULA.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ServerCheckResult(BaseModel):
    """Observable outcome of one vanilla server smoke test."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    returncode: int | None
    log_path: Path | None
    matched_errors: list[str] = Field(default_factory=list)
    output_tail: list[str] = Field(default_factory=list)


_ERROR_MARKERS = (
    "failed to load datapacks",
    "errors in currently selected datapacks",
    "parsing error loading function",
    "couldn't load tag",
    "failed to parse",
    "failed to load function",
)
_READY_MARKERS = ("Done (", 'For help, type "help"')


def check_with_server(
    pack: Path,
    server_jar: Path,
    *,
    java: str = "java",
    timeout: float = 120.0,
    keep_directory: Path | None = None,
    accept_eula: bool = False,
) -> ServerCheckResult:
    """Load ``pack`` with a supplied server JAR and inspect startup logs."""

    pack = pack.expanduser().resolve()
    server_jar = server_jar.expanduser().resolve()
    if not accept_eula:
        raise ValueError("Refusing to start the server without explicit EULA acceptance")
    if not pack.is_file():
        raise ValueError(f"Pack archive does not exist: {pack}")
    if not server_jar.is_file():
        raise ValueError(f"Server JAR does not exist: {server_jar}")

    context = tempfile.TemporaryDirectory(prefix="dpcompat-server-") if keep_directory is None else None
    if context is not None:
        root = Path(context.name)
    else:
        assert keep_directory is not None
        root = keep_directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        (root / "server.properties").write_text(
            "level-name=world\n"
            "online-mode=false\n"
            "enable-command-block=true\n"
            "max-players=1\n"
            "view-distance=2\n"
            "simulation-distance=2\n",
            encoding="utf-8",
        )
        datapacks = root / "world/datapacks"
        datapacks.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pack, datapacks / pack.name)
        local_jar = root / "server.jar"
        if local_jar != server_jar:
            shutil.copy2(server_jar, local_jar)

        process = subprocess.Popen(
            [java, "-Xms512M", "-Xmx1G", "-jar", str(local_jar), "nogui"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        lines: list[str] = []
        ready = False
        deadline = time.monotonic() + timeout
        assert process.stdout is not None
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            # stdout iteration can block even after the main thread reaches its timeout.
            # A daemon reader plus Queue lets the controller enforce a real deadline.
            assert process.stdout is not None
            for stream_line in process.stdout:
                output_queue.put(stream_line)
            output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        stream_closed = False
        while time.monotonic() < deadline and not stream_closed:
            try:
                line = output_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                stream_closed = True
                break
            lines.append(line.rstrip("\r\n"))
            if any(marker in line for marker in _READY_MARKERS):
                ready = True
                break

        if ready and process.stdin is not None:
            process.stdin.write("stop\n")
            process.stdin.flush()
        elif process.poll() is None:
            process.terminate()

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=2)
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                lines.append(line.rstrip("\r\n"))

        log_path = root / "logs/latest.log"
        log_text = "\n".join(lines)
        if log_path.is_file():
            log_text += "\n" + log_path.read_text(encoding="utf-8", errors="replace")
        matched = [line for line in log_text.splitlines() if any(marker in line.lower() for marker in _ERROR_MARKERS)]
        success = ready and process.returncode == 0 and not matched
        retained_log = log_path if keep_directory is not None and log_path.exists() else None
        return ServerCheckResult(
            success=success,
            returncode=process.returncode,
            log_path=retained_log,
            matched_errors=matched[-50:],
            output_tail=lines[-80:],
        )
    finally:
        if context is not None:
            context.cleanup()
