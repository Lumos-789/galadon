from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

_DEPLOY_FIELDS = {
    "adapter",
    "env",
    "build",
    "migrate",
    "release",
    "probe_url",
    "probe_expect",
    "artifact",
    "options",
}
_SSH_REQUIRED = ("host", "user", "remote_root", "service", "artifact")


class ConfigError(ValueError):
    pass


@dataclass
class DeployConfig:
    adapter: str = "local"
    env: str = "test"
    build: str = ""
    migrate: str = ""
    release: str = ""
    probe_url: str = ""
    probe_expect: str = ""
    artifact: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChecksConfig:
    build: str = "true"
    test: str = "true"
    timeout: int = 300


@dataclass
class ProjectConfig:
    name: str
    template: str
    role: str
    deploy: DeployConfig
    checks: ChecksConfig
    tools: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def render_probe_expect(expect: str, commit: str | None) -> str:
    if "{{COMMIT}}" not in expect:
        return expect
    if not commit:
        raise ConfigError("deploy.probe_expect uses {{COMMIT}} but the project has no git commit")
    return expect.replace("{{COMMIT}}", commit)


def _validate_ssh_options(options: dict[str, Any], artifact: str) -> None:
    values = {
        "host": options.get("host"),
        "user": options.get("user"),
        "remote_root": options.get("remote_root"),
        "service": options.get("service"),
        "artifact": artifact,
    }
    missing = [
        key
        for key in _SSH_REQUIRED
        if values[key] is None or not str(values[key]).strip()
    ]
    if missing:
        raise ConfigError(
            "galadon.yaml: deploy.adapter ssh requires host, user, remote_root, "
            f"service, artifact; missing: {', '.join(missing)}"
        )

    host = str(values["host"]).strip()
    user = str(values["user"]).strip()
    service = str(values["service"]).strip()
    remote_root = str(values["remote_root"]).strip().rstrip("/")
    host_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-[]"
    user_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    service_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.@-"
    if not host[0].isalnum() or any(char not in host_chars for char in host):
        raise ConfigError("galadon.yaml: deploy.options.host contains unsupported characters")
    if not user[0].isalnum() or any(char not in user_chars for char in user):
        raise ConfigError("galadon.yaml: deploy.options.user contains unsupported characters")
    if not service[0].isalnum() or any(char not in service_chars for char in service):
        raise ConfigError("galadon.yaml: deploy.options.service contains unsupported characters")
    if not remote_root.startswith("/"):
        raise ConfigError("galadon.yaml: deploy.options.remote_root must be an absolute path")
    remote_root_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/"
    if any(char not in remote_root_chars for char in remote_root):
        raise ConfigError(
            "galadon.yaml: deploy.options.remote_root may only contain letters, digits, "
            "dot, underscore, hyphen and slash"
        )
    parts = PurePosixPath(remote_root).parts
    if len(parts) < 3 or ".." in parts or "." in parts:
        raise ConfigError(
            "galadon.yaml: deploy.options.remote_root must be a specific directory below /"
        )


def load_config(root: Path) -> ProjectConfig:
    path = root / "galadon.yaml"
    if not path.is_file():
        raise ConfigError(f"missing {path}; run `galadon init` first")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"galadon.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("galadon.yaml must contain a mapping")

    name = str(data.get("project", "")).strip()
    if not name:
        raise ConfigError("galadon.yaml: project is required")
    template = str(data.get("template", "")).strip()
    if template not in {"web", "api", "static"}:
        raise ConfigError("galadon.yaml: template must be web, api or static")
    role = str(data.get("role", "solo")).strip()
    if role not in {"solo", "six-ring", "orchestrator"}:
        raise ConfigError("galadon.yaml: role must be solo, six-ring or orchestrator")

    deploy_raw = data.get("deploy") or {}
    checks_raw = data.get("checks") or {}
    if not isinstance(deploy_raw, dict):
        raise ConfigError("galadon.yaml: deploy must be a mapping")
    if not isinstance(checks_raw, dict):
        raise ConfigError("galadon.yaml: checks must be a mapping")

    options_raw = deploy_raw.get("options", {})
    if options_raw is None:
        options_raw = {}
    if not isinstance(options_raw, dict):
        raise ConfigError("galadon.yaml: deploy.options must be a mapping")
    options = dict(options_raw)
    for key, value in deploy_raw.items():
        if key not in _DEPLOY_FIELDS and key not in options:
            options[str(key)] = value

    deploy = DeployConfig(
        adapter=str(deploy_raw.get("adapter", "local")),
        env=str(deploy_raw.get("env", "test")),
        build=str(deploy_raw.get("build", "")),
        migrate=str(deploy_raw.get("migrate", "")),
        release=str(deploy_raw.get("release", "")),
        probe_url=str(deploy_raw.get("probe_url", "")),
        probe_expect=str(deploy_raw.get("probe_expect", "")),
        artifact=str(deploy_raw.get("artifact", "")),
        options=options,
    )
    if deploy.adapter not in {"local", "ssh", "k8s"}:
        raise ConfigError("galadon.yaml: deploy.adapter must be local, ssh or k8s")
    if deploy.adapter == "ssh":
        _validate_ssh_options(options, deploy.artifact)

    checks = ChecksConfig(
        build=str(checks_raw.get("build", "true")),
        test=str(checks_raw.get("test", "true")),
        timeout=int(checks_raw.get("timeout", 300)),
    )
    tools = data.get("tools") or {}
    if not isinstance(tools, dict):
        raise ConfigError("galadon.yaml: tools must be a mapping")

    return ProjectConfig(
        name=name,
        template=template,
        role=role,
        deploy=deploy,
        checks=checks,
        tools={str(key): str(value) for key, value in tools.items()},
        raw=data,
    )
