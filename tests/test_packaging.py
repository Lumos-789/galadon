"""The distribution set that `package.json` `files` declares.

The npm packer folds overlapping entries; the ZCode marketplace payload tool
copies each entry on its own and stops with "File exists" when two of them
cover the same file. The templates live inside the package and carry `.py`
files of their own, so a `*.py` glob next to a `templates/` directory entry
used to collide.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.templates import BUNDLED_TEMPLATES, FileTemplateProvider  # noqa: E402


def expanded(entry: str) -> list[Path]:
    """The files one `files` entry ships, relative to the repository root."""
    pattern = entry.rstrip("/")
    if any(char in pattern for char in "*?["):
        return sorted(path for path in ROOT.glob(pattern) if path.is_file())
    target = ROOT / pattern
    if target.is_dir():
        return sorted(path for path in target.rglob("*") if path.is_file())
    return [target]


class PackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["files"]
        self.shipped: dict[str, list[str]] = {}
        for entry in self.entries:
            files = expanded(entry)
            self.assertTrue(files, f"package.json files entry ships nothing: {entry}")
            self.shipped[entry] = [path.relative_to(ROOT).as_posix() for path in files]

    def test_no_two_entries_ship_the_same_file(self) -> None:
        seen: dict[str, str] = {}
        overlaps = []
        for entry, files in self.shipped.items():
            for path in files:
                if path in seen:
                    overlaps.append(f"{path} <- {seen[path]} and {entry}")
                seen[path] = entry
        self.assertEqual(overlaps, [])

    def test_every_template_and_module_ships_exactly_once(self) -> None:
        shipped = [path for files in self.shipped.values() for path in files]
        for directory in (BUNDLED_TEMPLATES, ROOT / "src" / "galadon"):
            for path in directory.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    relative = path.relative_to(ROOT).as_posix()
                    self.assertEqual(shipped.count(relative), 1, relative)
        self.assertFalse([path for path in shipped if "__pycache__" in path or path.endswith(".pyc")])

    def test_the_skill_the_cli_and_the_manifests_are_in_the_set(self) -> None:
        shipped = {path for files in self.shipped.values() for path in files}
        for required in (
            "bin/galadon",
            "skills/galadon/SKILL.md",
            ".zcode-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            "assets/galadon/icon.png",
            "README.md",
            "README_CN.md",
        ):
            self.assertIn(required, shipped)
        self.assertFalse([path for path in shipped if path.startswith("tests/")], "tests are not distributed")


class ZipInstallTests(unittest.TestCase):
    """What the scaffold does when the plugin itself arrived as the marketplace
    zip, which carries every file as 0644: the CI `build_zip()` writes that
    mode for all entries, templates included."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-zip-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A copy of the bundled templates with the exec bit stripped everywhere.
        self.plain = self.tmp / "plain-templates"
        shutil.copytree(BUNDLED_TEMPLATES, self.plain)
        for path in self.plain.rglob("*"):
            if path.is_file():
                path.chmod(0o644)

    def render(self, layout: str = "none", stack: str = "none") -> Path:
        project = self.tmp / f"project-{layout}-{stack}"
        FileTemplateProvider(self.plain).render(
            project, "web", "solo", "demo", layout=layout, stack=stack
        )
        return project

    def test_wrappers_are_runnable_even_when_no_template_is(self) -> None:
        self.assertFalse([p for p in self.plain.rglob("*") if p.is_file() and os.access(p, os.X_OK)])

        for project, runnable in (
            (self.render("plugin", "python"), ("bin/demo", "scripts/dev.sh", "scripts/quality-check.sh")),
            (self.render("webapp", "python"), ("scripts/dev.sh", "scripts/quality-check.sh")),
            (self.render("webapp", "node"), ("scripts/dev.sh", "scripts/quality-check.sh")),
            (self.render(), ("scripts/quality-check.sh",)),
        ):
            for relative in runnable:
                self.assertTrue(os.access(project / relative, os.X_OK), relative)
            self.assertFalse(os.access(project / "README.md", os.X_OK), "a document is not made executable")

    def test_a_generated_wrapper_actually_runs(self) -> None:
        project = self.render("plugin", "python")
        proc = subprocess.run([str(project / "bin" / "demo"), "--version"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("demo 0.1.0", proc.stdout)
        proc = subprocess.run([str(project / "scripts" / "dev.sh"), "ping", "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"ok": true', proc.stdout)


if __name__ == "__main__":
    unittest.main()
