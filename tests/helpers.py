from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def make_pack(root: Path, pack_format: int | list[int] = 61, *, description: str = "test") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pack: dict[str, Any] = {"description": description}
    if isinstance(pack_format, int) and pack_format < 82:
        pack["pack_format"] = pack_format
    else:
        value = pack_format if isinstance(pack_format, list) else [pack_format, 0]
        pack["min_format"] = value
        pack["max_format"] = value
    (root / "pack.mcmeta").write_text(json.dumps({"pack": pack}, indent=2) + "\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    return root


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _RepoHandler(BaseHTTPRequestHandler):
    root: Path

    def do_GET(self) -> None:
        target = self.root / self.path.lstrip("/")
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if target.suffix == ".json" else "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def repo_server(root: Path) -> Iterator[str]:
    """Serve a directory tree over local HTTP; yields the base URL."""

    handler = type("Handler", (_RepoHandler,), {"root": root})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
