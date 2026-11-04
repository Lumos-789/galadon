from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .roles import role_policy_for
from .util import now_iso, read_json, write_json

PHASES = ("spec", "build", "verify", "ship", "learn")
SPEC_PATH = Path("spec/spec.md")
RETRO_PATH = Path("wiki/retro.md")
CHECKS_PATH = Path(".galadon/checks.json")
RELEASE_PATH = Path("release/release.json")
EVIDENCE_PATH = Path("verify/evidence.md")

ACTIONS = {
    "spec": "Write spec/spec.md: problem, acceptance criteria and non-goals.",
    "build": "Implement the spec, then run `galadon check`.",
    "verify": "Fix the failed checks, then run `galadon check` again.",
    "ship": "Run `galadon ship --env <env>` after checking `--dry-run`.",
    "learn": "Write wiki/retro.md, then run `galadon check`.",
    "done": "Loop complete. Start the next spec.",
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def spec_ok(root: Path) -> tuple[bool, str]:
    text = _text(root / SPEC_PATH)
    if not text:
        return False, "spec/spec.md is missing"
    if "TODO" in text:
        return False, "spec/spec.md still contains TODO"
    for heading in ("## Problem", "## Acceptance", "## Non-goals"):
        if heading not in text:
            return False, f"spec/spec.md is missing {heading}"
    if not re.search(r"^- \[[ xX]\] ", text, flags=re.MULTILINE):
        return False, "spec/spec.md needs at least one acceptance checkbox"
    return True, "spec is complete"


def retro_ok(root: Path) -> tuple[bool, str]:
    text = _text(root / RETRO_PATH)
    if not text:
        return False, "wiki/retro.md is missing"
    if "TODO" in text:
        return False, "wiki/retro.md still contains TODO"
    for heading in ("## What changed", "## Next"):
        if heading not in text:
            return False, f"wiki/retro.md is missing {heading}"
    return True, "retro is complete"


def infer_phase(root: Path, config: ProjectConfig) -> str:
    if not spec_ok(root)[0]:
        return "spec"
    checks = read_json(root / CHECKS_PATH, None)
    if checks is None:
        return "build"
    if not isinstance(checks, dict) or checks.get("ok") is not True:
        return "verify"
    release = read_json(root / RELEASE_PATH, None)
    if not isinstance(release, dict) or release.get("ok") is not True:
        return "ship"
    if not retro_ok(root)[0]:
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
        "next": ACTIONS[phase],
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
