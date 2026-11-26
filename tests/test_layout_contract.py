"""What every layout owes a project: the spec truth layer, a green or
loudly-failing scaffold for every combination, and a bundled example that
still works."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from galadon.cli import main  # noqa: E402
from galadon.config import LAYOUTS, STACKS, TEMPLATES, load_config  # noqa: E402
from galadon.layouts import (  # noqa: E402
    LAYOUT_SPECS,
    checks_for,
    layout_contract_problems,
    layout_listing,
)
from galadon.templates import FileTemplateProvider  # noqa: E402
from helpers import SPEC_TRUTH_FILES, run  # noqa: E402


def item(report: dict, item_id: str) -> dict:
    return next(result for result in report["results"] if result["id"] == item_id)

EXPECTED_LISTING = """\
layouts (galadon init --layout):
  none     base template only (default); no layout contract
  webapp   server/ src/ (python) web/ e2e/ tests/ scripts/ deploy/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/
  service  server/ src/ (python) tests/ scripts/ deploy/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/
  plugin   bin/<project> src/<project>/ skills/<project>/ docs/ tests/ scripts/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/

stacks (galadon init --stack):
  python   build: python3 -m compileall -f -q <layout sources> | test: python3 -m unittest discover -s tests -v
  node     build: node --check <entry> | test: npm test (package.json scripts.test = node --test tests/)
  none     no checks: galadon check fails with "no checks configured — declare checks.build/checks.test" once the spec is complete

