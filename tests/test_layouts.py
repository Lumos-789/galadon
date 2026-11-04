from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.cli import main  # noqa: E402

AGENT_SLOTS = ("plans", "evidence", "archive", "skills", "tmp", "wiki")

EXPECTED_LISTING = """\
layouts (galadon init --layout):
  none     base template only (default); no layout contract
  webapp   server/ web/ e2e/ tests/ scripts/ deploy/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/
  service  server/ tests/ scripts/ deploy/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/
  plugin   bin/<project> src/<project>/ skills/<project>/ docs/ tests/ scripts/ + openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/

stacks (galadon init --stack):
  python   build: python3 -m compileall -f -q <layout sources> && <layout shell checks> | test: python3 -m unittest discover -s tests -v
  node     build: node --check <entry> && <layout shell checks> | test: npm test (package.json scripts.test = node --test tests/)
  none     no checks: galadon check fails with "no checks configured — declare checks.build/checks.test"
"""


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def item(report: dict, item_id: str) -> dict:
    return next(result for result in report["results"] if result["id"] == item_id)


class LayoutTestCase(unittest.TestCase):
    layout = "none"
    stack = "none"
    name = "demo"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-layout-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "demo"
        code, out, err = run(
            "init",
            "--path",
            str(self.project),
            "--name",
            self.name,
            "--layout",
            self.layout,
            "--stack",
            self.stack,
        )
        self.assertEqual(code, 0, out + err)

    def config(self) -> dict:
        return yaml.safe_load((self.project / "galadon.yaml").read_text(encoding="utf-8"))

    def check(self) -> tuple[int, dict, str]:
        code, out, err = run("check", "--path", str(self.project), "--json")
        self.assertNotIn("Traceback", err)
        return code, json.loads(out), err

    def assert_layout_contract_passes(self, report: dict) -> None:
        layout = item(report, "layout")
        self.assertEqual(layout["code"], 0, layout["stdout"])
        self.assertIn("is complete", layout["stdout"])
        for path in ("AGENTS.md", "openspec/config.yaml", "spec/spec.md"):
            self.assertTrue((self.project / path).is_file(), path)
        for name in AGENT_SLOTS:
            self.assertTrue((self.project / ".agents" / name).is_dir(), name)
        ignore = (self.project / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("out/", ignore)
        self.assertIn(".agents/", ignore)

    def source_files(self, *roots: str) -> list[str]:
        files: list[str] = []
        for root in roots:
            files.extend(
                path.relative_to(self.project).as_posix()
                for path in sorted((self.project / root).rglob("*.py"))
            )
        return files

    def assert_broken_file_fails(self, relative: str, gate: str, snippet: str) -> None:
        path = self.project / relative
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + snippet, encoding="utf-8")
            code, report, err = self.check()
            self.assertEqual(code, 1, f"{relative}: {err}")
            result = item(report, gate)
            self.assertNotEqual(result["code"], 0, f"{relative}: {result}")
            output = result["stdout"] + result["stderr"]
            self.assertIn(relative, output, f"{relative}: {output}")
        finally:
            path.write_text(original, encoding="utf-8")


class PythonLayoutTests(LayoutTestCase):
    stack = "python"
    python_roots = ("src", "server")

    def assert_python_test_ran(self, report: dict) -> None:
        test = item(report, "test")
        self.assertEqual(test["code"], 0, test["stdout"] + test["stderr"])
        self.assertIn("Ran 2 tests", test["stderr"])
        self.assertIn("OK", test["stderr"])

    def assert_python_gates_cover_every_file(self) -> None:
        for relative in self.source_files(*self.python_roots):
            with self.subTest(gate="build", file=relative):
                self.assert_broken_file_fails(relative, "build", "\ndef broken(:\n")
        for relative in self.source_files("tests"):
            with self.subTest(gate="test", file=relative):
                self.assert_broken_file_fails(relative, "test", "\ndef broken(:\n")


