from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.config import load_config
from galadon.loop import uncommitted_paths


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
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_dirty_source_file_blocks_the_ship(self) -> None:
        (self.project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertEqual(uncommitted_paths(self.project, self.config), ["src/app.py"])

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


if __name__ == "__main__":
    unittest.main()
