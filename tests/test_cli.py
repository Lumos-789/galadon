from __future__ import annotations

import fcntl
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.cli import main  # noqa: E402


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Handler(BaseHTTPRequestHandler):
    body = b'{"ok": true, "commit": "abc1234"}'

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args) -> None:
        return


class GaladonCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-test-"))
        self.project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(self.project), "--name", "demo")
        self.assertEqual(code, 0, out + err)
        self.enable_checks()

    def tearDown(self) -> None:
        import shutil

        Handler.body = b'{"ok": true, "commit": "abc1234"}'
        shutil.rmtree(self.tmp, ignore_errors=True)

    def enable_checks(self) -> None:
        data = self.config()
        data["checks"]["build"] = "true"
        data["checks"]["test"] = "true"
        self.set_config(data)

    def write_spec(self) -> None:
        (self.project / "spec" / "spec.md").write_text(
            "# demo spec\n\n"
            "## Problem\n\nUsers need a reliable demo service.\n\n"
            "## Acceptance\n\n- [ ] GET /health returns 200\n\n"
            "## Non-goals\n\nNo authentication in this iteration.\n",
            encoding="utf-8",
        )

    def write_retro(self) -> None:
        (self.project / "wiki" / "retro.md").write_text(
            "# demo retro\n\n"
            "## What changed\n\nShipped the demo service.\n\n"
            "## What surprised us\n\nNothing material.\n\n"
            "## Next\n\nAdd authentication.\n",
            encoding="utf-8",
        )

    def config(self) -> dict:
        return yaml.safe_load((self.project / "galadon.yaml").read_text(encoding="utf-8"))

    def set_config(self, data: dict) -> None:
        (self.project / "galadon.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def probe_server(self) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def probe_config(self, server: ThreadingHTTPServer, expect: str) -> None:
        data = self.config()
        data["deploy"]["probe_url"] = f"http://127.0.0.1:{server.server_port}/health.json"
        data["deploy"]["probe_expect"] = expect
        self.set_config(data)

    def release_record(self) -> dict:
        return json.loads(
            (self.project / "release" / "release.json").read_text(encoding="utf-8")
        )

    def test_full_loop_with_local_probe(self) -> None:
        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["phase"], "spec")

        self.write_spec()
        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(json.loads(out)["phase"], "build")

        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["ok"], True)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = self.config()
            data["deploy"]["probe_url"] = "%s://%s:%s/health" % (
                "http",
                "127.0.0.1",
                server.server_port,
            )
            data["deploy"]["probe_expect"] = "abc1234"
            self.set_config(data)

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = json.loads((self.project / "release" / "release.json").read_text(encoding="utf-8"))
            self.assertTrue(release["ok"])
            self.assertEqual(release["probe"]["ok"], True)
            self.assertEqual(release["actual_commit"], "abc1234")
        finally:
            server.shutdown()
            server.server_close()

        self.write_retro()
        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["ok"], True)

        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(json.loads(out)["phase"], "done")

    def test_local_ship_hashes_directory_artifact(self) -> None:
        self.write_spec()
        dist = self.project / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<h1>demo</h1>\n", encoding="utf-8")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = self.config()
            data["deploy"]["artifact"] = "dist"
            data["deploy"]["probe_url"] = "http://127.0.0.1:%s/health" % server.server_port
            data["deploy"]["probe_expect"] = "abc1234"
            self.set_config(data)

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = json.loads(
                (self.project / "release" / "release.json").read_text(encoding="utf-8")
            )
            self.assertTrue(release["ok"])
            self.assertEqual(release["artifact"]["path"], "dist")
            self.assertEqual(len(release["artifact"]["digest"]), 64)
        finally:
            server.shutdown()
            server.server_close()

    def test_invalid_yaml_fails_loud_without_traceback(self) -> None:
        (self.project / "galadon.yaml").write_text("deploy: [\n", encoding="utf-8")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("galadon.yaml is not valid YAML", err)
        self.assertNotIn("Traceback", err)

    def test_local_ship_requires_probe_expect(self) -> None:
        self.write_spec()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = self.config()
            data["deploy"]["probe_url"] = "http://127.0.0.1:%s/health" % server.server_port
            data["deploy"]["probe_expect"] = ""
            self.set_config(data)

            code, _out, _err = run("ship", "--path", str(self.project), "--env", "test")
            self.assertEqual(code, 1)
            release = json.loads(
                (self.project / "release" / "release.json").read_text(encoding="utf-8")
            )
            self.assertFalse(release["ok"])
            self.assertIn("probe_expect is required for the local adapter", release["probe"]["detail"])
        finally:
            server.shutdown()
            server.server_close()

    def test_fresh_project_check_fails_until_checks_are_declared(self) -> None:
        fresh = self.tmp / "fresh"
        code, out, err = run("init", "--path", str(fresh), "--name", "fresh")
        self.assertEqual(code, 0, out + err)
        (fresh / "spec" / "spec.md").write_text(
            "# fresh spec\n\n"
            "## Problem\n\nA fresh project needs a real gate.\n\n"
            "## Acceptance\n\n- [ ] the gate is real\n\n"
            "## Non-goals\n\nNo deploy yet.\n",
            encoding="utf-8",
        )
        data = yaml.safe_load((fresh / "galadon.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["checks"]["build"], "")
        self.assertEqual(data["checks"]["test"], "")

        code, out, err = run("check", "--path", str(fresh), "--json")
        self.assertEqual(code, 1, out + err)
        report = json.loads(out)
        self.assertFalse(report["ok"])
        messages = [item["stdout"] for item in report["results"]]
        self.assertIn("no checks configured — declare checks.build/checks.test", messages)
        code, out, _ = run("next", "--path", str(fresh), "--json")
        self.assertEqual(json.loads(out)["phase"], "verify")

    def test_ship_refuses_when_local_deploy_lock_is_held(self) -> None:
        self.write_spec()
        server = self.probe_server()
        self.probe_config(server, "abc1234")
        lock_path = self.project / ".galadon" / "deploy" / "shared" / "deploy.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code, out, err = run("ship", "--path", str(self.project), "--env", "test")
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertEqual(code, 2, out + err)
        self.assertIn("another deployment holds", err)
        releases = self.project / ".galadon" / "deploy" / "releases"
        self.assertFalse(releases.exists() and any(releases.iterdir()))

    def test_local_ship_rolls_back_to_previous_release_on_failed_probe(self) -> None:
        from unittest import mock

        self.write_spec()
        Handler.body = b'{"ok": true, "commit": "good123"}'
        server = self.probe_server()
        self.probe_config(server, "{{COMMIT}}")

        with mock.patch("galadon.cli.git_commit", side_effect=["good123", "bad999"]):
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            current = self.project / ".galadon" / "deploy" / "current"
            first_target = current.resolve()

            self.write_retro()
            code, out, err = run("next", "--path", str(self.project), "--json")
            self.assertEqual(json.loads(out)["phase"], "done", out + err)
            code, out, err = run("new", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)
            code, out, err = run("check", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

        self.assertEqual(code, 1, out + err)
        self.assertIn("release good123 is live again", err)
        release = self.release_record()
        self.assertFalse(release["ok"])
        self.assertEqual(release["commit"], "bad999")
        self.assertEqual(release["actual_commit"], "good123")
        self.assertTrue(release["rollback"]["ok"])
        self.assertEqual(release["rollback"]["version"], "good123")
        self.assertEqual(current.resolve(), first_target)

    def test_actual_commit_comes_from_the_probe_not_build_metadata(self) -> None:
        from unittest import mock

        self.write_spec()
        Handler.body = b'{"ok": true, "commit": "abc1234"}'
        server = self.probe_server()
        self.probe_config(server, "abc1234")

        with mock.patch("galadon.cli.git_commit", return_value="build999"):
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

        self.assertEqual(code, 0, out + err)
        release = self.release_record()
        self.assertEqual(release["commit"], "build999")
        self.assertEqual(release["probe"]["actual"], "abc1234")
        self.assertEqual(release["actual_commit"], "abc1234")

    def test_local_probe_rejects_commit_that_only_appears_in_history(self) -> None:
        from unittest import mock

        self.write_spec()
        Handler.body = b'{"commit":"0000000","history":["6139370"]}'
        server = self.probe_server()
        self.probe_config(server, "6139370")

        with mock.patch("galadon.cli.git_commit", return_value="6139370"):
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

        self.assertEqual(code, 1, out + err)
        release = self.release_record()
        self.assertFalse(release["probe"]["ok"])
        self.assertEqual(release["probe"]["actual"], "0000000")
        self.assertIn("commit mismatch", release["probe"]["detail"])
        self.assertEqual(release["actual_commit"], "0000000")
        self.assertNotEqual(release["actual_commit"], "6139370")

    def test_new_archives_finished_loop_and_returns_to_build(self) -> None:
        self.write_spec()
        self.write_retro()
        (self.project / ".galadon" / "checks.json").write_text('{"ok": true}', encoding="utf-8")
        (self.project / "release" / "release.json").write_text('{"ok": true}', encoding="utf-8")
        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(json.loads(out)["phase"], "done")

        code, out, err = run("new", "--path", str(self.project), "--json")
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(len(payload["archived"]), 2)
        self.assertEqual(payload["next"]["phase"], "build")
        self.assertFalse((self.project / "release" / "release.json").exists())
        self.assertTrue((self.project / ".galadon" / "history").is_dir())

    def test_new_refuses_before_the_loop_is_done(self) -> None:
        self.write_spec()
        code, _, err = run("new", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("still in build", err)

    def test_check_failure_returns_nonzero_and_keeps_verify_phase(self) -> None:
        self.write_spec()
        data = self.config()
        data["checks"]["test"] = "false"
        self.set_config(data)

        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 1, out + err)
        self.assertFalse(json.loads(out)["ok"])
        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(json.loads(out)["phase"], "verify")

    def test_prod_requires_explicit_confirmation(self) -> None:
        self.write_spec()
        code, out, err = run("ship", "--path", str(self.project), "--env", "prod")
        self.assertEqual(code, 2, out + err)
        self.assertIn("--yes-prod", err)

    def test_prod_dry_run_previews_without_the_confirmation_flag(self) -> None:
        self.write_spec()
        code, out, err = run(
            "ship", "--path", str(self.project), "--env", "prod", "--dry-run", "--json"
        )
        self.assertEqual(code, 0, out + err)
        plan = json.loads(out)
        self.assertEqual(plan["env"], "prod")
        self.assertEqual(plan["requires"], "--yes-prod")

    def test_unimplemented_adapter_fails_loud(self) -> None:
        self.write_spec()
        data = self.config()
        data["deploy"]["adapter"] = "k8s"
        self.set_config(data)
        code, out, err = run("ship", "--path", str(self.project), "--env", "test")
        self.assertEqual(code, 2, out + err)
        self.assertIn("no implementation", err)

    def test_all_profiles_and_role_presets_render(self) -> None:
        for profile in ("web", "api", "static"):
            for role in ("solo", "six-ring", "orchestrator"):
                target = self.tmp / f"{profile}-{role}"
                code, out, err = run(
                    "init",
                    "--path",
                    str(target),
                    "--name",
                    target.name,
                    "--template",
                    profile,
                    "--role",
                    role,
                )
                self.assertEqual(code, 0, out + err)
                self.assertTrue((target / "docs" / "profile.md").is_file())
                self.assertTrue((target / "spec" / "spec.md").is_file())
                text = (target / "galadon.yaml").read_text(encoding="utf-8")
                self.assertIn(f"template: {profile}", text)
                self.assertIn(f"role: {role}", text)

    def test_dry_run_does_not_write_release(self) -> None:
        self.write_spec()
        code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--dry-run")
        self.assertEqual(code, 0, out + err)
        self.assertIn("adapter: local", out)
        self.assertFalse((self.project / "release" / "release.json").exists())

    def test_ship_renders_commit_placeholder_in_probe_expect(self) -> None:
        self.write_spec()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = Handler.body
        try:
            Handler.body = b'{"ok": true, "commit": "abc1234"}'
            data = self.config()
            data["deploy"]["probe_url"] = f"http://127.0.0.1:{server.server_port}/health.json"
            data["deploy"]["probe_expect"] = "{{COMMIT}}"
            self.set_config(data)
            from unittest import mock

            with mock.patch("galadon.cli.git_commit", return_value="abc1234"):
                code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = json.loads((self.project / "release" / "release.json").read_text(encoding="utf-8"))
            self.assertTrue(release["ok"])
            self.assertEqual(release["commit"], "abc1234")
        finally:
            Handler.body = original
            server.shutdown()
            server.server_close()

    def test_teardown_requires_yes(self) -> None:
        code, out, err = run("teardown", "--path", str(self.project), "--env", "test")
        self.assertEqual(code, 2, out + err)
        self.assertIn("--yes", err)

    def test_bootstrap_and_teardown_forward_to_adapter(self) -> None:
        data = self.config()
        data["deploy"]["adapter"] = "ssh"
        data["deploy"]["artifact"] = "dist"
        data["deploy"]["options"] = {
            "host": "203.0.113.10",
            "user": "root",
            "remote_root": "/srv/demo",
            "service": "demo",
            "domain": "demo.example.com",
            "listen": "127.0.0.1:18790",
            "exec_start": "/usr/bin/python3 /srv/demo/current/app.py",
            "caddy_block": "demo-test",
        }
        self.set_config(data)

        class FakeAdapter:
            name = "ssh"
            calls: list[str] = []

            def describe(self, ctx):
                return "root@203.0.113.10:/srv/demo"

            def bootstrap(self, ctx):
                self.calls.append("bootstrap")
                return {
                    "target": "root@203.0.113.10",
                    "service": "demo",
                    "caddy": {"changed": False},
                }

            def teardown(self, ctx):
                self.calls.append("teardown")
                return {
                    "target": "root@203.0.113.10",
                    "service_removed": True,
                    "root_removed": True,
                }

        adapter = FakeAdapter()
        from unittest import mock

        with mock.patch("galadon.cli.adapter_for", return_value=adapter):
            code, out, err = run("bootstrap", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(json.loads(out)["service"], "demo")
            code, out, err = run(
                "teardown", "--path", str(self.project), "--env", "test", "--yes", "--json"
            )
            self.assertEqual(code, 0, out + err)
            self.assertTrue(json.loads(out)["root_removed"])
        self.assertEqual(adapter.calls, ["bootstrap", "teardown"])

    def test_init_refuses_non_empty_directory(self) -> None:
        code, out, err = run("init", "--path", str(self.project), "--name", "again")
        self.assertEqual(code, 2, out + err)
        self.assertIn("not empty", err)


if __name__ == "__main__":
    unittest.main()
