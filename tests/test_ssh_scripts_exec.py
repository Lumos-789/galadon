"""The bash the ssh adapter sends to a host, run for real on this machine.

The adapter tests check what the scripts say; these run them. `systemctl`,
`flock` and `chown` are shims that only log, `mv` gains the GNU `-T` flag
macOS lacks, and `readlink -f` is pinned so the suite does not depend on the
macOS version. Everything else — links, locks, `set -euo pipefail` — is the
real thing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.adapters.ssh_scripts import (  # noqa: E402
    REMOTE_PATH,
    finalize_script,
    rollback_script,
)

SHIMS = {
    "flock": "#!/bin/sh\nexit 0\n",
    "chown": "#!/bin/sh\nexit 0\n",
    # GNU `-T` means "replace the link itself, never follow it into a
    # directory". Dropping it would move the new link *into* the old release.
    # BSD `-h` is the same promise for a symlink target.
    "mv": (
        "#!/bin/sh\n"
        'if [ "$1" = "-Tf" ]; then shift; exec /bin/mv -h -f "$@"; fi\n'
        'exec /bin/mv "$@"\n'
    ),
    "systemctl": (
        "#!/bin/sh\n"
        'echo "$@" >> "$GALADON_SYSTEMCTL_LOG"\n'
        'if [ "$1 $2" = "$GALADON_SYSTEMCTL_FAIL" ]; then exit 1; fi\n'
        "exit 0\n"
    ),
    "readlink": (
        "#!/bin/sh\n"
        '[ "$1" = "-f" ] || exec /usr/bin/readlink "$@"\n'
        f'exec {sys.executable} -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "$2"\n'
    ),
}


class Host:
    """A stand-in for the remote host: a release tree under a temp directory."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "srv" / "demo"
        (self.root / "releases").mkdir(parents=True)
        (self.root / "shared").mkdir()
        shims = tmp / "shims"
        shims.mkdir()
        for name, body in SHIMS.items():
            (shims / name).write_text(body, encoding="utf-8")
            (shims / name).chmod(0o755)
        self.path_line = REMOTE_PATH.replace(
            "export PATH=", f"export PATH={shims}:", 1
        )
        self.log = tmp / "systemctl.log"
        self.fail = ""

    def release(self, name: str) -> Path:
        path = self.root / "releases" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "app.py").write_text(f"VERSION = {name!r}\n", encoding="utf-8")
        # Resolved: the temp dir is a symlink on macOS, as realpaths on a host are not.
        return path.resolve()

    def link(self, name: str, target: Path) -> None:
        (self.root / name).unlink(missing_ok=True)
        (self.root / name).symlink_to(target)

    def target(self, name: str) -> Path | None:
        link = self.root / name
        return link.resolve() if link.is_symlink() else None

    def exists(self, name: str) -> bool:
        return (self.root / name).is_symlink() or (self.root / name).exists()

    def releases(self) -> list[str]:
        return sorted(path.name for path in (self.root / "releases").iterdir())

    def run(self, script: str, fail: str = "") -> subprocess.CompletedProcess:
        script = script.replace(REMOTE_PATH, self.path_line, 1)
        assert script.count(self.path_line) == 1, "the shims must come first on PATH"
        env = {
            **os.environ,
            "GALADON_SYSTEMCTL_LOG": str(self.log),
            "GALADON_SYSTEMCTL_FAIL": fail,
        }
        return subprocess.run(
            ["bash", "-s"], input=script, text=True, capture_output=True, env=env, check=False
        )

    def systemctl_calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def clear_log(self) -> None:
        self.log.unlink(missing_ok=True)


class FinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-exec-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.host = Host(self.tmp)
        self.first = self.host.release("20261126-100000-aaa1111")
        self.second = self.host.release("20261126-100100-bbb2222")

    def finalize(self, release: Path, migrate: str = "", keep: int = 5, fail: str = "") -> subprocess.CompletedProcess:
        return self.host.run(
            finalize_script(str(self.host.root.resolve()), "demo", "galadon", str(release), migrate, keep),
            fail,
        )

    def fields(self, proc: subprocess.CompletedProcess) -> dict[str, str]:
        fields = {}
        for line in proc.stdout.splitlines():
            if line.startswith("GALADON_") and "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        return fields

    def test_the_first_release_switches_current_and_restarts(self) -> None:
        proc = self.finalize(self.first)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host.target("current"), self.first)
        self.assertFalse(self.host.exists("previous"), "nothing was live before")
        self.assertEqual(self.host.systemctl_calls(), ["restart demo"])
        self.assertEqual(self.fields(proc)["GALADON_PREVIOUS"], "")
        self.assertEqual(self.fields(proc)["GALADON_OK"], "1")

    def test_a_later_release_keeps_the_previous_one(self) -> None:
        self.host.link("current", self.first)

        proc = self.finalize(self.second)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host.target("current"), self.second)
        self.assertEqual(self.host.target("previous"), self.first)
        self.assertEqual(self.fields(proc)["GALADON_PREVIOUS"], str(self.first.resolve()))
        self.assertEqual(self.fields(proc)["GALADON_PRUNED"].split(), [], "two releases are inside the limit")

    def test_a_failed_restart_restores_the_previous_release(self) -> None:
        self.host.link("current", self.first)

        proc = self.finalize(self.second, fail="restart demo")

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.host.target("current"), self.first)
        self.assertIn("restoring previous current", proc.stderr)

    def test_a_failed_restart_on_the_first_release_removes_current(self) -> None:
        proc = self.finalize(self.first, fail="restart demo")

        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.host.exists("current"))

    def test_the_remote_migrate_runs_inside_the_new_release_before_the_switch(self) -> None:
        self.host.link("current", self.first)

        proc = self.finalize(self.second, migrate="pwd > where.txt && touch migrated")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.second / "migrated").exists(), True)
        self.assertTrue((self.second / "where.txt").read_text().strip().endswith("bbb2222"))
        self.assertEqual(self.host.target("current"), self.second)

    def test_a_failed_remote_migrate_leaves_current_alone(self) -> None:
        self.host.link("current", self.first)

        proc = self.finalize(self.second, migrate="exit 3")

        self.assertEqual(proc.returncode, 70)
        self.assertEqual(self.host.target("current"), self.first)
        self.assertIn("remote migrate failed with exit 3", proc.stderr)
        self.assertIn("current was not switched", proc.stderr)
        self.assertEqual(self.host.systemctl_calls(), [], "the service is never restarted")

    def test_old_releases_are_pruned_after_the_switch(self) -> None:
        older = [self.host.release(f"20261126-0{n}0000-old{n}000") for n in range(3)]
        (self.host.root / "releases" / "not-ours").mkdir()
        self.host.link("current", self.first)

        proc = self.finalize(self.second, keep=2)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.host.releases(),
            sorted(["20261126-100000-aaa1111", "20261126-100100-bbb2222", "not-ours"]),
        )
        # Listed newest first, the order the host walked them.
        self.assertEqual(
            self.fields(proc)["GALADON_PRUNED"].split(), [path.name for path in reversed(older)]
        )
        self.assertEqual(self.host.target("current"), self.second)
        self.assertEqual(self.host.target("previous"), self.first)

    def test_pruning_never_removes_the_live_or_the_previous_release(self) -> None:
        self.host.link("current", self.first)

        proc = self.finalize(self.second, keep=1)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host.target("current"), self.second)
        self.assertEqual(self.host.target("previous"), self.first)
        self.assertTrue(self.first.is_dir(), "`previous` is beyond the limit and still kept")

    def test_keep_releases_zero_prunes_nothing(self) -> None:
        older = [self.host.release(f"20261126-0{n}0000-old{n}000") for n in range(3)]
        self.host.link("current", older[-1])

        proc = self.finalize(self.second, keep=0)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.host.releases()), 5)
        self.assertNotIn("GALADON_PRUNED", proc.stdout)


class RollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-exec-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.host = Host(self.tmp)
        self.first = self.host.release("20261126-100000-aaa1111")
        self.second = self.host.release("20261126-100100-bbb2222")

    def rollback(self) -> subprocess.CompletedProcess:
        return self.host.run(rollback_script(str(self.host.root.resolve()), "demo"))

    def test_rollback_switches_back_to_the_previous_release_and_restarts(self) -> None:
        self.host.link("current", self.second)
        self.host.link("previous", self.first)

        proc = self.rollback()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host.target("current"), self.first)
        self.assertEqual(self.host.systemctl_calls(), ["restart demo"])
        self.assertIn(f"GALADON_ROLLBACK_TO={self.first.resolve()}", proc.stdout)

    def test_a_first_release_rollback_leaves_nothing_live(self) -> None:
        self.host.link("current", self.first)

        proc = self.rollback()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.host.exists("current"))
        self.assertEqual(self.host.systemctl_calls(), ["stop demo"])
        self.assertIn("GALADON_FIRST_RELEASE=1", proc.stdout)
        self.assertTrue(self.first.is_dir(), "the release stays as the record")

    def test_with_nothing_deployed_it_reports_a_first_release_and_changes_nothing(self) -> None:
        proc = self.rollback()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.host.exists("current"))
        self.assertIn("GALADON_FIRST_RELEASE=1", proc.stdout)
        self.assertEqual(self.host.systemctl_calls(), ["stop demo"])

    def test_a_broken_previous_link_is_not_mistaken_for_a_first_release(self) -> None:
        (self.host.root / "previous").symlink_to(self.host.root / "releases" / "gone")
        self.host.link("current", self.second)

        proc = self.rollback()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.host.target("current"), self.second, "the live release is left alone")
        self.assertEqual(self.host.systemctl_calls(), [], "the service is left running")
        self.assertIn("previous release is missing", proc.stderr)

    def test_a_previous_link_outside_the_release_tree_is_refused(self) -> None:
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (self.host.root / "previous").symlink_to(outside)

        proc = self.rollback()

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not point into", proc.stderr)
        self.assertEqual(self.host.systemctl_calls(), [])


if __name__ == "__main__":
    unittest.main()