class WebappPythonTests(PythonLayoutTests):
    layout = "webapp"

    def test_webapp_python_ships_the_layers_and_a_green_check(self) -> None:
        for path in (
            "server/app.py",
            "src/demo/app.py",
            "tests/test_app.py",
            "web/README.md",
            "e2e/README.md",
            "scripts/dev.sh",
            "deploy/README.md",
            "config.md",
            "wiki/knowledge/README.md",
            "wiki/progress/README.md",
            "wiki/runbook/README.md",
        ):
            self.assertTrue((self.project / path).exists(), path)
        self.assertFalse((self.project / "wiki" / "retro.md").exists())

        config = self.config()
        self.assertEqual(config["layout"], "webapp")
        self.assertEqual(config["stack"], "python")
        self.assertEqual(config["checks"]["build"], "python3 -m compileall -f -q src server")
        self.assertEqual(
            config["checks"]["test"],
            "python3 -m unittest discover -s tests -v",
        )
        self.assertEqual(config["paths"]["retro"], "wiki/progress/retro.md")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_python_test_ran(report)

    def test_webapp_python_build_checks_every_source_file(self) -> None:
        self.assert_python_gates_cover_every_file()


class ServicePythonTests(PythonLayoutTests):
    layout = "service"

    def test_service_python_ships_the_layers_and_a_green_check(self) -> None:
        for path in (
            "server/app.py",
            "src/demo/app.py",
            "tests/test_app.py",
            "scripts/dev.sh",
            "deploy/README.md",
            "config.md",
        ):
            self.assertTrue((self.project / path).exists(), path)
        self.assertFalse((self.project / "web").exists())
        self.assertFalse((self.project / "e2e").exists())

        config = self.config()
        self.assertEqual(config["stack"], "python")
        self.assertEqual(config["checks"]["build"], "python3 -m compileall -f -q src server")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_python_test_ran(report)

    def test_service_python_build_checks_every_source_file(self) -> None:
        self.assert_python_gates_cover_every_file()


class PluginPythonTests(PythonLayoutTests):
    layout = "plugin"
    python_roots = ("src",)

    def test_plugin_python_ships_the_layers_and_a_green_check(self) -> None:
        for path in (
            "bin/demo",
            "src/demo/cli.py",
            "src/demo/__main__.py",
            "skills/demo/SKILL.md",
            "docs/packaging.md",
            "tests/test_cli.py",
            "config.md",
        ):
            self.assertTrue((self.project / path).exists(), path)
        self.assertTrue(os.access(self.project / "bin" / "demo", os.X_OK))
        self.assertFalse((self.project / ".zcode-plugin").exists())
        self.assertFalse((self.project / ".codex-plugin").exists())

        config = self.config()
        self.assertEqual(config["layout"], "plugin")
        self.assertEqual(config["checks"]["build"], "python3 -m compileall -f -q src && bash -n bin/demo")
        self.assertEqual(config["checks"]["test"], "python3 -m unittest discover -s tests -v")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_python_test_ran(report)

    def test_plugin_python_build_checks_every_source_file(self) -> None:
        self.assert_python_gates_cover_every_file()

    def test_plugin_python_build_checks_the_shell_wrapper(self) -> None:
        self.assert_broken_file_fails("bin/demo", "build", "\ndef broken(:\n")


class NodeLayoutTests(LayoutTestCase):
    stack = "node"
    node_entry = "server/index.js"

    def assert_node_test_ran(self, report: dict) -> None:
        test = item(report, "test")
        self.assertEqual(test["code"], 0, test["stdout"] + test["stderr"])
        self.assertRegex(test["stdout"], r"pass [1-9]\d*")
        self.assertNotIn("pass 0", test["stdout"])

    def assert_node_build_checks_entry(self) -> None:
        self.assert_broken_file_fails(self.node_entry, "build", "\nconst broken = ;\n")


