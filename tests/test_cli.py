from __future__ import annotations

import fcntl
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ProbeServer, commit_all, git, head, run, set_probe  # noqa: E402


class GaladonCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(self.project), "--name", "demo")
        self.assertEqual(code, 0, out + err)
        data = self.config()
        data["checks"]["build"] = "true"
        data["checks"]["test"] = "true"
        self.set_config(data)

    def write_spec(self, checked: bool = True) -> None:
        box = "- [x]" if checked else "- [ ]"
        (self.project / "spec" / "spec.md").write_text(
            "# demo spec\n\n"
            "## Problem\n\nUsers need a reliable demo service.\n\n"
            f"## Acceptance\n\n{box} GET /health returns 200\n\n"
            "## Non-goals\n\nNo authentication in this iteration.\n",
            encoding="utf-8",
        )

    def write_retro(self) -> None:
        (self.project / "wiki" / "retro.md").write_text(
            "# demo retro\n\n## What changed\n\nShipped the demo service.\n\n"
            "## What surprised us\n\nNothing material.\n\n## Next\n\nAdd authentication.\n",
            encoding="utf-8",
        )

    def config(self) -> dict:
        return yaml.safe_load((self.project / "galadon.yaml").read_text(encoding="utf-8"))

    def set_config(self, data: dict) -> None:
        (self.project / "galadon.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    def repo(self) -> str:
        """Turn the project into a git repository; returns the short commit."""
        git(self.project, "init")
        return commit_all(self.project, "init")

    def release_record(self) -> dict:
        return json.loads((self.project / "release" / "release.json").read_text(encoding="utf-8"))

    def phase(self) -> str:
        code, out, err = run("next", "--path", str(self.project), "--json")
        assert code == 0, err
        return json.loads(out)["phase"]

    def test_full_loop_with_local_probe(self) -> None:
        code, out, _ = run("next", "--path", str(self.project), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["phase"], "spec")

        self.write_spec()
        self.assertEqual(self.phase(), "build")

        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["ok"], True)

        server = ProbeServer(commit="abc1234")
        try:
            set_probe(self.project, server.url, "abc1234")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = self.release_record()
            self.assertTrue(release["ok"])
            self.assertEqual(release["probe"]["ok"], True)
            self.assertEqual(release["actual_commit"], "abc1234")
        finally:
            server.stop()

        self.write_retro()
        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["ok"], True)
        self.assertEqual(self.phase(), "done")

    def test_an_untouched_placeholder_spec_cannot_ship(self) -> None:
        # A scaffold's own spec ships with every box unticked; that used to be
        # enough to ride one green check all the way to a release.
        self.write_spec(checked=False)
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")
            code, _out, err = run("check", "--path", str(self.project))
            self.assertEqual(code, 0, err)
            self.assertEqual(self.phase(), "ship")

            code, out, _ = run("next", "--path", str(self.project), "--json")
            self.assertIn("1 acceptance criteria in spec/spec.md are still unchecked", json.loads(out)["next"])

            code, _out, err = run("ship", "--path", str(self.project), "--env", "test")
            self.assertEqual(code, 2)
            self.assertIn("still unchecked", err)
            self.assertEqual(server.requests, 0, "nothing was probed, so nothing was released")
            self.assertFalse((self.project / "release" / "release.json").exists())
        finally:
            server.stop()

    def test_local_ship_hashes_directory_artifact(self) -> None:
        self.write_spec()
        dist = self.project / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<h1>demo</h1>\n", encoding="utf-8")
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234", artifact="dist")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = self.release_record()
            self.assertTrue(release["ok"])
            self.assertEqual(release["artifact"]["path"], "dist")
            self.assertEqual(len(release["artifact"]["digest"]), 64)
        finally:
            server.stop()

    def test_invalid_yaml_fails_loud_without_traceback(self) -> None:
        (self.project / "galadon.yaml").write_text("deploy: [\n", encoding="utf-8")
        code, _out, err = run("next", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("galadon.yaml is not valid YAML", err)
        self.assertNotIn("Traceback", err)

    def test_local_ship_requires_probe_expect(self) -> None:
        # Refused up front; it used to be discovered by the probe, after the switch.
        self.write_spec()
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "")
            code, _out, err = run("ship", "--path", str(self.project), "--env", "test")
            self.assertEqual(code, 2)
            self.assertIn("probe_expect is required for the local adapter", err)
            self.assertFalse((self.project / ".galadon" / "deploy").exists())
        finally:
            server.stop()

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
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")
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
        finally:
            server.stop()

    def test_local_ship_rolls_back_to_previous_release_on_failed_probe(self) -> None:
        self.write_spec()
        self.repo()
        frozen: dict[str, str] = {}

        def answer(_requests: int) -> tuple[int, str]:
            # Serves the current HEAD, until told to keep serving an older one.
            commit = frozen.get("commit") or head(self.project)
            return 200, json.dumps({"ok": True, "commit": commit})

        server = ProbeServer(handler=answer)
        try:
            set_probe(self.project, server.url, "{{COMMIT}}")
            first_commit = commit_all(self.project, "probe config")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            current = self.project / ".galadon" / "deploy" / "current"
            first_target = current.resolve()

            self.write_retro()
            self.assertEqual(self.phase(), "done")
            code, out, err = run("new", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)
            code, out, err = run("check", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)
            (self.project / "NOTES.md").write_text("second loop\n", encoding="utf-8")
            second_commit = commit_all(self.project, "loop two")
            # The service never came up on the new build: it keeps answering
            # the first release.
            frozen["commit"] = first_commit

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

            self.assertEqual(code, 1, out + err)
            self.assertIn(f"release {first_commit} is live again", err)
            release = self.release_record()
            self.assertFalse(release["ok"])
            self.assertEqual(release["commit"], second_commit)
            self.assertEqual(release["actual_commit"], first_commit)
            self.assertTrue(release["rollback"]["ok"])
            self.assertEqual(release["rollback"]["version"], first_commit)
            self.assertEqual(current.resolve(), first_target)
        finally:
            server.stop()

    def test_local_ship_static_expect_rollback_reports_ok(self) -> None:
        self.write_spec()
        dist = self.project / "dist"
        dist.mkdir()
        (dist / "marker.txt").write_text("v9.9.9\n", encoding="utf-8")
        deploy = self.project / ".galadon" / "deploy"

        def marker(_requests: int) -> tuple[int, str]:
            # Serves whatever release `current` points at, like a real service.
            try:
                value = (deploy / "current" / "marker.txt").read_text(encoding="utf-8").strip()
            except OSError:
                value = "(no release)"
            return 200, json.dumps({"commit": value})

        server = ProbeServer(handler=marker)
        try:
            set_probe(self.project, server.url, "v9.9.9", artifact="dist")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)

            self.write_retro()
            self.assertEqual(self.phase(), "done")
            code, out, err = run("new", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)
            code, out, err = run("check", "--path", str(self.project), "--json")
            self.assertEqual(code, 0, out + err)
            (dist / "marker.txt").write_text("broken999\n", encoding="utf-8")

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

            self.assertEqual(code, 1, out + err)
            self.assertIn("rolled back automatically", err)
            release = self.release_record()
            self.assertFalse(release["ok"])
            self.assertTrue(release["rollback"]["ok"])
            self.assertEqual(release["rollback"]["actual"], "v9.9.9")
            self.assertEqual(release["actual_commit"], "v9.9.9")
            live = (deploy / "current" / "marker.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(live, "v9.9.9")
        finally:
            server.stop()

    def test_actual_commit_comes_from_the_probe_not_build_metadata(self) -> None:
        self.write_spec()
        self.repo()
        server = ProbeServer(commit="abc1234")
        try:
            set_probe(self.project, server.url, "abc1234")
            commit = commit_all(self.project, "probe config")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = self.release_record()
            self.assertEqual(release["commit"], commit)
            self.assertEqual(release["probe"]["actual"], "abc1234")
            self.assertEqual(release["actual_commit"], "abc1234")
        finally:
            server.stop()

    def test_local_probe_rejects_commit_that_only_appears_in_history(self) -> None:
        self.write_spec()
        self.repo()
        server = ProbeServer(
            handler=lambda _n: (200, '{"commit":"0000000","history":["%s"]}' % head(self.project))
        )
        try:
            set_probe(self.project, server.url, head(self.project))
            commit_all(self.project, "probe config")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")

            self.assertEqual(code, 1, out + err)
            release = self.release_record()
            self.assertFalse(release["probe"]["ok"])
            self.assertEqual(release["probe"]["actual"], "0000000")
            self.assertIn("commit mismatch", release["probe"]["detail"])
            self.assertIsNone(release["actual_commit"], "a failed first release leaves nothing live")
            self.assertTrue(release["rollback"]["first_release"])
        finally:
            server.stop()

    def test_new_archives_the_whole_loop_and_returns_to_build(self) -> None:
        self.write_spec()
        self.write_retro()
        (self.project / ".galadon" / "checks.json").write_text('{"ok": true}', encoding="utf-8")
        (self.project / "release" / "release.json").write_text('{"ok": true}', encoding="utf-8")
        self.assertEqual(self.phase(), "done")

        code, out, err = run("new", "--path", str(self.project), "--json")

        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(len(payload["archived"]), 3)
        self.assertTrue(any(item.endswith("retro.md") for item in payload["archived"]))
        self.assertEqual(payload["next"]["phase"], "build")
        self.assertFalse((self.project / "release" / "release.json").exists())
        self.assertFalse((self.project / "wiki" / "retro.md").exists())
        self.assertTrue((self.project / ".galadon" / "history").is_dir())

    def test_the_second_loop_gets_its_own_learn_phase(self) -> None:
        # The first retro used to satisfy every later loop's learn gate.
        self.write_spec()
        self.repo()
        server = ProbeServer(handler=lambda _n: (200, json.dumps({"commit": head(self.project)})))
        try:
            set_probe(self.project, server.url, "{{COMMIT}}")
            commit_all(self.project, "probe config")
            self.assertEqual(run("ship", "--path", str(self.project), "--env", "test")[0], 0)
            self.write_retro()
            self.assertEqual(run("check", "--path", str(self.project))[0], 0)
            self.assertEqual(self.phase(), "done")
            self.assertEqual(run("new", "--path", str(self.project))[0], 0)
            self.assertEqual(run("check", "--path", str(self.project))[0], 0)
            commit_all(self.project, "loop two")

            self.assertEqual(run("ship", "--path", str(self.project), "--env", "test")[0], 0)

            self.assertEqual(self.phase(), "learn")
            self.assertFalse((self.project / "wiki" / "retro.md").exists())
        finally:
            server.stop()

    def test_new_refuses_before_the_loop_is_done(self) -> None:
        self.write_spec()
        code, _out, err = run("new", "--path", str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("still in build", err)

    def test_check_failure_returns_nonzero_and_keeps_verify_phase(self) -> None:
        self.write_spec()
        data = self.config()
        data["checks"]["test"] = "false"
        self.set_config(data)

        code, out, _ = run("check", "--path", str(self.project), "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertEqual(self.phase(), "verify")

    def test_production_requires_explicit_confirmation(self) -> None:
        self.write_spec()
        data = self.config()
        data["deploy"]["envs"] = {"prod": {}}
        self.set_config(data)
        code, _out, err = run("ship", "--path", str(self.project), "--env", "prod")
        self.assertEqual(code, 2)
        self.assertIn("--yes-prod", err)

    def test_prod_dry_run_previews_without_the_confirmation_flag(self) -> None:
        self.write_spec()
        data = self.config()
        data["deploy"]["envs"] = {"prod": {}}
        self.set_config(data)
        code, out, err = run("ship", "--path", str(self.project), "--env", "prod", "--dry-run", "--json")
        self.assertEqual(code, 0, out + err)
        plan = json.loads(out)
        self.assertEqual(plan["env"], "prod")
        self.assertEqual(plan["requires"], "--yes-prod")

    def test_unimplemented_adapter_fails_loud(self) -> None:
        self.write_spec()
        data = self.config()
        data["deploy"]["adapter"] = "k8s"
        self.set_config(data)
        code, _out, err = run("ship", "--path", str(self.project), "--env", "test")
        self.assertEqual(code, 2)
        self.assertIn("no implementation", err)

    def test_all_profiles_and_role_presets_render(self) -> None:
        for profile in ("web", "api", "static"):
            for role in ("solo", "six-ring", "orchestrator"):
                target = self.tmp / f"{profile}-{role}"
                code, out, err = run(
                    "init", "--path", str(target), "--name", target.name,
                    "--template", profile, "--role", role,
                )
                self.assertEqual(code, 0, out + err)
                self.assertTrue((target / "docs" / "profile.md").is_file())
                self.assertTrue((target / "spec" / "spec.md").is_file())
                text = (target / "galadon.yaml").read_text(encoding="utf-8")
                self.assertIn(f"template: {profile}", text)
                self.assertIn(f"role: {role}", text)

    def test_an_unknown_template_profile_fails_loud(self) -> None:
        code, _out, err = run("init", "--path", str(self.tmp / "bad"), "--template", "shop")
        self.assertEqual(code, 2)
        self.assertIn("unknown template profile: shop", err)
        self.assertFalse((self.tmp / "bad").exists())

    def test_dry_run_does_not_write_release(self) -> None:
        self.write_spec()
        code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--dry-run")
        self.assertEqual(code, 0, out + err)
        self.assertIn("adapter: local", out)
        self.assertFalse((self.project / "release" / "release.json").exists())

    def test_ship_renders_commit_placeholder_in_probe_expect(self) -> None:
        self.write_spec()
        self.repo()
        server = ProbeServer(handler=lambda _n: (200, json.dumps({"commit": head(self.project)})))
        try:
            set_probe(self.project, server.url, "{{COMMIT}}")
            commit = commit_all(self.project, "probe config")
            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            release = self.release_record()
            self.assertTrue(release["ok"])
            self.assertEqual(release["commit"], commit)
        finally:
            server.stop()

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
        self.assertIn("the files already there are kept", err)


if __name__ == "__main__":
    unittest.main()