plugin appends to the build command: && bash -n bin/<project>
"""

class SpecTruthLayerTests(unittest.TestCase):
    layouts = ("webapp", "service", "plugin")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-truth-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def init(self, layout: str, subdir: str = "") -> Path:
        project = self.tmp / (subdir or layout)
        code, out, err = run(
            "init",
            "--path",
            str(project),
            "--name",
            "demo",
            "--layout",
            layout,
            "--stack",
            "python",
        )
        self.assertEqual(code, 0, out + err)
        return project

    def template_root(self) -> Path:
        root = self.tmp / "template-plugin"
        base = root / "templates" / "base"
        base.mkdir(parents=True)
        (base / "README.md").write_text("base", encoding="utf-8")
        (root / "templates" / "profiles" / "web").mkdir(parents=True)
        return root

    def write_template(self, root: Path, relative: str, text: str) -> None:
        path = root / "templates" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_each_layout_ships_the_four_spec_truth_files(self) -> None:
        for layout in self.layouts:
            with self.subTest(layout=layout):
                project = self.init(layout)
                for relative in SPEC_TRUTH_FILES:
                    path = project / relative
                    self.assertTrue(path.is_file(), f"{layout}: {relative}")
                    text = path.read_text(encoding="utf-8")
                    self.assertGreater(len(text.strip()), 0, f"{layout}: {relative}")
                    self.assertNotIn("{{", text, f"{layout}: {relative}")
                    self.assertNotIn("TODO", text, f"{layout}: {relative}")
                    self.assertNotIn("FIXME", text, f"{layout}: {relative}")

    def test_spec_truth_layer_states_the_required_rules(self) -> None:
        project = self.init("webapp")
        index = (project / "spec" / "INDEX.md").read_text(encoding="utf-8")
        for marker in (
            "F-<DOMAIN>-01",
            "| ID | Feature point | GATE | 主落点 |",
            "`1w`",
            "`2w`",
        ):
            self.assertIn(marker, index)
        domains = (project / "spec" / "domains" / "README.md").read_text(encoding="utf-8")
        for marker in ("`impl`", "spec/INDEX.md", "behaviour truth"):
            self.assertIn(marker, domains)
        testing = (project / "spec" / "TESTING.md").read_text(encoding="utf-8")
        for marker in (
            "backend route",
            "database schema",
            "UI component",
            "deployment config",
            "docs only",
            "`checks.test`",
        ):
            self.assertIn(marker, testing)
        constitution = (project / "spec" / "CONSTITUTION.md").read_text(encoding="utf-8")
        for marker in (
            "Never change silently",
            "Requires review",
            "testable acceptance criterion",
        ):
            self.assertIn(marker, constitution)

    def test_a_fresh_layout_passes_check_with_the_truth_layer(self) -> None:
        for layout in self.layouts:
            with self.subTest(layout=layout):
                project = self.init(layout, subdir=f"fresh-{layout}")
                code, out, err = run("check", "--path", str(project), "--json")
                self.assertEqual(code, 0, out + err)
                report = json.loads(out)
                self.assertTrue(report["ok"])
                layout_result = item(report, "layout")
                self.assertEqual(layout_result["code"], 0, layout_result["stdout"])

    def test_shared_layer_renders_before_layout_common(self) -> None:
        root = self.template_root()
        self.write_template(root, "layouts/_shared/spec/INDEX.md", "shared")
        self.write_template(root, "layouts/webapp/common/spec/INDEX.md", "common")
        (root / "templates" / "layouts" / "webapp" / "python").mkdir(parents=True)
        project = self.tmp / "override"
        FileTemplateProvider(root / "templates").render(
            project, "web", "solo", "demo", layout="webapp", stack="python"
        )
        self.assertEqual(
            (project / "spec" / "INDEX.md").read_text(encoding="utf-8"),
            "common",
        )

    def test_layout_none_does_not_render_the_shared_layer(self) -> None:
        root = self.template_root()
        self.write_template(root, "layouts/_shared/spec/INDEX.md", "shared")
        project = self.tmp / "none-provider"
        FileTemplateProvider(root / "templates").render(
            project, "web", "solo", "demo", layout="none", stack="none"
        )
        self.assertTrue((project / "README.md").is_file())
        self.assertFalse((project / "spec" / "INDEX.md").exists())



class LayoutMatrixTests(unittest.TestCase):
    """Every layout × stack × template combination either ships a gate that is
    green on a fresh scaffold, or fails loudly with the reason that combination
    is expected to produce. A combination that silently produces a broken
    project fails here."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-matrix-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_combination_is_green_or_fails_by_design(self) -> None:
        for layout in LAYOUTS:
            for stack in STACKS:
                for template in TEMPLATES:
                    key = f"{layout}/{stack}/{template}"
                    with self.subTest(combination=key):
                        project = self.tmp / f"{layout}-{stack}-{template}"
                        code, out, err = run(
                            "init",
                            "--path",
                            str(project),
                            "--name",
                            "demo",
                            "--layout",
                            layout,
                            "--stack",
                            stack,
                            "--template",
                            template,
                        )
                        self.assertEqual(code, 0, f"{key}: init failed\n{out}{err}")

                        code, out, err = run("check", "--path", str(project), "--json")
                        report = json.loads(out)
                        failed = {
                            result["id"]: result
                            for result in report["results"]
                            if result["code"] != 0
                        }

                        if layout != "none" and stack != "none":
                            self.assertEqual(code, 0, f"{key}: expected green\n{out}{err}")
                            self.assertTrue(report["ok"], key)
                            self.assertEqual(failed, {}, key)
                            continue

                        self.assertEqual(
                            code, 1, f"{key}: expected a loud failure\n{out}{err}"
                        )
                        self.assertFalse(report["ok"], key)

                        if layout == "none":
                            # The base stub is full of TODO, so a fresh `none`
                            # project stops at the spec gate first.
                            self.assertEqual(sorted(failed), ["spec"], key)
                            self.assertIn("TODO", failed["spec"]["stdout"], key)
                        else:
                            # A real layout with --stack none ships a filled-in
                            # spec and stops at the layout contract, which names
                            # the missing checks.
                            self.assertEqual(sorted(failed), ["layout"], key)
                            self.assertIn(
                                "no checks configured", failed["layout"]["stdout"], key
                            )


