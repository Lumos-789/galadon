from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.adapters.local import DeployContext, DeployError  # noqa: E402
from galadon.adapters.ssh import SshSystemdAdapter  # noqa: E402
from galadon.config import load_config  # noqa: E402
from galadon.ports import Artifact, ProbeResult, Release  # noqa: E402


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.ssh_output = "GALADON_OK=1\n"

    def __call__(self, argv, *, cwd=None, input=None, timeout=300):
        self.calls.append(
            {"argv": list(argv), "cwd": cwd, "input": input, "timeout": timeout}
        )
        if argv[0] == "rsync":
            return 0, "", ""
        if argv[0] == "ssh":
            return 0, self.ssh_output, ""
        return 0, "", ""


class ProbeHandler(BaseHTTPRequestHandler):
    commit = "abc1234"
    status = 200

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True, "commit": self.commit}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


class SshAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-ssh-"))
        self.project = self.tmp / "demo"
        self.project.mkdir()
        (self.project / "dist").mkdir()
        (self.project / "dist" / "server.py").write_text("print('ok')\n", encoding="utf-8")
        self.config_path = self.project / "galadon.yaml"
        self.write_config()
        self.config = load_config(self.project)
        self.runner = FakeRunner()
        self.adapter = SshSystemdAdapter(
            runner=self.runner,
            clock=lambda: datetime(2026, 11, 4, 17, 30, tzinfo=timezone.utc),
            commit_provider=lambda _root: "abc1234",
        )
        self.ctx = DeployContext(root=self.project, config=self.config, env="test")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, **overrides) -> None:
        options = {
            "host": "203.0.113.10",
            "user": "root",
            "remote_root": "/srv/demo",
            "service": "demo",
            "domain": "demo.example.com",
            "listen": "127.0.0.1:18790",
            "exec_start": "/usr/bin/python3 /srv/demo/current/server.py",
            "caddy_block": "demo-test",
        }
        options.update(overrides)
        data = {
            "project": "demo",
            "template": "web",
            "role": "solo",
            "deploy": {
                "adapter": "ssh",
                "env": "test",
                "build": "",
                "migrate": "",
                "release": "",
                "probe_url": "https://demo.example.com/health.json",
                "probe_expect": "",
                "artifact": "dist",
                "options": options,
            },
            "checks": {"build": "true", "test": "true", "timeout": 30},
        }
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def test_build_hashes_directory_artifact(self) -> None:
        artifact = self.adapter.build(self.ctx)
        self.assertEqual(artifact.path, (self.project / "dist").resolve())
        self.assertEqual(len(artifact.digest), 64)
        self.assertEqual(self.runner.calls, [])

    def test_preflight_runs_remote_guards(self) -> None:
        result = self.adapter.preflight(self.ctx)
        self.assertEqual(result["target"], "root@203.0.113.10")
        script = self.runner.calls[0]["input"]
        self.assertIn("systemctl cat", script)
        self.assertIn("flock", script)
        self.assertIn('test -w "$root"', script)

    def test_release_uses_atomic_links_and_lock(self) -> None:
        self.runner.ssh_output = (
            "GALADON_PREVIOUS=/srv/demo/releases/20261104-160000-prev123\n"
            "GALADON_CURRENT=/srv/demo/releases/20261104-173000-abc1234\n"
            "GALADON_OK=1\n"
        )
        release = self.adapter.release(self.ctx, self.adapter.build(self.ctx))
        self.assertEqual(release.metadata["commit"], "abc1234")
        self.assertEqual(
            release.metadata["release_dir"],
            "/srv/demo/releases/20261104-173000-abc1234",
        )
        rsync = next(call for call in self.runner.calls if call["argv"][0] == "rsync")
        self.assertIn("root@203.0.113.10:/srv/demo/releases/20261104-173000-abc1234/", rsync["argv"])
        self.assertFalse(
            [arg for arg in rsync["argv"] if arg.startswith("--chmod")],
            "rsync flags must stay compatible with the macOS 2.6.9 client",
        )
        finalize = next(
            call
            for call in self.runner.calls
            if call["argv"][0] == "ssh" and "mv -Tf" in (call["input"] or "")
        )
        self.assertIn("flock -n 9", finalize["input"])
        self.assertIn('mv -Tf "$root/.current.new" "$root/current"', finalize["input"])
        self.assertIn('mv -Tf "$root/.previous.new" "$root/previous"', finalize["input"])

    def test_release_prefers_the_context_commit(self) -> None:
        self.ctx.commit = "ctx5678"
        release = self.adapter.release(self.ctx, self.adapter.build(self.ctx))
        self.assertEqual(release.metadata["commit"], "ctx5678")
        self.assertIn("ctx5678", release.metadata["release_dir"])

    def test_probe_requires_matching_json_commit(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health.json"
            self.write_config()
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            data["deploy"]["probe_url"] = url
            self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            config = load_config(self.project)
            ctx = DeployContext(root=self.project, config=config, env="test")
            artifact = Artifact(path=self.project / "dist", digest="digest")
            release = Release(env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"})
            self.assertTrue(self.adapter.probe(ctx, release).ok)

            ProbeHandler.commit = "def5678"
            with self.assertRaises(DeployError) as caught:
                self.adapter.probe(ctx, release)
            self.assertIn("commit mismatch", str(caught.exception))
        finally:
            ProbeHandler.commit = "abc1234"
            server.shutdown()
            server.server_close()

    def test_probe_waits_out_a_just_restarted_service(self) -> None:
        self.write_config()
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        data["deploy"]["probe_url"] = "http://127.0.0.1:1/health.json"
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        config = load_config(self.project)
        ctx = DeployContext(root=self.project, config=config, env="test")
        artifact = Artifact(path=self.project / "dist", digest="digest")
        release = Release(
            env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"}
        )
        adapter = SshSystemdAdapter(
            runner=self.runner,
            clock=lambda: datetime(2026, 11, 4, 17, 30, tzinfo=timezone.utc),
            commit_provider=lambda _root: "abc1234",
            sleep=lambda _seconds: None,
            probe_ready=2.0,
        )
        attempts = {"n": 0}

        def fake_urlopen(url: str, timeout: float | None = None) -> object:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionResetError(54, "Connection reset by peer")

            class Response:
                def getcode(self) -> int:
                    return 200

                def read(self, _size: int = -1) -> bytes:
                    return b'{"commit":"abc1234"}'

                def __enter__(self) -> "Response":
                    return self

                def __exit__(self, *_exc: object) -> bool:
                    return False

            return Response()

        with mock.patch("galadon.adapters.probe.urllib.request.urlopen", fake_urlopen):
            probe = adapter.probe(ctx, release)

        self.assertTrue(probe.ok)
        self.assertEqual(probe.actual, "abc1234")
        self.assertEqual(attempts["n"], 2)

    def test_probe_still_fails_loudly_when_the_window_expires(self) -> None:
        self.write_config()
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        data["deploy"]["probe_url"] = "http://127.0.0.1:1/health.json"
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        config = load_config(self.project)
        ctx = DeployContext(root=self.project, config=config, env="test")
        adapter = SshSystemdAdapter(
            runner=self.runner,
            clock=lambda: datetime(2026, 11, 4, 17, 30, tzinfo=timezone.utc),
            commit_provider=lambda _root: "abc1234",
            sleep=lambda _seconds: None,
            probe_ready=0.0,
        )
        artifact = Artifact(path=self.project / "dist", digest="digest")
        release = Release(
            env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"}
        )

        with mock.patch(
            "galadon.adapters.probe.urllib.request.urlopen",
            side_effect=ConnectionResetError(54, "Connection reset by peer"),
        ):
            with self.assertRaises(DeployError) as caught:
                adapter.probe(ctx, release)

        self.assertIn("probe request failed", str(caught.exception))
        self.assertIn("no answer within", str(caught.exception))

    def test_rollback_switches_previous_and_reprobes(self) -> None:
        self.runner.ssh_output = (
            "GALADON_ROLLBACK_TO=/srv/demo/releases/20261104-160000-prev123\n"
            "GALADON_OK=1\n"
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health.json"
            self.write_config()
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            data["deploy"]["probe_url"] = url
            self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            config = load_config(self.project)
            ctx = DeployContext(root=self.project, config=config, env="test")
            artifact = Artifact(path=self.project / "dist", digest="digest")
            release = Release(env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"})
            ProbeHandler.commit = "prev123"
            self.adapter.rollback(ctx, release)
            self.assertTrue(release.metadata["rollback"]["ok"])
            self.assertEqual(release.metadata["rollback"]["version"], "prev123")
            script = next(
                call["input"] for call in self.runner.calls if "GALADON_ROLLBACK_TO" in (call["input"] or "")
            )
            self.assertIn('ln -sfn "$target" "$root/.current.rollback"', script)
            self.assertIn('mv -Tf "$root/.current.rollback" "$root/current"', script)
        finally:
            ProbeHandler.commit = "abc1234"
            server.shutdown()
            server.server_close()

    def test_rollback_recheck_ignores_probe_expect(self) -> None:
        self.runner.ssh_output = (
            "GALADON_ROLLBACK_TO=/srv/demo/releases/20261104-160000-prev123\n"
            "GALADON_OK=1\n"
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health.json"
            self.write_config()
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            data["deploy"]["probe_url"] = url
            data["deploy"]["probe_expect"] = "only-the-failed-build-emits-this"
            self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            config = load_config(self.project)
            ctx = DeployContext(root=self.project, config=config, env="test")
            artifact = Artifact(path=self.project / "dist", digest="digest")
            release = Release(env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"})
            ProbeHandler.commit = "prev123"
            self.adapter.rollback(ctx, release)
            self.assertTrue(release.metadata["rollback"]["ok"])
        finally:
            ProbeHandler.commit = "abc1234"
            server.shutdown()
            server.server_close()

    def test_bootstrap_script_is_idempotent_and_guards_caddy(self) -> None:
        self.runner.ssh_output = (
            "GALADON_USER_CREATED=0\n"
            "GALADON_UNIT_CHANGED=1\n"
            "GALADON_CADDY_CHANGED=1\n"
            "GALADON_CADDY_BACKUP=/etc/caddy/Caddyfile.galadon-bak.1\n"
            "GALADON_OK=1\n"
        )
        result = self.adapter.bootstrap(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertEqual(result["service"], "demo")
        self.assertTrue(result["caddy"]["changed"])
        self.assertIn("groupadd --system", script)
        self.assertIn('useradd --system --gid "$run_user"', script)
        self.assertIn('cmp -s "$unit_tmp" "$unit_path"', script)
        self.assertIn('begin="# galadon:begin $marker"', script)
        self.assertIn("caddy validate", script)
        self.assertIn("previous config restored and caddy was not reloaded", script)
        self.assertIn("systemctl reload caddy", script)

    def _caddy_segment(self, caddyfile: Path) -> str:
        script = self.runner.calls[0]["input"]
        start = script.index("block_file=$(mktemp)")
        end = script.index("printf 'GALADON_USER_CREATED", start)
        segment = script[start:end]
        return (
            "set -euo pipefail\n"
            "marker=demo-test\n"
            f"caddyfile={shlex.quote(str(caddyfile))}\n"
            + segment
            + "\nprintf 'GALADON_CADDY_CHANGED=%s\\n' \"$caddy_changed\"\n"
            + "printf 'GALADON_CADDY_BACKUP=%s\\n' \"$backup\"\n"
        )

    def _run_segment(self, segment: str, caddy_exit: int = 0) -> tuple[subprocess.CompletedProcess, Path]:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        marker = self.tmp / "systemctl-called"
        for name, body in {
            "caddy": f"#!/bin/sh\nexit {caddy_exit}\n",
            "systemctl": f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(marker))}\nexit 0\n",
        }.items():
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        proc = subprocess.run(
            ["bash", "-s"],
            input=segment,
            text=True,
            capture_output=True,
            cwd=self.tmp,
            env=env,
        )
        return proc, marker

    def test_caddy_marker_replacement_is_idempotent(self) -> None:
        self.adapter.bootstrap(self.ctx)
        caddyfile = self.tmp / "Caddyfile"
        caddyfile.write_text(
            "other.example {\n    respond ok\n}\n\n"
            "# galadon:begin demo-test\n"
            "old.example {\n}\n"
            "# galadon:end demo-test\n",
            encoding="utf-8",
        )
        segment = self._caddy_segment(caddyfile)
        first, _ = self._run_segment(segment)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("GALADON_CADDY_CHANGED=1", first.stdout)
        text = caddyfile.read_text(encoding="utf-8")
        self.assertNotIn("old.example", text)
        self.assertIn("other.example", text)
        self.assertEqual(text.count("# galadon:begin demo-test"), 1)

        second, _ = self._run_segment(segment)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("GALADON_CADDY_CHANGED=0", second.stdout)
        self.assertEqual(
            caddyfile.read_text(encoding="utf-8").count("# galadon:begin demo-test"),
            1,
        )

    def test_caddy_validate_failure_restores_without_reload(self) -> None:
        self.adapter.bootstrap(self.ctx)
        caddyfile = self.tmp / "Caddyfile"
        original = "other.example {\n    respond ok\n}\n"
        caddyfile.write_text(original, encoding="utf-8")
        segment = self._caddy_segment(caddyfile)
        proc, systemctl_log = self._run_segment(segment, caddy_exit=1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(caddyfile.read_text(encoding="utf-8"), original)
        self.assertFalse(systemctl_log.exists())

    def test_teardown_removes_service_marker_and_root(self) -> None:
        self.runner.ssh_output = (
            "GALADON_SERVICE_REMOVED=1\n"
            "GALADON_CADDY_CHANGED=1\n"
            "GALADON_CADDY_BACKUP=/etc/caddy/Caddyfile.galadon-bak.2\n"
            "GALADON_ROOT_REMOVED=1\n"
            "GALADON_OK=1\n"
        )
        result = self.adapter.teardown(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertTrue(result["service_removed"])
        self.assertTrue(result["root_removed"])
        self.assertIn('systemctl disable --now "$service"', script)
        self.assertIn('rm -rf "$root"', script)
        self.assertIn("caddy validate", script)
        self.assertIn('if [ ! -f "$marker_file" ]; then', script)
        self.assertLess(script.index('if [ ! -f "$marker_file" ]; then'), script.index('rm -rf "$root"'))
        self.assertIn('userdel "$run_user"', script)

    def test_preflight_detects_systemd_unit_drift(self) -> None:
        self.runner.ssh_output = "GALADON_OK=1\n"
        result = self.adapter.preflight(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertIn("systemd unit drift detected", script)
        self.assertIn("ExecStart=/usr/bin/python3 /srv/demo/current/server.py", script)
        self.assertIn("galadon bootstrap --env", script)
        self.assertEqual(result["notes"], [])

    def test_preflight_names_the_skip_when_bootstrap_options_are_missing(self) -> None:
        self.write_config(domain="", listen="", exec_start="", caddy_block="")
        self.ctx = DeployContext(
            root=self.project, config=load_config(self.project), env="test"
        )
        self.runner.ssh_output = "GALADON_OK=1\n"
        result = self.adapter.preflight(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertNotIn("unit drift detected", script)
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn(
            "skipping unit drift check: unit template incomplete", result["notes"][0]
        )
        self.assertIn("domain, listen, exec_start, caddy_block", result["notes"][0])

    def test_bootstrap_refuses_a_non_empty_root_it_does_not_own(self) -> None:
        self.runner.ssh_output = "GALADON_OK=1\n"
        self.adapter.bootstrap(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertIn('marker_file="$root/.galadon-root"', script)
        self.assertIn("refusing to bootstrap", script)
        self.assertIn("directory is not empty and carries no .galadon-root", script)
        self.assertIn("it belongs to marker $existing", script)
        # the ownership check must decide before anything is mutated
        self.assertLess(
            script.index("refusing to bootstrap"),
            script.index('useradd --system'),
        )
        self.assertLess(
            script.index("refusing to bootstrap"),
            script.index('printf \'%s\\n\' "$marker" > "$root/.galadon-root"'),
        )

    def test_teardown_leaves_a_shared_run_user_in_place(self) -> None:
        self.runner.ssh_output = (
            "GALADON_SERVICE_REMOVED=1\n"
            "GALADON_ROOT_REMOVED=1\n"
            "GALADON_USER_REMOVED=0\n"
            "GALADON_USER_SKIPPED=still in use by another service\n"
            "GALADON_OK=1\n"
        )
        result = self.adapter.teardown(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertFalse(result["user_removed"])
        self.assertEqual(result["user_skip_reason"], "still in use by another service")
        self.assertIn('elif pgrep -u "$run_user"', script)
        self.assertIn("left $run_user in place", script)

    def test_bootstrap_writes_root_marker(self) -> None:
        self.runner.ssh_output = "GALADON_OK=1\n"
        self.adapter.bootstrap(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertIn('printf \'%s\\n\' "$marker" > "$root/.galadon-root"', script)

    def test_preflight_refuses_an_unusable_probe_before_touching_the_host(self) -> None:
        self.write_config()
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        data["deploy"]["probe_url"] = ""
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.ctx = DeployContext(
            root=self.project, config=load_config(self.project), env="test"
        )

        with self.assertRaises(DeployError) as caught:
            self.adapter.preflight(self.ctx)

        self.assertIn("probe_url is required", str(caught.exception))
        self.assertEqual(self.runner.calls, [], "nothing may reach the host first")

    def test_a_first_release_rollback_stops_the_service_and_reprobes_nothing(self) -> None:
        self.runner.ssh_output = "GALADON_FIRST_RELEASE=1\nGALADON_OK=1\n"
        artifact = Artifact(path=self.project / "dist", digest="digest")
        release = Release(env="test", artifact=artifact, adapter="ssh", metadata={"commit": "abc1234"})

        self.adapter.rollback(self.ctx, release)

        self.assertEqual(
            release.metadata["rollback"],
            {
                "version": None,
                "target": None,
                "ok": True,
                "first_release": True,
                "service_stopped": True,
            },
        )
        script = self.runner.calls[0]["input"]
        self.assertIn('rm -f "$root/current"', script)
        self.assertIn('systemctl stop "$service"', script)
        self.assertNotIn("GALADON_ROLLBACK_TO", script[: script.index("GALADON_FIRST_RELEASE")])

    def test_release_migrates_on_the_host_before_the_switch_and_prunes_after_it(self) -> None:
        self.write_config(
            remote_migrate="python -m migrate",
            keep_releases=2,
            environment_file="/srv/demo/shared/env",
        )
        self.ctx = DeployContext(
            root=self.project, config=load_config(self.project), env="test"
        )
        self.runner.ssh_output = (
            "GALADON_PREVIOUS=\n"
            "GALADON_PRUNED= 20261101-000000-old1 20261101-000100-old2\n"
            "GALADON_OK=1\n"
        )

        release = self.adapter.release(self.ctx, self.adapter.build(self.ctx))

        self.assertEqual(
            release.metadata["pruned"], ["20261101-000000-old1", "20261101-000100-old2"]
        )
        script = next(
            call["input"] for call in self.runner.calls if "mv -Tf" in (call["input"] or "")
        )
        self.assertIn("remote_migrate='python -m migrate'", script)
        self.assertIn("keep_releases=2", script)
        switch = 'mv -Tf "$root/.current.new" "$root/current"'
        self.assertLess(script.index('eval "$remote_migrate"'), script.index(switch))
        self.assertLess(script.index(switch), script.index("printf 'GALADON_PRUNED"))
        self.assertIn("current was not switched", script)

    def test_bootstrap_keeps_a_site_out_of_search_engines_until_it_opts_in(self) -> None:
        self.runner.ssh_output = "GALADON_OK=1\n"
        self.adapter.bootstrap(self.ctx)
        script = self.runner.calls[0]["input"]
        self.assertIn('header X-Robots-Tag "noindex, nofollow"', script)
        self.assertIn("Disallow: /", script)
        self.assertNotIn("EnvironmentFile=", script)

        self.write_config(environment_file="/srv/demo/shared/env", search_index=True)
        self.ctx = DeployContext(
            root=self.project, config=load_config(self.project), env="test"
        )
        self.adapter.bootstrap(self.ctx)
        script = self.runner.calls[-1]["input"]
        self.assertNotIn("noindex", script)
        self.assertNotIn("Disallow", script)
        self.assertIn("reverse_proxy 127.0.0.1:18790", script)
        self.assertIn("EnvironmentFile=/srv/demo/shared/env", script)


if __name__ == "__main__":
    unittest.main()
