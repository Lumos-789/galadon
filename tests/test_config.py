from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.config import (  # noqa: E402
    DEFAULT_PATHS,
    ConfigError,
    load_config,
    render_probe_expect,
)


BASE = {
    "project": "demo",
    "template": "web",
    "role": "solo",
    "deploy": {
        "adapter": "local",
        "env": "test",
        "build": "true",
        "migrate": "",
        "release": "true",
        "probe_url": "",
        "probe_expect": "",
        "artifact": "",
    },
    "checks": {"build": "true", "test": "true", "timeout": 300},
}


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="galadon-config-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, data: dict) -> Path:
        (self.tmp / "galadon.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )
        return self.tmp

    def test_local_config_keeps_working(self) -> None:
        config = load_config(self.write(BASE))
        self.assertEqual(config.deploy.adapter, "local")
        self.assertEqual(config.deploy.options, {})

    def test_unknown_deploy_keys_are_preserved_in_options(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["host"] = "203.0.113.10"
        data["deploy"]["nested"] = {"keep": [1, 2]}
        config = load_config(self.write(data))
        self.assertEqual(config.deploy.options["host"], "203.0.113.10")
        self.assertEqual(config.deploy.options["nested"], {"keep": [1, 2]})

    def test_unknown_top_level_keys_fail_loud(self) -> None:
        # `tools:` was the 0.1.0 ToolPort block. The port is gone, so a
        # leftover block must be reported instead of silently ignored.
        data = json.loads(json.dumps(BASE))
        data["tools"] = {"issue": "local-md", "ci": "none"}
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data))
        message = str(caught.exception)
        self.assertIn("unknown top-level keys: tools", message)
        self.assertIn("allowed:", message)
        for allowed in ("project", "deploy", "checks", "paths"):
            self.assertIn(allowed, message)

    def test_unknown_top_level_keys_are_listed_sorted(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["zeta"] = 1
        data["alpha"] = {"nested": True}
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data))
        self.assertIn("unknown top-level keys: alpha, zeta", str(caught.exception))

    def test_explicit_options_are_preserved(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["options"] = {"custom": {"flag": True}}
        config = load_config(self.write(data))
        self.assertEqual(config.deploy.options, {"custom": {"flag": True}})

    def test_invalid_options_mapping_fails_loud(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["options"] = []
        with self.assertRaises(ConfigError):
            load_config(self.write(data))

    def test_ssh_missing_fields_are_reported_once(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["adapter"] = "ssh"
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data))
        message = str(caught.exception)
        for field in ("host", "user", "remote_root", "service", "artifact"):
            self.assertIn(field, message)
        self.assertIn("missing: host, user, remote_root, service, artifact", message)

    def test_ssh_valid_config_and_unknown_options(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["adapter"] = "ssh"
        data["deploy"]["artifact"] = "dist"
        data["deploy"]["options"] = {
            "host": "203.0.113.10",
            "user": "root",
            "remote_root": "/srv/demo",
            "service": "demo",
            "domain": "demo.example.com",
        }
        config = load_config(self.write(data))
        self.assertEqual(config.deploy.options["service"], "demo")
        self.assertEqual(config.deploy.options["domain"], "demo.example.com")

    def test_ssh_remote_root_rejects_shell_metacharacters(self) -> None:
        for bad in ("/srv/demo$(id)", "/srv/demo;id", "/srv/demo\nExecStartPre=/bin/true"):
            data = json.loads(json.dumps(BASE))
            data["deploy"]["adapter"] = "ssh"
            data["deploy"]["artifact"] = "dist"
            data["deploy"]["options"] = {
                "host": "203.0.113.10",
                "user": "root",
                "remote_root": bad,
                "service": "demo",
            }
            with self.subTest(remote_root=bad):
                with self.assertRaises(ConfigError):
                    load_config(self.write(data))

    def test_missing_checks_default_to_fail_loud(self) -> None:
        data = json.loads(json.dumps(BASE))
        del data["checks"]
        config = load_config(self.write(data))
        self.assertEqual(config.checks.build, "")
        self.assertEqual(config.checks.test, "")

    def test_probe_commit_placeholder(self) -> None:
        self.assertEqual(render_probe_expect("{{COMMIT}}", "abc1234"), "abc1234")
        self.assertEqual(render_probe_expect("plain", None), "plain")
        with self.assertRaises(ConfigError):
            render_probe_expect("{{COMMIT}}", None)

    def test_a_key_left_empty_means_not_configured(self) -> None:
        # `probe_url:` with nothing after it used to be read as the string
        # "None" and requested as a URL once the release was already live.
        data = json.loads(json.dumps(BASE))
        data["deploy"].update(
            {"build": None, "migrate": None, "release": None, "probe_url": None,
             "probe_expect": None, "artifact": None}
        )
        deploy = load_config(self.write(data)).deploy
        for field in ("build", "migrate", "release", "probe_url", "probe_expect", "artifact"):
            self.assertEqual(getattr(deploy, field), "", field)

    def test_a_deploy_field_must_be_a_string(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["probe_url"] = 123
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data))
        self.assertIn("deploy.probe_url must be a string", str(caught.exception))

    def test_timeout_must_be_a_positive_whole_number(self) -> None:
        for bad in ("abc", -5, 0, "300", 2.5, True, None):
            data = json.loads(json.dumps(BASE))
            data["checks"]["timeout"] = bad
            with self.subTest(timeout=bad):
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.write(data))
                self.assertIn("checks.timeout", str(caught.exception))

    def test_keys_left_empty_are_not_read_as_the_string_none(self) -> None:
        for key, message in (
            ("project", "project is required"),
            ("template", "template must be web, api or static"),
            ("role", "role must be solo, six-ring or orchestrator"),
            ("layout", "layout must be none, webapp, service, plugin"),
            ("stack", "stack must be python, node, none"),
        ):
            data = json.loads(json.dumps(BASE))
            data[key] = None
            with self.subTest(key=key):
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.write(data))
                self.assertIn(message, str(caught.exception))

    def test_an_empty_string_keeps_the_default_layout_and_stack(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["layout"] = ""
        data["stack"] = ""
        config = load_config(self.write(data))
        self.assertEqual((config.layout, config.stack), ("none", "none"))

    def test_envs_override_the_shared_deploy_block(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"].update(
            {
                "adapter": "ssh",
                "artifact": "dist",
                "probe_expect": "{{COMMIT}}",
                "options": {"service": "demo", "user": "root", "run_user": "demo"},
                "envs": {
                    "test": {
                        "options": {"host": "203.0.113.10", "remote_root": "/srv/demo"},
                        "probe_url": "https://test.example.com/health.json",
                    },
                    "prod": {
                        "options": {"host": "203.0.113.11", "remote_root": "/srv/demo-prod"},
                        "probe_url": "https://demo.example.com/health.json",
                    },
                },
            }
        )
        root = self.write(data)
        test = load_config(root).deploy
        prod = load_config(root, env="prod").deploy

        self.assertEqual(test.env, "test")
        self.assertEqual(test.options["host"], "203.0.113.10")
        self.assertEqual(test.options["service"], "demo")
        self.assertEqual(test.options["run_user"], "demo")
        self.assertEqual(test.probe_url, "https://test.example.com/health.json")
        self.assertEqual(test.probe_expect, "{{COMMIT}}")
        self.assertFalse(test.protected)

        self.assertEqual(prod.env, "prod")
        self.assertEqual(prod.options["host"], "203.0.113.11")
        self.assertEqual(prod.options["remote_root"], "/srv/demo-prod")
        self.assertEqual(prod.options["service"], "demo")
        self.assertEqual(prod.probe_url, "https://demo.example.com/health.json")
        self.assertTrue(prod.protected)
        self.assertEqual(prod.envs, ("test", "prod"))

    def test_an_undefined_environment_is_refused(self) -> None:
        data = json.loads(json.dumps(BASE))
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data), env="prod")
        message = str(caught.exception)
        self.assertIn("defines no deploy.envs.prod", message)
        self.assertIn("target test", message)

    def test_the_configured_environment_is_enough_without_envs(self) -> None:
        deploy = load_config(self.write(BASE), env="test").deploy
        self.assertEqual(deploy.env, "test")

    def test_env_errors_name_the_environment(self) -> None:
        base = json.loads(json.dumps(BASE))
        base["deploy"]["envs"] = {"prod": {"host": "nowhere"}}
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(base))
        self.assertIn("deploy.envs.prod has unknown keys: host", str(caught.exception))

        base = json.loads(json.dumps(BASE))
        base["deploy"]["envs"] = {"prod": "not-a-mapping"}
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(base))
        self.assertIn("deploy.envs.prod must be a mapping", str(caught.exception))

        base = json.loads(json.dumps(BASE))
        base["deploy"]["adapter"] = "ssh"
        base["deploy"]["artifact"] = "dist"
        base["deploy"]["options"] = {"host": "h", "user": "root", "remote_root": "/srv/demo", "service": "demo"}
        base["deploy"]["envs"] = {"prod": {"options": {"remote_root": None}}}
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(base))
        self.assertIn("deploy.envs.prod", str(caught.exception))
        self.assertIn("remote_root", str(caught.exception))

    def test_protection_is_heuristic_unless_declared(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["envs"] = {
            "production": {},
            "prd": {"protected": True},
            "prod-eu": {},
            "prodlike-sandbox": {"protected": False},
            "staging": {},
        }
        root = self.write(data)
        expected = {
            "production": True,
            "prd": True,
            "prod-eu": True,
            "prodlike-sandbox": False,
            "staging": False,
        }
        for env, protected in expected.items():
            with self.subTest(env=env):
                self.assertEqual(load_config(root, env=env).deploy.protected, protected)

        data["deploy"]["envs"]["prd"]["protected"] = "yes"
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data), env="prd")
        self.assertIn("protected must be true or false", str(caught.exception))

    def test_typed_options_are_checked_at_load(self) -> None:
        bad = {
            "probe_ready_seconds": ("15s", -1, True),
            "keep_releases": ("5", -1, 2.5),
            "search_index": ("true", 1),
            "environment_file": (123,),
            "remote_migrate": (["a", "b"],),
        }
        for key, values in bad.items():
            for value in values:
                data = json.loads(json.dumps(BASE))
                data["deploy"]["options"] = {key: value}
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ConfigError) as caught:
                        load_config(self.write(data))
                    self.assertIn(f"deploy.options.{key}", str(caught.exception))

    def test_environment_file_must_be_a_plain_absolute_path(self) -> None:
        for bad in ("srv/demo/env", "/srv/demo env", "/srv/demo/$(id)", "/srv/demo\nX=y"):
            data = json.loads(json.dumps(BASE))
            data["deploy"]["options"] = {"environment_file": bad}
            with self.subTest(environment_file=bad):
                with self.assertRaises(ConfigError):
                    load_config(self.write(data))

    def test_valid_typed_options_pass_through(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["deploy"]["options"] = {
            "probe_ready_seconds": 30,
            "keep_releases": 0,
            "search_index": True,
            "environment_file": "/srv/demo/shared/env",
            "remote_migrate": "python -m migrate",
        }
        self.assertEqual(load_config(self.write(data)).deploy.options["keep_releases"], 0)

    def test_ports_are_validated_at_load(self) -> None:
        def with_ports(ports: object) -> dict:
            data = json.loads(json.dumps(BASE))
            data["ports"] = ports
            return data

        for ports, message in (
            ({"nope": "x:Y"}, "ports has unknown keys: nope"),
            ({"deploy": "galadon"}, "ports.deploy must point at code"),
            ({"deploy": "pkg.mod:Deploy", "role": 5}, "ports.role must be a string"),
            ({"template": "/nowhere"}, "ports.template must be a template directory"),
            ("x:Y", "ports must be a mapping"),
        ):
            with self.subTest(ports=ports):
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.write(with_ports(ports)))
                self.assertIn(message, str(caught.exception))

        config = load_config(self.write(with_ports({"role": "team.roles:Pair", "deploy": "team.deploy:Nomad"})))
        self.assertEqual(config.ports.role, "team.roles:Pair")
        self.assertEqual(config.ports.deploy, "team.deploy:Nomad")
        # A project that ships its own DeployPort still names deploy.adapter,
        # but the name is its own to choose.
        self.assertEqual(config.deploy.adapter, "local")

    def test_a_custom_deploy_port_may_name_the_adapter(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["ports"] = {"deploy": "team.deploy:Nomad"}
        data["deploy"]["adapter"] = "nomad"
        self.assertEqual(load_config(self.write(data)).deploy.adapter, "nomad")
        data["deploy"]["adapter"] = "not a name!"
        with self.assertRaises(ConfigError):
            load_config(self.write(data))

    def test_a_custom_role_may_name_the_role(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["ports"] = {"role": "team.roles:Pair"}
        data["role"] = "pair"
        self.assertEqual(load_config(self.write(data)).role, "pair")
        data["role"] = None
        with self.assertRaises(ConfigError):
            load_config(self.write(data))

    def test_a_custom_template_root_names_its_own_profiles(self) -> None:
        root = self.tmp / "custom"
        (root / "templates" / "base").mkdir(parents=True)
        (root / "templates" / "profiles" / "shop").mkdir(parents=True)
        data = json.loads(json.dumps(BASE))
        data["ports"] = {"template": str(root / "templates")}
        data["template"] = "shop"
        config = load_config(self.write(data))
        self.assertEqual(config.template, "shop")
        self.assertEqual(config.ports.template, str(root / "templates"))

        data["template"] = "nope"
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(data))
        self.assertIn("is not a profile under", str(caught.exception))

    def test_layout_and_paths_default_to_the_022_values(self) -> None:
        config = load_config(self.write(BASE))
        self.assertEqual(config.layout, "none")
        self.assertEqual(config.stack, "none")
        self.assertEqual(config.paths.spec, Path(DEFAULT_PATHS["spec"]))
        self.assertEqual(config.paths.evidence, Path(DEFAULT_PATHS["evidence"]))
        self.assertEqual(config.paths.retro, Path(DEFAULT_PATHS["retro"]))
        self.assertEqual(config.paths.release, Path(DEFAULT_PATHS["release"]))

    def test_layout_stack_and_paths_are_read(self) -> None:
        data = json.loads(json.dumps(BASE))
        data["layout"] = "webapp"
        data["stack"] = "node"
        data["paths"] = {"retro": "wiki/progress/retro.md", "spec": "docs/spec.md"}
        config = load_config(self.write(data))
        self.assertEqual(config.layout, "webapp")
        self.assertEqual(config.stack, "node")
        self.assertEqual(config.paths.retro, Path("wiki/progress/retro.md"))
        self.assertEqual(config.paths.spec, Path("docs/spec.md"))
        self.assertEqual(config.paths.evidence, Path("verify/evidence.md"))
        self.assertEqual(config.paths.release, Path("release/release.json"))

    def test_invalid_layout_stack_and_paths_fail_loud(self) -> None:
        for key, value in (("layout", "hub"), ("stack", "rust")):
            data = json.loads(json.dumps(BASE))
            data[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.write(data))
                self.assertIn(key, str(caught.exception))
        for paths in (
            {"unknown": "x"},
            {"spec": "../escape.md"},
            {"spec": "/absolute/spec.md"},
            {"spec": ""},
        ):
            data = json.loads(json.dumps(BASE))
            data["paths"] = paths
            with self.subTest(paths=paths):
                with self.assertRaises(ConfigError):
                    load_config(self.write(data))


if __name__ == "__main__":
    unittest.main()