class BundledExampleTests(unittest.TestCase):
    """`examples/minimal` ships inside the npm tarball, so it has to keep
    working; nothing else in the suite would notice it rotting."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-example-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_bundled_example_passes_check(self) -> None:
        # `check` writes .galadon/ and verify/; copy to a scratch directory so
        # the shipped example stays free of generated artifacts.
        project = self.tmp / "minimal"
        shutil.copytree(
            ROOT / "examples" / "minimal",
            project,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".galadon", "verify"),
        )
        code, out, err = run("check", "--path", str(project), "--json")
        report = json.loads(out)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(report["ok"])
        self.assertEqual(
            [result["id"] for result in report["results"]],
            ["spec", "build", "test"],
        )

    def test_shipped_example_carries_no_generated_artifacts(self) -> None:
        self.assertFalse(
            (ROOT / "examples" / "minimal" / "verify").exists(),
            "examples/minimal/verify is generated by `galadon check`; run it in a copy",
        )



class CompatibilityAndGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-layout-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def init(self, *extra: str, name: str = "demo", subdir: str = "demo") -> Path:
        project = self.tmp / subdir
        code, out, err = run("init", "--path", str(project), "--name", name, *extra)
        self.assertEqual(code, 0, out + err)
        return project

    def check(self, project: Path) -> tuple[int, dict]:
        code, out, err = run("check", "--path", str(project), "--json")
        self.assertNotIn("Traceback", err)
        return code, json.loads(out)

    def assert_missing_contract_path(self, relative: str, expected: str) -> None:
        project = self.init(
            "--layout",
            "webapp",
            "--stack",
            "python",
            subdir=f"missing-{relative.replace('/', '-').strip('-')}",
        )
        target = project / relative
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        code, report = self.check(project)
        self.assertEqual(code, 1)
        layout = item(report, "layout")
        self.assertEqual(layout["code"], 1)
        self.assertIn("layout contract (webapp) missing:", layout["stdout"])
        self.assertIn(expected, layout["stdout"])

    def test_contract_gate_names_a_missing_spec_index(self) -> None:
        self.assert_missing_contract_path("spec/INDEX.md", "spec/INDEX.md")

    def test_contract_gate_names_a_missing_spec_domains_dir(self) -> None:
        self.assert_missing_contract_path("spec/domains", "spec/domains/")

    def test_contract_gate_names_a_missing_spec_testing(self) -> None:
        self.assert_missing_contract_path("spec/TESTING.md", "spec/TESTING.md")

    def test_contract_gate_names_a_missing_spec_constitution(self) -> None:
        self.assert_missing_contract_path(
            "spec/CONSTITUTION.md", "spec/CONSTITUTION.md"
        )

    def test_stack_none_fails_with_no_checks_configured(self) -> None:
        project = self.init("--layout", "webapp", "--stack", "none")
        self.assertFalse((project / "server").exists())
        self.assertFalse((project / "src").exists())
        code, report = self.check(project)
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        layout = item(report, "layout")
        self.assertEqual(layout["code"], 1)
        self.assertIn("no checks configured", layout["stdout"])
        self.assertEqual(
            [result["id"] for result in report["results"]],
            ["spec", "layout"],
        )

    def test_contract_gate_names_a_missing_wiki_runbook(self) -> None:
        project = self.init("--layout", "webapp", "--stack", "python")
        shutil.rmtree(project / "wiki" / "runbook")
        code, report = self.check(project)
        self.assertEqual(code, 1)
        layout = item(report, "layout")
        self.assertEqual(layout["code"], 1)
        self.assertIn("layout contract (webapp) missing:", layout["stdout"])
        self.assertIn("wiki/runbook/", layout["stdout"])

    def test_a_missing_agents_slot_is_restored_instead_of_failing_the_gate(self) -> None:
        # The slots are gitignored, so every fresh clone starts without them.
        # Failing the gate for that left an untouched scaffold red on any
        # machine but the one that ran `init`.
        project = self.init("--layout", "service", "--stack", "python")
        shutil.rmtree(project / ".agents" / "evidence")
        shutil.rmtree(project / ".agents" / "tmp")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["check", "--path", str(project), "--json"])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertTrue(json.loads(out.getvalue())["ok"])
        self.assertTrue((project / ".agents" / "evidence").is_dir())
        self.assertTrue((project / ".agents" / "tmp").is_dir())
        self.assertIn("recreated 2 missing local slot(s) under .agents/", err.getvalue())
        self.assertIn("(evidence, tmp)", err.getvalue())

        # The gate itself still names a slot that is gone.
        shutil.rmtree(project / ".agents" / "evidence")
        missing = layout_contract_problems(project, load_config(project))
        self.assertIn(".agents/evidence/", missing)

    def test_contract_gate_names_missing_gitignore_entries(self) -> None:
        project = self.init("--layout", "plugin", "--stack", "python")
        lines = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
        for missing in ("out/", ".agents/"):
            with self.subTest(missing=missing):
                (project / ".gitignore").write_text(
                    "\n".join(line for line in lines if line != missing) + "\n",
                    encoding="utf-8",
                )
                code, report = self.check(project)
                self.assertEqual(code, 1)
                self.assertIn(
                    f".gitignore must ignore {missing}",
                    item(report, "layout")["stdout"],
                )

    def test_agents_template_renders_to_dot_agents_and_git_ignores_it(self) -> None:
        project = self.init("--layout", "webapp", "--stack", "python")
        self.assertTrue((project / ".agents" / "README.md").is_file())
        leftovers = [path for path in project.rglob("*") if "__agents__" in path.parts]
        self.assertEqual(leftovers, [])

        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertNotIn(".agents", status)
        self.assertNotIn("__agents__", status)
        self.assertNotIn("out/", status)

    def test_list_layouts_is_stable_and_writes_nothing(self) -> None:
        target = self.tmp / "never-created"
        code, out, err = run("init", "--list-layouts", "--path", str(target))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, EXPECTED_LISTING)
        self.assertFalse(target.exists())
        code, again, _ = run("init", "--list-layouts")
        self.assertEqual(code, 0)
        self.assertEqual(again, out)

    def test_list_layouts_shows_the_exact_generated_check_commands(self) -> None:
        listing = layout_listing()
        self.assertNotIn("<layout shell checks>", listing)
        build_lines = {}
        for line in listing.splitlines():
            for stack in ("python", "node"):
                prefix = f"  {stack:<8} build: "
                if line.startswith(prefix):
                    build_lines[stack] = line[len(prefix):].split(" | test: ")[0]
        appends = {
            name: "".join(
                f" && {command.format(bin='<project>')}" for command in spec.shell_checks
            )
            for name, spec in LAYOUT_SPECS.items()
            if spec.shell_checks
        }
        self.assertIn("plugin appends to the build command: && bash -n bin/<project>", listing)

        for layout in ("webapp", "service", "plugin"):
            spec = LAYOUT_SPECS[layout]
            for stack in ("python", "node"):
                with self.subTest(layout=layout, stack=stack):
                    generated = checks_for(layout, stack, "demo")[0]
                    shown = (
                        build_lines[stack]
                        .replace("<layout sources>", " ".join(spec.python_roots))
                        .replace("<entry>", spec.node_entry or "")
                        + appends.get(layout, "")
                    ).replace("bin/<project>", "bin/demo")
                    self.assertEqual(shown, generated)

    def test_retro_path_comes_from_the_configured_paths(self) -> None:
        project = self.init("--layout", "webapp", "--stack", "python")
        (project / ".galadon" / "checks.json").write_text('{"ok": true}', encoding="utf-8")
        (project / "release" / "release.json").write_text('{"ok": true}', encoding="utf-8")
        retro = "# demo retro\n\n## What changed\n\nShipped.\n\n## Next\n\nNothing.\n"
        (project / "wiki" / "progress" / "retro.md").write_text(retro, encoding="utf-8")

        code, out, _ = run("next", "--path", str(project), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["phase"], "done")

        (project / "wiki" / "progress" / "retro.md").unlink()
        (project / "wiki" / "retro.md").write_text(retro, encoding="utf-8")
        code, out, _ = run("next", "--path", str(project), "--json")
        self.assertEqual(json.loads(out)["phase"], "learn")
        self.assertIn("wiki/progress/retro.md", json.loads(out)["next"])

    def test_placeholders_are_substituted_in_template_paths(self) -> None:
        project = self.init("--layout", "plugin", "--stack", "python", name="Demo App 2")
        self.assertTrue((project / "src" / "demo_app_2" / "cli.py").is_file())
        self.assertTrue((project / "bin" / "demo-app-2").is_file())
        self.assertTrue((project / "skills" / "demo-app-2" / "SKILL.md").is_file())
        self.assertFalse((project / "src" / "{{PACKAGE}}").exists())
        code, report = self.check(project)
        self.assertEqual(code, 0, json.dumps(report)[:500])
        self.assertTrue(report["ok"])

    def test_init_rejects_a_name_with_a_path_separator(self) -> None:
        code, _, err = run(
            "init",
            "--path",
            str(self.tmp / "bad"),
            "--name",
            "a/b",
            "--layout",
            "webapp",
        )
        self.assertEqual(code, 2)
        self.assertIn("path separator", err)


if __name__ == "__main__":
    unittest.main()
