from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .config import ProjectConfig

Phase = Literal["spec", "build", "verify", "ship", "learn", "done"]
GateStatus = Literal["pass", "fail", "waived"]


@dataclass
class Artifact:
    path: Path
    digest: str


@dataclass
class Release:
    env: str
    artifact: Artifact
    adapter: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeResult:
    ok: bool
    detail: str
    url: str | None = None


@dataclass
class GateResult:
    id: str
    status: GateStatus
    detail: str


@dataclass
class DeployContext:
    root: Path
    config: ProjectConfig
    env: str


class RolePort(Protocol):
    def assign(self, phase: Phase, task: str) -> list[str]: ...

    def handoff(self, from_role: str, to_role: str) -> str: ...

    def reject(self, phase: Phase, reason: str) -> Phase: ...


class DeployPort(Protocol):
    name: str

    def describe(self, ctx: DeployContext) -> str: ...

    def bootstrap(self, ctx: DeployContext) -> dict[str, Any]: ...

    def build(self, ctx: Any) -> Artifact: ...

    def migrate(self, ctx: Any) -> None: ...

    def release(self, ctx: Any, artifact: Artifact) -> Release: ...

    def probe(self, ctx: Any, release: Release) -> ProbeResult: ...

    def rollback(self, ctx: Any, release: Release) -> None: ...

    def teardown(self, ctx: DeployContext) -> dict[str, Any]: ...


class TemplatePort(Protocol):
    def render(self, root: Path, profile: str, role: str, name: str) -> list[Path]: ...


class Gate(Protocol):
    id: str

    def evaluate(self, root: Path, config: Any) -> GateResult: ...
