from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .roles import role_policy_for
from .util import now_iso, read_json, write_json

CHECKS_PATH = Path(".galadon/checks.json")

ACTIONS = {
    "build": "Implement the spec, then run `galadon check`.",
    "verify": "Fix the failed checks, then run `galadon check` again.",
    "ship": "Run `galadon ship --env <env>` after checking `--dry-run`.",
    "done": "Loop complete. Start the next spec.",
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def actions_for(config: ProjectConfig) -> dict[str, str]:
    return {
        "spec": (
            f"Write {config.paths.spec}: problem, acceptance criteria and non-goals."
        ),
        **ACTIONS,
        "learn": f"Write {config.paths.retro}, then run `galadon check`.",
    }


def spec_ok(root: Path, config: ProjectConfig) -> tuple[bool, str]:
    relative = config.paths.spec
    text = _text(root / relative)
    if not text:
        return False, f"{relative} is missing"
    if "TODO" in text:
        return False, f"{relative} still contains TODO"
    for heading in ("## Problem", "## Acceptance", "## Non-goals"):
        if heading not in text:
            return False, f"{relative} is missing {heading}"
    if not re.search(r"^- \[[ xX]\] ", text, flags=re.MULTILINE):
        return False, f"{relative} needs at least one acceptance checkbox"
    return True, "spec is complete"


def retro_ok(root: Path, config: ProjectConfig) -> tuple[bool, str]:
    relative = config.paths.retro
    text = _text(root / relative)
    if not text:
        return False, f"{relative} is missing"
    if "TODO" in text:
        return False, f"{relative} still contains TODO"
    for heading in ("## What changed", "## Next"):
        if heading not in text:
            return False, f"{relative} is missing {heading}"
    return True, "retro is complete"


def infer_phase(root: Path, config: ProjectConfig) -> str:
    if not spec_ok(root, config)[0]:
        return "spec"
    checks = read_json(root / CHECKS_PATH, None)
    if checks is None:
        return "build"
    if not isinstance(checks, dict) or checks.get("ok") is not True:
        return "verify"
    release = read_json(root / config.paths.release, None)
    if not isinstance(release, dict) or release.get("ok") is not True:
        return "ship"
    if not retro_ok(root, config)[0]:
        return "learn"
    return "done"


def role_hint(phase: str, config: ProjectConfig) -> str:
    return " + ".join(role_policy_for(config.role).assign(phase, ""))


def next_payload(root: Path, config: ProjectConfig) -> dict[str, Any]:
    phase = infer_phase(root, config)
    return {
        "project": config.name,
        "phase": phase,
        "role": role_hint(phase, config),
        "next": actions_for(config)[phase],
    }


def update_agents_block(root: Path, payload: dict[str, Any]) -> None:
    path = root / "AGENTS.md"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    block = (
        "<!-- galadon:begin -->\n"
        f"- phase: {payload['phase']}\n"
        f"- role: {payload['role']}\n"
        f"- next: {payload['next']}\n"
        "<!-- galadon:end -->"
    )
    pattern = re.compile(r"<!-- galadon:begin -->.*?<!-- galadon:end -->", re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def save_state(root: Path, payload: dict[str, Any]) -> None:
    data = {
        "updated_at": now_iso(),
        "phase": payload.get("phase"),
        "role": payload.get("role"),
        "next": payload.get("next"),
    }
    write_json(root / ".galadon" / "state.json", data)


def dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