class WebappNodeTests(NodeLayoutTests):
    layout = "webapp"

    def test_webapp_node_ships_the_package_and_a_green_check(self) -> None:
        self.assertTrue((self.project / "server" / "index.js").is_file())
        self.assertTrue((self.project / "tests" / "health.test.js").is_file())
        package = json.loads((self.project / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["test"], "node --test tests/")
        self.assertEqual(package["type"], "module")

        config = self.config()
        self.assertEqual(config["checks"]["build"], "node --check server/index.js")
        self.assertEqual(config["checks"]["test"], "npm test")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_node_test_ran(report)

    def test_webapp_node_build_checks_the_entry(self) -> None:
        self.assert_node_build_checks_entry()


class ServiceNodeTests(NodeLayoutTests):
    layout = "service"

    def test_service_node_ships_the_package_and_a_green_check(self) -> None:
        self.assertTrue((self.project / "server" / "index.js").is_file())
        self.assertFalse((self.project / "web").exists())
        package = json.loads((self.project / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["test"], "node --test tests/")

        config = self.config()
        self.assertEqual(config["checks"]["build"], "node --check server/index.js")
        self.assertEqual(config["checks"]["test"], "npm test")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_node_test_ran(report)

    def test_service_node_build_checks_the_entry(self) -> None:
        self.assert_node_build_checks_entry()


class PluginNodeTests(NodeLayoutTests):
    layout = "plugin"
    node_entry = "src/index.js"

    def test_plugin_node_ships_the_package_and_a_green_check(self) -> None:
        self.assertTrue((self.project / "src" / "index.js").is_file())
        self.assertTrue((self.project / "bin" / "demo").is_file())
        self.assertTrue((self.project / "skills" / "demo" / "SKILL.md").is_file())
        package = json.loads((self.project / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["test"], "node --test tests/")
        self.assertEqual(package["bin"]["demo"], "bin/demo")

        config = self.config()
        self.assertEqual(
            config["checks"]["build"],
            "node --check src/index.js && bash -n bin/demo",
        )
        self.assertEqual(config["checks"]["test"], "npm test")

        code, report, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(report["ok"])
        self.assert_layout_contract_passes(report)
        self.assert_node_test_ran(report)

    def test_plugin_node_build_checks_the_entry(self) -> None:
        self.assert_node_build_checks_entry()

    def test_plugin_node_build_checks_the_shell_wrapper(self) -> None:
        self.assert_broken_file_fails("bin/demo", "build", "\ndef broken(:\n")


class NoneLayoutTests(LayoutTestCase):
    layout = "none"
    stack = "none"

    def test_layout_none_keeps_the_022_shape(self) -> None:
        directories = {path.name for path in self.project.iterdir() if path.is_dir()}
        self.assertEqual(
            directories,
            {
                ".galadon",
                "deploy",
                "docs",
                "release",
                "scripts",
                "spec",
                "src",
                "verify",
                "wiki",
            },
        )
        for absent in (".agents", "openspec", "out", "server"):
            self.assertFalse((self.project / absent).exists(), absent)
        self.assertTrue((self.project / "wiki" / "retro.md").is_file())
        self.assertTrue((self.project / ".gitignore").is_file())
        self.assertFalse((self.project / "wiki" / "knowledge").exists())

        config = self.config()
        self.assertEqual(config["layout"], "none")
        self.assertEqual(config["stack"], "none")
        self.assertEqual(config["checks"]["build"], "")
        self.assertEqual(config["checks"]["test"], "")
        self.assertEqual(
            config["paths"],
            {
                "spec": "spec/spec.md",
                "evidence": "verify/evidence.md",
                "retro": "wiki/retro.md",
                "release": "release/release.json",
            },
        )

        code, report, err = self.check()
        self.assertEqual(code, 1, err)
        self.assertFalse(report["ok"])
        self.assertEqual([result["id"] for result in report["results"]], ["spec"])

    def test_layout_none_ignores_a_stack_choice(self) -> None:
        other = self.tmp / "none-with-node"
        code, out, err = run(
            "init",
            "--path",
            str(other),
            "--name",
            "demo",
            "--layout",
            "none",
            "--stack",
            "node",
        )
        self.assertEqual(code, 0, out + err)
        self.assertFalse((other / "package.json").exists())
        data = yaml.safe_load((other / "galadon.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["stack"], "none")


class CompatibilityAndGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-layout-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def init(self, *extra: str, name: str = "demo") -> Path:
        project = self.tmp / "demo"
        code, out, err = run("init", "--path", str(project), "--name", name, *extra)
        self.assertEqual(code, 0, out + err)
        return project

    def check(self, project: Path) -> tuple[int, dict]:
        code, out, err = run("check", "--path", str(project), "--json")
        self.assertNotIn("Traceback", err)
        return code, json.loads(out)

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

    def test_contract_gate_names_a_missing_agents_evidence_slot(self) -> None:
        project = self.init("--layout", "service", "--stack", "python")
        shutil.rmtree(project / ".agents" / "evidence")
        code, report = self.check(project)
        self.assertEqual(code, 1)
        layout = item(report, "layout")
        self.assertIn(".agents/evidence/", layout["stdout"])

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
