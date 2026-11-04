from __future__ import annotations

import fcntl
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.adapters.local import DeployError, LocalCommandAdapter  # noqa: E402
from galadon.config import load_config  # noqa: E402
from galadon.ports import Artifact, DeployContext  # noqa: E402


class ProbeHandler(BaseHTTPRequestHandler):
    body = b'{"ok": true, "commit": "good123"}'

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args) -> None:
        return


class LocalAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-local-"))
        self.project = self.tmp / "demo"
        self.project.mkdir()
        self.dist = self.project / "dist"
        self.dist.mkdir()
        (self.dist / "server.py").write_text("print('ok')\n", encoding="utf-8")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.probe_url = f"http://127.0.0.1:{self.server.server_port}/health.json"
        self.config_path = self.project / "galadon.yaml"
        self.write_config()
        self.adapter = LocalCommandAdapter()
        self.ctx = DeployContext(
            root=self.project,
            config=load_config(self.project),
            env="test",
            commit="good123",
        )
        self.deploy_root = self.project / ".galadon" / "deploy"

    def tearDown(self) -> None:
        import shutil

        ProbeHandler.body = b'{"ok": true, "commit": "good123"}'
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, *, probe_expect: str = "") -> None:
        data = {
            "project": "demo",
            "template": "web",
            "role": "solo",
            "deploy": {
                "adapter": "local",
                "env": "test",
                "build": "",
                "migrate": "",
                "release": "",
                "probe_url": self.probe_url,
                "probe_expect": probe_expect,
                "artifact": "dist",
            },
            "checks": {"build": "", "test": "", "timeout": 30},
        }
        self.config_path.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

    def artifact(self) -> Artifact:
        return Artifact(path=self.dist / "server.py", digest="a" * 64)

    def test_release_creates_release_tree_and_switches_current_atomically(self) -> None:
        release = self.adapter.release(self.ctx, self.artifact())

        target = Path(release.metadata["release_dir"])
        self.assertTrue(target.is_dir())
        self.assertEqual((target / "server.py").read_text(encoding="utf-8"), "print('ok')\n")
        self.assertTrue(target.name.endswith("-good123"))
        current = self.deploy_root / "current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), target.resolve())
        self.assertFalse((self.deploy_root / "previous").exists())
        self.assertTrue((self.deploy_root / "shared" / "deploy.lock").is_file())

    def test_second_release_keeps_previous_and_rollback_reprobes_it(self) -> None:
        first = self.adapter.release(self.ctx, self.artifact())
        self.ctx.commit = "bad999"
        second = self.adapter.release(self.ctx, self.artifact())

        previous = self.deploy_root / "previous"
        self.assertEqual(previous.resolve(), Path(first.metadata["release_dir"]).resolve())
        ProbeHandler.body = b'{"ok": true, "commit": "good123"}'
        self.adapter.rollback(self.ctx, second)

        self.assertTrue(second.metadata["rollback"]["ok"])
        self.assertEqual(second.metadata["rollback"]["version"], "good123")
        self.assertEqual(second.metadata["rollback"]["actual"], "good123")
        self.assertEqual(
            (self.deploy_root / "current").resolve(),
            Path(first.metadata["release_dir"]).resolve(),
        )

    def test_release_refuses_while_another_deployment_holds_the_lock(self) -> None:
        lock_dir = self.deploy_root / "shared"
        lock_dir.mkdir(parents=True)
        lock = lock_dir / "deploy.lock"
        with lock.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(DeployError) as caught:
                    self.adapter.release(self.ctx, self.artifact())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertIn("another deployment holds", str(caught.exception))
        releases = self.deploy_root / "releases"
        self.assertFalse(releases.exists() and any(releases.iterdir()))

    def test_probe_rejects_a_commit_that_only_appears_in_history(self) -> None:
        self.write_config(probe_expect="6139370")
        self.ctx.config = load_config(self.project)
        self.ctx.commit = "6139370"
        release = self.adapter.release(self.ctx, self.artifact())
        ProbeHandler.body = b'{"commit":"0000000","history":["6139370"]}'

        result = self.adapter.probe(self.ctx, release)

        self.assertFalse(result.ok)
        self.assertEqual(result.actual, "0000000")
        self.assertIn("commit mismatch", result.detail)

    def test_probe_requires_exact_text_when_the_body_is_not_json(self) -> None:
        self.write_config(probe_expect="6139370")
        self.ctx.config = load_config(self.project)
        self.ctx.commit = "6139370"
        release = self.adapter.release(self.ctx, self.artifact())
        ProbeHandler.body = b"build 6139370 ready\n"

        result = self.adapter.probe(self.ctx, release)

        self.assertFalse(result.ok)
        self.assertIn("does not exactly match", result.detail)

    def test_probe_accepts_exact_text_that_looks_like_a_json_scalar(self) -> None:
        self.write_config(probe_expect="6139370")
        self.ctx.config = load_config(self.project)
        self.ctx.commit = "6139370"
        release = self.adapter.release(self.ctx, self.artifact())
        ProbeHandler.body = b"6139370\n"

        result = self.adapter.probe(self.ctx, release)

        self.assertTrue(result.ok)
        self.assertEqual(result.actual, "6139370")


if __name__ == "__main__":
    unittest.main()
