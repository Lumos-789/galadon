from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.config import load_config  # noqa: E402
from galadon.loop import uncommitted_paths  # noqa: E402
from galadon.util import GitError, git_context  # noqa: E402


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class UncommittedPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-dirty-"))
        self.project = self.tmp / "demo"
        (self.project / "src").mkdir(parents=True)
        (self.project / "verify").mkdir()
        (self.project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.project / "verify" / "evidence.md").write_text("old\n", encoding="utf-8")
        (self.project / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        (self.project / "galadon.yaml").write_text(
            yaml.safe_dump(
                {
                    "project": "demo",
                    "template": "api",
                    "role": "solo",
                    "deploy": {
                        "adapter": "local",
                        "env": "test",
                        "build": "",
                        "migrate": "",
                        "release": "",
                        "probe_url": "http://127.0.0.1:1/health.json",
                        "probe_expect": "",
                        "artifact": "dist",
                        "options": {},
                    },
                    "checks": {"build": "true", "test": "true", "timeout": 30},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        git(self.project, "init")
        git(self.project, "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
        git(
            self.project,
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=a",
            "commit",
            "-m",
            "base",
        )
        self.config = load_config(self.project)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_dirty_source_file_blocks_the_ship(self) -> None:
        (self.project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(self.project, self.config), ["src/app.py"])

    def test_an_untracked_source_file_blocks_the_ship(self) -> None:
        (self.project / "src" / "new_module.py").write_text("VALUE = 4\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(self.project, self.config), ["src/new_module.py"])

    def test_untracked_files_the_loop_owns_do_not_block(self) -> None:
        checks = self.project / ".galadon" / "checks.json"
        checks.parent.mkdir(parents=True, exist_ok=True)
        checks.write_text("{}\n", encoding="utf-8")
        release = self.project / self.config.paths.release
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_text("{}\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(self.project, self.config), [])

    def test_the_loops_own_files_are_not_blocking(self) -> None:
        (self.project / "verify" / "evidence.md").write_text("new\n", encoding="utf-8")
        (self.project / "AGENTS.md").write_text("# agents\n\n- phase: ship\n", encoding="utf-8")
        release = self.project / self.config.paths.release
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_text("{}\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(self.project, self.config), [])

    def test_a_clean_tree_reports_nothing(self) -> None:
        self.assertEqual(uncommitted_paths(self.project, self.config), [])

    def test_without_git_nothing_is_reported(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "galadon.yaml").write_text(
            (self.project / "galadon.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (plain / "src").mkdir()
        (plain / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        config = load_config(plain)
        self.assertEqual(uncommitted_paths(plain, config), [])

    def test_git_that_cannot_answer_fails_loud(self) -> None:
        # A broken repository used to read as "no git here", which switched
        # the guard off and let uncommitted work ship under no commit at all.
        shutil.rmtree(self.project / ".git")
        (self.project / ".git").write_text("gitdir: /nonexistent/repo\n", encoding="utf-8")
        with self.assertRaises(GitError) as caught:
            uncommitted_paths(self.project, self.config)
        self.assertIn("git cannot read", str(caught.exception))

    def test_an_untracked_folder_inside_another_repo_is_not_versioned(self) -> None:
        # The outer repository's commit has nothing to do with this project;
        # it must not be stamped on its releases.
        outer = self.tmp / "outer"
        outer.mkdir()
        git(outer, "init")
        (outer / ".gitignore").write_text("projects/\n", encoding="utf-8")
        git(outer, "add", "-A")
        self.commit(outer, "base")
        nested = self.copy_config(outer / "projects" / "demo")
        (nested / "src").mkdir()
        (nested / "src" / "app.py").write_text("VALUE = 9\n", encoding="utf-8")

        self.assertIsNone(git_context(nested).commit)
        self.assertEqual(uncommitted_paths(nested, load_config(nested)), [])

    def test_a_subproject_of_a_larger_repo_sees_only_its_own_tree(self) -> None:
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git(outer, "init")
        (outer / "other").mkdir()
        (outer / "other" / "x.txt").write_text("x\n", encoding="utf-8")
        sub = self.copy_config(outer / "services" / "demo")
        git(outer, "add", "-A")
        self.commit(outer, "base")
        config = load_config(sub)

        head = subprocess.run(
            ["git", "-C", str(outer), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(git_context(sub).commit, head)
        self.assertEqual(git_context(sub).prefix, "services/demo/")
        self.assertEqual(uncommitted_paths(sub, config), [])

        (outer / "other" / "x.txt").write_text("changed\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(sub, config), [], "another team's file is not ours")

        (sub / "src").mkdir()
        (sub / "src" / "new.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(sub, config), ["src/new.py"])

    def copy_config(self, path: Path) -> Path:
        path.mkdir(parents=True)
        (path / "galadon.yaml").write_text(
            (self.project / "galadon.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return path

    def commit(self, root: Path, message: str) -> None:
        git(
            root,
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=a",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )


if __name__ == "__main__":
    unittest.main()
