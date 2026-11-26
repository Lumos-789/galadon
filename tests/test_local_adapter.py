from __future__ import annotations

import fcntl
import itertools
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from galadon.adapters.local import DeployError, LocalCommandAdapter  # noqa: E402
from galadon.config import load_config  # noqa: E402
from galadon.ports import Artifact, DeployContext  # noqa: E402
from helpers import ProbeServer  # noqa: E402


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

    def write_config(self, *, probe_expect: str = "", options: dict | None = None) -> None:
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
                "options": options or {},
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

    def test_rollback_recheck_honors_a_static_probe_expect(self) -> None:
        self.write_config(probe_expect="v9.9.9")
        self.ctx.config = load_config(self.project)
        ProbeHandler.body = b'{"ok": true, "commit": "v9.9.9"}'
        self.adapter.release(self.ctx, self.artifact())
        self.ctx.commit = "bad999"
        second = self.adapter.release(self.ctx, self.artifact())

        self.adapter.rollback(self.ctx, second)

        self.assertTrue(second.metadata["rollback"]["ok"])
        self.assertEqual(second.metadata["rollback"]["actual"], "v9.9.9")

    def test_rollback_recheck_with_commit_template_asserts_the_version(self) -> None:
        self.write_config(probe_expect="{{COMMIT}}")
        self.ctx.config = load_config(self.project)
        self.adapter.release(self.ctx, self.artifact())
        self.ctx.commit = "bad999"
        second = self.adapter.release(self.ctx, self.artifact())

        ProbeHandler.body = b'{"ok": true, "commit": "good123"}'
        self.adapter.rollback(self.ctx, second)

        self.assertTrue(second.metadata["rollback"]["ok"])
        self.assertEqual(second.metadata["rollback"]["version"], "good123")
        self.assertEqual(second.metadata["rollback"]["actual"], "good123")

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

    def test_preflight_refuses_an_unusable_probe_before_anything_is_built(self) -> None:
        # It used to be discovered by the probe, with the new release live.
        self.write_config(probe_expect="")
        self.ctx.config = load_config(self.project)
        with self.assertRaises(DeployError) as caught:
            self.adapter.preflight(self.ctx)
        self.assertIn("probe_expect is required", str(caught.exception))

        self.config_path.write_text(
            self.config_path.read_text(encoding="utf-8").replace(self.probe_url, ""), encoding="utf-8"
        )
        self.ctx.config = load_config(self.project)
        with self.assertRaises(DeployError) as caught:
            self.adapter.preflight(self.ctx)
        self.assertIn("probe_url is required", str(caught.exception))
        self.assertFalse(self.deploy_root.exists())

    def test_a_healthy_probe_passes_preflight(self) -> None:
        self.write_config(probe_expect="v1")
        self.ctx.config = load_config(self.project)
        result = self.adapter.preflight(self.ctx)
        self.assertEqual(result["probe"], self.probe_url)
        self.assertEqual(result["notes"], [])

    def test_a_failed_first_release_leaves_nothing_live(self) -> None:
        # Nothing was live before the first release, so a rollback restores
        # exactly that instead of leaving the failed release on `current`.
        release = self.adapter.release(self.ctx, self.artifact())

        self.adapter.rollback(self.ctx, release)

        self.assertFalse((self.deploy_root / "current").is_symlink())
        self.assertEqual(
            release.metadata["rollback"],
            {"version": None, "target": None, "ok": True, "first_release": True},
        )
        self.assertTrue(Path(release.metadata["release_dir"]).is_dir(), "kept as the record")

    def _release_series(self, keep: int) -> list:
        self.write_config(options={"keep_releases": keep})
        self.ctx.config = load_config(self.project)
        minute = itertools.count(0)
        adapter = LocalCommandAdapter(
            clock=lambda: datetime(2026, 1, 1, 0, 0, next(minute), tzinfo=timezone.utc)
        )
        releases = []
        for commit in ("aaaa111", "bbbb222", "cccc333", "dddd444"):
            self.ctx.commit = commit
            releases.append(adapter.release(self.ctx, self.artifact()))
        return releases

    def test_old_releases_are_pruned_but_never_the_live_ones(self) -> None:
        (self.deploy_root / "releases").mkdir(parents=True)
        (self.deploy_root / "releases" / "not-ours").mkdir()
        (self.deploy_root / "releases" / "not-ours" / "data.txt").write_text("x", encoding="utf-8")

        releases = self._release_series(keep=2)

        names = sorted(path.name for path in (self.deploy_root / "releases").iterdir())
        self.assertEqual(len(names), 3)
        self.assertIn("not-ours", names, "a directory galadon did not name is never removed")
        self.assertTrue((self.deploy_root / "current").resolve().name.endswith("-dddd444"))
        self.assertTrue((self.deploy_root / "previous").resolve().name.endswith("-cccc333"))
        self.assertTrue(releases[0].metadata["release_dir"].endswith("-aaaa111"))
        # Each release prunes what is now beyond the limit, so the oldest go
        # first and earliest; nothing live is ever among them.
        pruned = sorted(sum((release.metadata["pruned"] for release in releases), []))
        self.assertEqual(
            pruned,
            sorted(
                [
                    Path(releases[0].metadata["release_dir"]).name,
                    Path(releases[1].metadata["release_dir"]).name,
                ]
            ),
        )
        for release in releases[:2]:
            self.assertFalse(Path(release.metadata["release_dir"]).exists())

    def test_keep_releases_zero_keeps_everything(self) -> None:
        releases = self._release_series(keep=0)
        self.assertEqual(len(list((self.deploy_root / "releases").iterdir())), 4)
        self.assertEqual(releases[-1].metadata["pruned"], [])

    def test_a_just_restarted_service_behind_a_gateway_is_waited_for(self) -> None:
        # The readiness window also covers the 502 a reverse proxy answers
        # while the upstream is still starting.
        def handler(requests: int) -> tuple[int, str]:
            return (502, "bad gateway") if requests < 2 else (200, '{"commit": "good123"}')

        gateway = ProbeServer(handler=handler)
        try:
            self.write_config(probe_expect="good123")
            self.config_path.write_text(
                self.config_path.read_text(encoding="utf-8").replace(self.probe_url, gateway.url),
                encoding="utf-8",
            )
            self.ctx.config = load_config(self.project)
            adapter = LocalCommandAdapter(probe_ready=1.0)
            result = adapter.probe(self.ctx, adapter.release(self.ctx, self.artifact()))
            self.assertTrue(result.ok, result.detail)
            self.assertEqual(gateway.requests, 3)
        finally:
            gateway.stop()


if __name__ == "__main__":
    unittest.main()
