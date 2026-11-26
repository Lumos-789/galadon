"""The ship command's safety net, exercised through the CLI: what it refuses
before touching anything, what it does when the probe or the rollback fails,
and what it leaves behind."""
from __future__ import annotations

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

from galadon.config import load_config  # noqa: E402
from galadon.ports import Artifact, ProbeResult, Release  # noqa: E402
from helpers import ProbeServer, commit_all, git, run, set_probe  # noqa: E402

SPEC = (
    "# demo spec\n\n## Problem\n\nUsers need a reliable demo service.\n\n"
    "## Acceptance\n\n- [x] GET /health returns 200\n\n"
    "## Non-goals\n\nNo authentication in this iteration.\n"
)
UNCHECKED_SPEC = SPEC.replace("- [x]", "- [ ]")

SSH_OPTIONS = {
    "host": "203.0.113.10",
    "user": "root",
    "remote_root": "/srv/demo",
    "service": "demo",
}


def write_config(root: Path, **deploy: object) -> None:
    (root / "galadon.yaml").write_text(
        yaml.safe_dump(
            {
                "project": "demo",
                "template": "web",
                "role": "solo",
                "deploy": {
                    "adapter": "local",
                    "env": "test",
                    "build": "",
                    "migrate": "",
                    "release": "",
                    "probe_url": "",
                    "probe_expect": "",
                    "artifact": "",
                    **deploy,
                },
                "checks": {"build": "true", "test": "true", "timeout": 30},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


class FakeShipAdapter:
    name = "ssh"

    def __init__(self) -> None:
        self.probe_calls = 0
        self.rollback_calls = 0
        self.events: list[str] = []

    def describe(self, ctx) -> str:
        return "root@example:/srv/demo"

    def preflight(self, ctx) -> dict:
        self.events.append("preflight")
        result = {"target": "root@example", "remote_root": "/srv/demo", "service": "demo"}
        if getattr(self, "preflight_notes", None):
            result["notes"] = self.preflight_notes
        return result

    def build(self, ctx) -> Artifact:
        self.events.append("build")
        path = ctx.root / "dist"
        path.mkdir(exist_ok=True)
        return Artifact(path=path, digest="digest")

    def migrate(self, ctx) -> None:
        self.events.append("migrate")
        return None

    def release(self, ctx, artifact: Artifact) -> Release:
        self.events.append("release")
        return Release(
            env=ctx.env,
            artifact=artifact,
            adapter=self.name,
            metadata={"commit": "bad1234"},
        )

    def probe(self, ctx, release: Release) -> ProbeResult:
        self.events.append("probe")
        self.probe_calls += 1
        return ProbeResult(ok=False, detail="commit mismatch", url=ctx.config.deploy.probe_url)

    def rollback(self, ctx, release: Release) -> None:
        self.events.append("rollback")
        self.rollback_calls += 1
        release.metadata["rollback"] = {
            "version": "good123",
            "target": "/srv/demo/releases/20261104-150000-good123",
            "ok": True,
            "probe": {"ok": True, "detail": "HTTP 200 commit good123", "url": None},
        }


def record(root: Path) -> dict:
    return json.loads((root / "release" / "release.json").read_text(encoding="utf-8"))


class ShipWithAFakeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-ship-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        (self.project / "spec").mkdir(parents=True)
        (self.project / "spec" / "spec.md").write_text(SPEC, encoding="utf-8")
        write_config(self.project, adapter="ssh", artifact="dist", options=SSH_OPTIONS)

    def ship(self, adapter: FakeShipAdapter) -> int:
        from unittest import mock

        with mock.patch("galadon.cli.adapter_for", return_value=adapter):
            return run("ship", "--path", str(self.project), "--env", "test")[0]

    def test_ship_rolls_back_and_records_actual_version(self) -> None:
        adapter = FakeShipAdapter()

        self.assertEqual(self.ship(adapter), 1)

        self.assertEqual(adapter.probe_calls, 1)
        self.assertEqual(adapter.rollback_calls, 1)
        self.assertEqual(
            adapter.events,
            ["preflight", "build", "migrate", "release", "probe", "rollback"],
        )
        released = record(self.project)
        self.assertFalse(released["ok"])
        self.assertEqual(released["commit"], "bad1234")
        self.assertEqual(released["actual_commit"], "good123")
        self.assertTrue(released["rollback"]["ok"])
        self.assertEqual(released["rollback"]["version"], "good123")

    def test_ship_prints_preflight_notes(self) -> None:
        from unittest import mock

        adapter = FakeShipAdapter()
        adapter.preflight_notes = [
            "skipping unit drift check: unit template incomplete "
            "(missing bootstrap keys: domain, listen)"
        ]
        with mock.patch("galadon.cli.adapter_for", return_value=adapter):
            code, _out, err = run("ship", "--path", str(self.project), "--env", "test")
        self.assertEqual(code, 1)
        self.assertIn(
            "galadon: skipping unit drift check: unit template incomplete",
            err,
        )

    def test_ship_refuses_an_unverified_spec_before_touching_anything(self) -> None:
        (self.project / "spec" / "spec.md").write_text(UNCHECKED_SPEC, encoding="utf-8")
        adapter = FakeShipAdapter()

        self.assertEqual(self.ship(adapter), 2)

        self.assertEqual(adapter.events, [], "no build, no release")
        self.assertFalse((self.project / "release" / "release.json").exists())

    def test_ship_still_rolls_back_and_records_when_the_probe_crashes(self) -> None:
        # An exception the adapter did not wrap used to escape: no rollback,
        # no release record, and the failed release left live.
        class CrashingProbeAdapter(FakeShipAdapter):
            def probe(self, ctx, release: Release) -> ProbeResult:
                self.events.append("probe")
                self.probe_calls += 1
                raise ValueError("unknown url type")

        adapter = CrashingProbeAdapter()

        self.assertEqual(self.ship(adapter), 1)

        self.assertEqual(adapter.rollback_calls, 1)
        released = record(self.project)
        self.assertFalse(released["ok"])
        self.assertIn("ValueError: unknown url type", released["probe"]["detail"])
        self.assertTrue(released["rollback"]["ok"])

    def test_ship_records_a_failed_rollback_whatever_went_wrong(self) -> None:
        class CrashingRollbackAdapter(FakeShipAdapter):
            def rollback(self, ctx, release: Release) -> None:
                self.rollback_calls += 1
                release.metadata["rollback"] = {"version": "good123", "target": "/x", "ok": False}
                raise OSError("disk on fire")

        adapter = CrashingRollbackAdapter()
        from unittest import mock

        with mock.patch("galadon.cli.adapter_for", return_value=adapter):
            code, _out, err = run("ship", "--path", str(self.project), "--env", "test")

        self.assertEqual(code, 1)
        released = record(self.project)
        self.assertFalse(released["rollback"]["ok"])
        self.assertEqual(released["rollback"]["error"], "OSError: disk on fire")
        self.assertEqual(released["rollback"]["version"], "good123")
        self.assertIn("rollback failed: OSError: disk on fire", err)

    def test_ship_says_when_a_first_release_left_nothing_live(self) -> None:
        class FirstReleaseAdapter(FakeShipAdapter):
            def rollback(self, ctx, release: Release) -> None:
                self.rollback_calls += 1
                release.metadata["rollback"] = {
                    "version": None,
                    "target": None,
                    "ok": True,
                    "first_release": True,
                }

        adapter = FirstReleaseAdapter()
        from unittest import mock

        with mock.patch("galadon.cli.adapter_for", return_value=adapter):
            code, out, err = run("ship", "--path", str(self.project), "--env", "test")

        self.assertEqual(code, 1)
        self.assertIn("this was the first release, so nothing is left live", err)
        self.assertIn("rollback: first release; nothing is live", out)
        released = record(self.project)
        self.assertTrue(released["rollback"]["first_release"])
        self.assertIsNone(released["actual_commit"])


class LocalShipSafetyTests(unittest.TestCase):
    """The real local adapter, no mocks: what `ship` does to a project."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-ship-local-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        (self.project / "spec").mkdir(parents=True)
        (self.project / "spec" / "spec.md").write_text(SPEC, encoding="utf-8")
        (self.project / "AGENTS.md").write_text("# demo\n", encoding="utf-8")
        write_config(self.project)

    def leave_empty(self, setting: str) -> None:
        """Rewrite `key: value` as `key:` — a value left empty in the YAML."""
        path = self.project / "galadon.yaml"
        key = setting.split(":", 1)[0]
        text = path.read_text(encoding="utf-8")
        assert text.count(setting) == 1, f"{setting!r} not found once in galadon.yaml"
        path.write_text(text.replace(setting, f"{key}:"), encoding="utf-8")

    def ship(self, *extra: str) -> tuple[int, str, str]:
        return run("ship", "--path", str(self.project), "--env", "test", *extra)

    def deploy_root(self) -> Path:
        return self.project / ".galadon" / "deploy"

    def test_a_failed_first_release_leaves_nothing_live_and_says_so(self) -> None:
        # Before it: `current` stayed on the release that just failed.
        server = ProbeServer(commit="not-the-build")
        try:
            set_probe(self.project, server.url, "v1")
            code, out, err = self.ship()
            self.assertEqual(code, 1)
            self.assertIn("this was the first release", err)
            self.assertFalse((self.deploy_root() / "current").is_symlink())
            self.assertFalse((self.deploy_root() / "previous").is_symlink())
            released = record(self.project)
            self.assertFalse(released["ok"])
            self.assertTrue(released["rollback"]["first_release"])
            self.assertIsNone(released["actual_commit"])
        finally:
            server.stop()

    def test_a_probe_left_empty_is_refused_before_anything_is_built(self) -> None:
        # It used to be read as the URL "None" and crash after the switch.
        write_config(self.project, build="touch build-ran.txt")
        self.leave_empty("probe_url: ''")

        code, _out, err = self.ship()

        self.assertEqual(code, 2)
        self.assertIn("probe_url is required", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.project / "build-ran.txt").exists(), "the build never ran")
        self.assertFalse(self.deploy_root().exists())

    def test_a_build_key_left_empty_is_not_run_as_a_command(self) -> None:
        # `build:` used to be read as the string "None" and executed.
        write_config(self.project, build="placeholder")
        self.leave_empty("build: placeholder")

        config = load_config(self.project)

        self.assertEqual(config.deploy.build, "")
        code, out, _err = self.ship("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("build: (no command)", out)

    def test_dry_run_runs_nothing_and_writes_nothing(self) -> None:
        path = self.project / "galadon.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["deploy"]["build"] = "touch build-ran.txt"
        data["checks"]["test"] = "exit 3"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        agents = self.project / "AGENTS.md"
        before = agents.read_text(encoding="utf-8")

        code, out, _err = self.ship("--dry-run")

        self.assertEqual(code, 0, out)
        self.assertIn("adapter: local", out)
        self.assertIn("checks: not run by --dry-run", out)
        self.assertFalse((self.project / "build-ran.txt").exists())
        self.assertFalse((self.project / ".galadon" / "checks.json").exists())
        self.assertFalse((self.project / "verify" / "evidence.md").exists())
        self.assertEqual(agents.read_text(encoding="utf-8"), before)

    def test_dry_run_says_what_a_real_ship_would_refuse(self) -> None:
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")
            git(self.project, "init")
            commit_all(self.project, "init")
            (self.project / "src").mkdir()
            (self.project / "src" / "stray.py").write_text("DATA = 1\n", encoding="utf-8")
            (self.project / "spec" / "spec.md").write_text(UNCHECKED_SPEC, encoding="utf-8")

            code, out, _err = self.ship("--dry-run", "--json")

            self.assertEqual(code, 0, out)
            plan = json.loads(out)
            self.assertEqual(len(plan["would_refuse"]), 2, plan)
            self.assertTrue(any("src/stray.py" in line for line in plan["would_refuse"]))
            self.assertTrue(any("still unchecked" in line for line in plan["would_refuse"]))
        finally:
            server.stop()

    def test_git_that_cannot_answer_blocks_the_ship(self) -> None:
        # It used to read as "no git here" and let uncommitted work ship.
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")
            (self.project / ".git").write_text("gitdir: /nonexistent/repo\n", encoding="utf-8")
            (self.project / "stray.txt").write_text("uncommitted\n", encoding="utf-8")

            code, _out, err = self.ship()

            self.assertEqual(code, 2)
            self.assertIn("git cannot read", err)
            self.assertNotIn("Traceback", err)
            self.assertFalse(self.deploy_root().exists())
        finally:
            server.stop()

    def test_an_untracked_folder_inside_another_repo_ships_without_a_commit(self) -> None:
        git(self.tmp, "init")
        (self.tmp / ".gitignore").write_text("demo/\n", encoding="utf-8")
        commit_all(self.tmp, "base")
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")

            code, _out, err = self.ship()

            self.assertEqual(code, 0, err)
            self.assertIn("not tracked by it; this release carries no commit", err)
            self.assertIsNone(record(self.project)["commit"])
        finally:
            server.stop()


class EnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        (self.project / "spec").mkdir(parents=True)
        (self.project / "spec" / "spec.md").write_text(SPEC, encoding="utf-8")
        write_config(self.project)

    def envs(self, test: ProbeServer, prod: ProbeServer, **prod_extra: object) -> None:
        path = self.project / "galadon.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["deploy"]["envs"] = {
            "test": {"probe_url": test.url, "probe_expect": "test-build"},
            "prod": {"probe_url": prod.url, "probe_expect": "prod-build", **prod_extra},
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def test_an_environment_is_a_real_target_not_a_label(self) -> None:
        test = ProbeServer(commit="test-build")
        prod = ProbeServer(commit="prod-build")
        try:
            self.envs(test, prod)

            # The preview names the environment's own probe and touches neither.
            code, out, err = run("ship", "--path", str(self.project), "--env", "prod", "--dry-run", "--json")
            self.assertEqual(code, 0, out + err)
            plan = json.loads(out)
            self.assertEqual((plan["env"], plan["probe"]), ("prod", prod.url))
            self.assertEqual(plan["requires"], "--yes-prod")
            self.assertEqual((test.requests, prod.requests), (0, 0))

            code, out, err = run("ship", "--path", str(self.project), "--env", "test", "--json")
            self.assertEqual(code, 0, out + err)
            released = json.loads(out)["release"]
            self.assertEqual((released["env"], released["probe"]["url"]), ("test", test.url))
            self.assertEqual(released["actual_commit"], "test-build")
            self.assertEqual((test.requests, prod.requests), (1, 0))
        finally:
            test.stop()
            prod.stop()

    def test_a_production_environment_needs_the_flag_in_the_same_invocation(self) -> None:
        test = ProbeServer(commit="test-build")
        prod = ProbeServer(commit="prod-build")
        try:
            self.envs(test, prod)

            code, _out, err = run("ship", "--path", str(self.project), "--env", "prod")
            self.assertEqual(code, 2)
            self.assertIn("--yes-prod", err)

            code, out, err = run("ship", "--path", str(self.project), "--env", "prod", "--yes-prod", "--json")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(json.loads(out)["release"]["probe"]["url"], prod.url)
        finally:
            test.stop()
            prod.stop()

    def test_protection_can_be_declared_for_a_differently_named_environment(self) -> None:
        test = ProbeServer(commit="test-build")
        prod = ProbeServer(commit="prod-build")
        try:
            self.envs(test, prod, protected=False)
            path = self.project / "galadon.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["deploy"]["envs"]["prod"]["protected"] = False
            data["deploy"]["envs"]["live"] = dict(data["deploy"]["envs"]["prod"], protected=True)
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

            code, _out, err = run("ship", "--path", str(self.project), "--env", "live")
            self.assertEqual(code, 2)
            self.assertIn("--yes-prod", err)

            code, out, _err = run("ship", "--path", str(self.project), "--env", "prod", "--json")
            self.assertEqual(code, 0, out)
        finally:
            test.stop()
            prod.stop()

    def test_an_undefined_environment_is_refused_before_anything_runs(self) -> None:
        server = ProbeServer()
        try:
            set_probe(self.project, server.url, "abc1234")

            code, _out, err = run("ship", "--path", str(self.project), "--env", "production")

            self.assertEqual(code, 2)
            self.assertIn("defines no deploy.envs.production", err)
            self.assertEqual(server.requests, 0)
            self.assertFalse((self.project / "release" / "release.json").exists())
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
