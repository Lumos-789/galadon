from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import LAYOUTS, STACKS, ProjectConfig

MISSING_CHECKS = "no checks configured — declare checks.build/checks.test"

# A retro is short-term progress, so every real layout keeps it next to the
# progress notes instead of at the wiki root.
LAYOUT_RETRO = "wiki/progress/retro.md"

AGENT_SLOTS = ("plans", "evidence", "archive", "skills", "tmp", "wiki")


@dataclass(frozen=True)
class LayoutSpec:
    creates: str
    node_entry: str | None


# The layout vocabulary was sampled from three unrelated production projects in
# different stacks; each already used the same nine layers.
LAYOUT_SPECS: dict[str, LayoutSpec] = {
    "none": LayoutSpec(
        creates="base template only (default); no layout contract",
        node_entry=None,
    ),
    "webapp": LayoutSpec(
        creates=(
            "server/ web/ e2e/ tests/ scripts/ deploy/ + openspec/ spec/ "
            "wiki/{knowledge,progress,runbook}/ .agents/"
        ),
        node_entry="server/index.js",
    ),
    "service": LayoutSpec(
        creates=(
            "server/ tests/ scripts/ deploy/ + openspec/ spec/ "
            "wiki/{knowledge,progress,runbook}/ .agents/"
        ),
        node_entry="server/index.js",
    ),
    "plugin": LayoutSpec(
        creates=(
            "bin/<project> src/<project>/ skills/<project>/ docs/ tests/ scripts/ + "
            "openspec/ spec/ wiki/{knowledge,progress,runbook}/ .agents/"
        ),
        node_entry="src/index.js",
    ),
}

# One table for every stack's real commands. `{entry}` is filled from the
# layout's node entry; keep the literals here instead of scattering them.
STACK_CHECKS: dict[str, tuple[str, str]] = {
    "python": (
        "python3 -m compileall -q src",
        "python3 -m unittest discover -s tests -v",
    ),
    "node": ("node --check {entry}", "npm test"),
    "none": ("", ""),
}

CONTRACT_FILES = ("AGENTS.md", "openspec/config.yaml")
CONTRACT_DIRS = (
    "spec",
    "wiki/knowledge",
    "wiki/progress",
    "wiki/runbook",
    *(f".agents/{slot}" for slot in AGENT_SLOTS),
)
CONTRACT_IGNORES = ("out", ".agents")


def checks_for(layout: str, stack: str) -> tuple[str, str]:
    if layout not in LAYOUT_SPECS:
        raise ValueError(f"unknown layout: {layout}")
    if stack not in STACK_CHECKS:
        raise ValueError(f"unknown stack: {stack}")
    if stack == "none":
        return ("", "")
    build, test = STACK_CHECKS[stack]
    if "{entry}" in build:
        entry = LAYOUT_SPECS[layout].node_entry
        if entry is None:
            raise ValueError(f"layout {layout} has no node entry")
        build = build.format(entry=entry)
    return (build, test)


def layout_listing() -> str:
    lines = ["layouts (galadon init --layout):"]
    for name in LAYOUTS:
        lines.append(f"  {name:<8} {LAYOUT_SPECS[name].creates}")
    lines.extend(["", "stacks (galadon init --stack):"])
    for stack in STACKS:
        build, test = STACK_CHECKS[stack]
        if stack == "none":
            lines.append(
                f'  none     no checks: galadon check fails with "{MISSING_CHECKS}"'
            )
            continue
        build = build.format(entry="<entry>")
        note = " (package.json scripts.test = node --test tests/)" if stack == "node" else ""
        lines.append(f"  {stack:<8} build: {build} | test: {test}{note}")
    return "\n".join(lines)


def _gitignore_entries(root: Path) -> set[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return set()
    entries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line.lstrip("/").rstrip("/"))
    return entries


def layout_contract_problems(root: Path, config: ProjectConfig) -> list[str]:
    problems: list[str] = []
    for name in CONTRACT_FILES:
        if not (root / name).is_file():
            problems.append(name)
    for name in CONTRACT_DIRS:
        if not (root / name).is_dir():
            problems.append(f"{name}/")
    if not config.checks.build.strip() or not config.checks.test.strip():
        problems.append(MISSING_CHECKS)
    ignored = _gitignore_entries(root)
    for name in CONTRACT_IGNORES:
        if name not in ignored:
            problems.append(f".gitignore must ignore {name}/")
    return problems
