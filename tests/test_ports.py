"""`ports:` — the project's own role policy, template root and deploy adapter."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from galadon.config import ConfigError, load_config  # noqa: E402
from helpers import run  # noqa: E402

SPEC = "# spec\n\n## Problem\n\np\n\n## Acceptance\n\n- [x] a\n\n## Non-goals\n\nn\n"

HELPER = "def name(ctx):\n    return ctx.config.name\n"

ADAPTER = textwrap.dedent(
    """
    from galadon.ports import Artifact, ProbeResult, Release
    import helper_module


    class FileAdapter:
        name = "file"

        def describe(self, ctx):
            return "file://" + helper_module.name(ctx)

        def bootstrap(self, ctx):
            raise RuntimeError("nothing to provision")

        def teardown(self, ctx):
            raise RuntimeError("nothing to remove")

        def build(self, ctx):
            return Artifact(path=ctx.root / "spec" / "spec.md", digest="d" * 64)

        def migrate(self, ctx):
            (ctx.root / "ports-migrate.txt").write_text("migrated")

        def release(self, ctx, artifact):
            return Release(env=ctx.env, artifact=artifact, adapter=self.name,
                           metadata={"commit": ctx.commit or "unversioned"})

        def probe(self, ctx, release):
            return ProbeResult(ok=True, detail="the port probed", actual="port-actual")

        def rollback(self, ctx, release):
            (ctx.root / "ports-rollback.txt").write_text("rolled back")
    """
)

ROLE = "class Pair:\n    def assign(self, phase):\n        return ['driver', 'navigator']\n"


def port_project(path: Path, *, ports: dict, files: dict[str, str] | None = None) -> Path:
    """A project whose `galadon.yaml` wires `ports:` to modules beside it."""
    if True:
        path.mkdir(parents=True)
        (path / "spec").mkdir()
        (path / "spec" / "spec.md").write_text(SPEC, encoding="utf-8")
        for name, text in {"helper_module.py": HELPER, "demo_ports.py": ADAPTER, **(files or {})}.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text, encoding="utf-8")
        (path / "galadon.yaml").write_text(
            yaml.safe_dump(
                {
                    "project": path.name,
                    "template": "web",
                    "role": "solo",
                    "ports": ports,
                    "deploy": {"adapter": "file" if "deploy" in ports else "local", "env": "test"},
                    "checks": {"build": "true", "test": "true", "timeout": 30},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path


class DeployPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-ports-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_project_own_adapter_carries_the_ship(self) -> None:
        project = port_project(self.tmp / "demo", ports={"deploy": "demo_ports:FileAdapter"})

        code, out, err = run("ship", "--path", str(project), "--env", "test", "--dry-run", "--json")

        self.assertEqual(code, 0, out + err)
        plan = json.loads(out)
        self.assertEqual((plan["adapter"], plan["target"]), ("file", "file://demo"))

        code, out, err = run("ship", "--path", str(project), "--env", "test", "--json")

        self.assertEqual(code, 0, out + err)
        released = json.loads(out)["release"]
        self.assertEqual(released["adapter"], "file")
        self.assertEqual(released["probe"]["detail"], "the port probed")
        self.assertEqual(released["actual_commit"], "port-actual")
        self.assertEqual((project / "ports-migrate.txt").read_text(encoding="utf-8"), "migrated")
        self.assertFalse((project / "ports-rollback.txt").exists())

    def test_a_port_must_implement_the_whole_deploy_contract(self) -> None:
        for port, expected in (
            ("class Half:\n    name = 'half'\n", "does not implement the DeployPort; missing: describe"),
            (ADAPTER.replace('    name = "file"\n\n', ""), "must carry a `name` string"),
        ):
            project = port_project(
                self.tmp / ("half" if "class Half" in port else "nameless"),
                ports={"deploy": "demo_ports:Half"},
                files={"demo_ports.py": port.replace("FileAdapter", "Half")},
            )
            with self.subTest(port=port.splitlines()[0].strip()[:20]):
                code, _out, err = run("ship", "--path", str(project), "--env", "test")
                self.assertEqual(code, 2)
                self.assertIn(expected, err)

    def test_import_errors_name_the_port_and_the_cause(self) -> None:
        project = port_project(
            self.tmp / "broken",
            ports={"deploy": "demo_ports:Missing"},
            files={"demo_ports.py": "raise RuntimeError('the port itself is broken')\n"},
        )
        code, _out, err = run("ship", "--path", str(project), "--env", "test")
        self.assertEqual(code, 2)
        self.assertIn("ports.deploy 'demo_ports:Missing' failed to import", err)
        self.assertIn("RuntimeError: the port itself is broken", err)
        self.assertNotIn("Traceback", err)

        project = port_project(self.tmp / "absent", ports={"deploy": "no_such_module:Thing"})
        code, _out, err = run("ship", "--path", str(project), "--env", "test")
        self.assertEqual(code, 2)
        self.assertIn("no_such_module is not a module inside", err)

        project = port_project(self.tmp / "attr", ports={"deploy": "demo_ports:NotThere"})
        code, _out, err = run("ship", "--path", str(project), "--env", "test")
        self.assertEqual(code, 2)
        self.assertIn("demo_ports has no NotThere", err)

    def test_two_projects_with_the_same_module_name_do_not_share_a_port(self) -> None:
        # A cached module would hand the second project the first one's code.
        first = port_project(self.tmp / "one", ports={"deploy": "demo_ports:FileAdapter"})
        second = port_project(
            self.tmp / "two",
            ports={"deploy": "demo_ports:FileAdapter"},
            files={"helper_module.py": "def name(ctx):\n    return 'the other project'\n"},
        )

        for project, target in ((first, "file://one"), (second, "file://the other project")):
            code, out, err = run("ship", "--path", str(project), "--env", "test", "--dry-run", "--json")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(json.loads(out)["target"], target)

    def test_a_changed_port_is_read_from_disk_again(self) -> None:
        project = port_project(self.tmp / "reload", ports={"deploy": "demo_ports:FileAdapter"})
        code, out, _ = run("ship", "--path", str(project), "--env", "test", "--dry-run", "--json")
        self.assertEqual(json.loads(out)["adapter"], "file")

        (project / "demo_ports.py").write_text(
            ADAPTER.replace('name = "file"', 'name = "file-v2"'), encoding="utf-8"
        )

        code, out, _ = run("ship", "--path", str(project), "--env", "test", "--dry-run", "--json")
        self.assertEqual(json.loads(out)["adapter"], "file-v2")

    def test_a_port_inside_a_package_may_use_relative_imports(self) -> None:
        packaged = textwrap.dedent(
            """
            from galadon.ports import Artifact, ProbeResult, Release
            from .helpers import LABEL


            class Packaged:
                name = "packaged"

                def describe(self, ctx):
                    return "pkg://" + LABEL

                def bootstrap(self, ctx):
                    pass

                def teardown(self, ctx):
                    pass

                def build(self, ctx):
                    return Artifact(path=ctx.root / "spec" / "spec.md", digest="d" * 64)

                def migrate(self, ctx):
                    pass

                def release(self, ctx, artifact):
                    return Release(env=ctx.env, artifact=artifact, adapter=self.name, metadata={})

                def probe(self, ctx, release):
                    return ProbeResult(ok=True, detail=LABEL)

                def rollback(self, ctx, release):
                    pass
            """
        )
        project = port_project(
            self.tmp / "pkg",
            ports={"deploy": "team_ports.adapter:Packaged"},
            files={
                "team_ports/__init__.py": "",
                "team_ports/helpers.py": "LABEL = 'from the package'\n",
                "team_ports/adapter.py": packaged,
            },
        )

        code, out, err = run("ship", "--path", str(project), "--env", "test", "--dry-run", "--json")

        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["target"], "pkg://from the package")

    def test_a_reference_must_be_module_and_attribute(self) -> None:
        project = port_project(self.tmp / "shape", ports={"deploy": "demo_ports"})
        code, _out, err = run("next", "--path", str(project))
        self.assertEqual(code, 2)
        self.assertIn("ports.deploy must point at code inside the project as module:Attribute", err)


class RolePortTests(unittest.TestCase):
    def test_the_project_own_policy_names_the_roles(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-role-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = port_project(
            tmp / "team", ports={"role": "team_roles:Pair"}, files={"team_roles.py": ROLE}
        )
        (project / "galadon.yaml").write_text(
            (project / "galadon.yaml").read_text(encoding="utf-8").replace("role: solo", "role: pair"),
            encoding="utf-8",
        )

        code, out, err = run("next", "--path", str(project), "--json")

        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["role"], "driver + navigator")
        self.assertEqual(load_config(project).role, "pair")

    def test_a_role_port_without_assign_is_refused(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-role-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = port_project(
            tmp / "silent",
            ports={"role": "team_roles:Pair"},
            files={"team_roles.py": "class Pair:\n    pass\n"},
        )
        code, _out, err = run("next", "--path", str(project))
        self.assertEqual(code, 2)
        self.assertIn("does not implement the RolePort", err)


class TemplatePortTests(unittest.TestCase):
    def test_a_project_template_root_supplies_the_profile(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-template-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        custom = tmp / "scaffold"
        (custom / "base").mkdir(parents=True)
        (custom / "base" / "README.md").write_text("custom base\n", encoding="utf-8")
        (custom / "profiles" / "shop" / "docs").mkdir(parents=True)
        (custom / "profiles" / "shop" / "docs" / "profile.md").write_text("shop profile\n", encoding="utf-8")
        project = tmp / "shop"
        project.mkdir()
        (project / "galadon.yaml").write_text(
            yaml.safe_dump(
                {
                    "project": "shop",
                    "template": "shop",
                    "role": "solo",
                    "ports": {"template": str(custom)},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        code, out, err = run("init", "--path", str(project), "--force", "--template", "shop")

        self.assertEqual(code, 0, out + err)
        self.assertEqual((project / "README.md").read_text(encoding="utf-8"), "custom base\n")
        self.assertEqual((project / "docs" / "profile.md").read_text(encoding="utf-8"), "shop profile\n")
        self.assertEqual(load_config(project).template, "shop")

    def test_a_profile_the_root_does_not_have_fails_loud(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-template-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        custom = tmp / "scaffold"
        (custom / "base").mkdir(parents=True)
        (custom / "profiles" / "shop").mkdir(parents=True)
        project = tmp / "shop"
        project.mkdir()
        (project / "galadon.yaml").write_text(
            f"project: shop\ntemplate: shop\nports:\n  template: {custom}\n", encoding="utf-8"
        )

        code, _out, err = run("init", "--path", str(project), "--force", "--template", "boutique")

        self.assertEqual(code, 2)
        self.assertIn("unknown template profile: boutique", err)

    def test_a_template_root_without_a_base_is_refused_at_load(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="galadon-template-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        project = tmp / "orphan"
        project.mkdir()
        (project / "galadon.yaml").write_text(
            f"project: orphan\ntemplate: web\nports:\n  template: {tmp}\n", encoding="utf-8"
        )
        with self.assertRaises(ConfigError):
            load_config(project)


if __name__ == "__main__":
    unittest.main()
