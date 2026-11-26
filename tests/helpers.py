"""Fixtures shared by the CLI-level tests: an in-process CLI runner, a
controllable local probe target and git shortcuts."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from galadon.cli import main  # noqa: E402

# What a non-`none` layout must ship, and the local slots it keeps out of git.
AGENT_SLOTS = ("plans", "evidence", "archive", "skills", "tmp", "wiki")
SPEC_TRUTH_FILES = (
    "spec/INDEX.md",
    "spec/domains/README.md",
    "spec/TESTING.md",
    "spec/CONSTITUTION.md",
)

IDENTITY = ("-c", "user.email=a@b", "-c", "user.name=a", "-c", "commit.gpgsign=false")


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *IDENTITY, *args],
        check=check,
        capture_output=True,
        text=True,
    )


def head(root: Path) -> str:
    return git(root, "rev-parse", "--short", "HEAD").stdout.strip()


def commit_all(root: Path, message: str = "wip") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return head(root)


def scaffold_repo(path: Path, *init_args: str) -> tuple[Path, str]:
    """`galadon init` into `path`, then an initial commit. Returns the project
    and the short hash of that commit."""
    code, out, err = run("init", "--path", str(path), *init_args)
    assert code == 0, out + err
    return path, commit_all(path, "init")


class ProbeServer:
    """A local probe target. It answers with `body`, or with JSON carrying
    `commit`, or with whatever `handler(request_count)` returns."""

    def __init__(self, commit: str = "abc1234", body: bytes | None = None, handler=None) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if outer.handler is not None:
                    status, text = outer.handler(outer.requests)
                    payload = text.encode()
                else:
                    status = 200
                    payload = outer.body or json.dumps({"ok": True, "commit": outer.commit}).encode()
                outer.requests += 1
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args) -> None:
                return

        self.commit = commit
        self.body = body
        self.handler = handler
        self.requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/health.json"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def set_probe(root: Path, url: str, expect: str, **deploy: object) -> None:
    """Point the project's local adapter at `url` without touching the rest of
    `galadon.yaml`."""
    import yaml

    path = root / "galadon.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["deploy"].update({"probe_url": url, "probe_expect": expect, **deploy})
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
