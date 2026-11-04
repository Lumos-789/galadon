from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import ProjectConfig
from ..ports import Artifact, DeployContext, ProbeResult, Release
from ..util import digest_path, now_iso, run_shell


class DeployError(RuntimeError):
    pass


class LocalCommandAdapter:
    name = "local"

    def describe(self, ctx: DeployContext) -> str:
        return f"local workspace {ctx.root}"

    def bootstrap(self, ctx: DeployContext) -> dict[str, Any]:
        raise DeployError("local adapter has no bootstrap step; there is nothing to provision")

    def teardown(self, ctx: DeployContext) -> dict[str, Any]:
        raise DeployError("local adapter has no teardown step; there is nothing to remove")

    def _run(self, command: str, ctx: DeployContext, stage: str) -> None:
        if not command:
            return
        code, stdout, stderr = run_shell(command, ctx.root, timeout=ctx.config.checks.timeout)
        if code != 0:
            detail = (stdout + "\n" + stderr).strip()[-2000:]
            raise DeployError(f"{stage} failed with exit {code}: {detail}")

    def build(self, ctx: DeployContext) -> Artifact:
        self._run(ctx.config.deploy.build, ctx, "build")
        configured = ctx.config.deploy.artifact.strip()
        if configured:
            path = ctx.root / configured
            if not path.exists():
                raise DeployError(f"configured artifact does not exist: {configured}")
        else:
            path = ctx.root / ".galadon" / "artifact.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"project": ctx.config.name, "built_at": now_iso()}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
        try:
            digest = digest_path(path)
        except OSError as exc:
            raise DeployError(f"cannot hash artifact {path}: {exc}") from exc
        return Artifact(path=path, digest=digest)

    def migrate(self, ctx: DeployContext) -> None:
        self._run(ctx.config.deploy.migrate, ctx, "migrate")

    def release(self, ctx: DeployContext, artifact: Artifact) -> Release:
        self._run(ctx.config.deploy.release, ctx, "release")
        return Release(
            env=ctx.env,
            artifact=artifact,
            adapter=self.name,
            metadata={"released_at": now_iso()},
        )

    def probe(self, ctx: DeployContext, release: Release) -> ProbeResult:
        url = ctx.config.deploy.probe_url.strip()
        if not url:
            raise DeployError(
                "deploy.probe_url is required; Galadon refuses to report success without a probe"
            )
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                status = response.getcode()
                body = response.read(256_000).decode(errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ProbeResult(ok=False, detail=f"{type(exc).__name__}: {exc}", url=url)
        if status != 200:
            return ProbeResult(ok=False, detail=f"HTTP {status}", url=url)
        expect = ctx.config.deploy.probe_expect.strip()
        if expect and expect not in body:
            return ProbeResult(ok=False, detail=f"probe body missing expected text: {expect}", url=url)
        return ProbeResult(ok=True, detail=f"HTTP {status}", url=url)

    def rollback(self, ctx: DeployContext, release: Release) -> None:
        raise DeployError("local adapter has no rollback implementation")
