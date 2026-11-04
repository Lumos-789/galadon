from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.config import ConfigError, load_config, render_probe_expect  # noqa: E402


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

    def test_probe_commit_placeholder(self) -> None:
        self.assertEqual(render_probe_expect("{{COMMIT}}", "abc1234"), "abc1234")
        self.assertEqual(render_probe_expect("plain", None), "plain")
        with self.assertRaises(ConfigError):
            render_probe_expect("{{COMMIT}}", None)

    def test_schema_declares_options(self) -> None:
        schema = json.loads((ROOT / "schema" / "galadon.schema.json").read_text(encoding="utf-8"))
        options = schema["properties"]["deploy"]["properties"]["options"]
        self.assertEqual(options["type"], "object")
        self.assertTrue(options["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
