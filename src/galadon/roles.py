from __future__ import annotations

from .ports import Phase


class SoloPolicy:
    name = "solo"

    def assign(self, phase: Phase, task: str) -> list[str]:
        return ["agent"]

    def handoff(self, from_role: str, to_role: str) -> str:
        return f"{from_role} -> {to_role}"

    def reject(self, phase: Phase, reason: str) -> Phase:
        return phase


class SixRingPolicy:
    name = "six-ring"
    _mapping = {
        "spec": ["spec"],
        "build": ["dev"],
        "verify": ["test", "check"],
        "ship": ["git"],
        "learn": ["spec", "check"],
        "done": ["agent"],
    }

    def assign(self, phase: Phase, task: str) -> list[str]:
        return list(self._mapping.get(phase, ["agent"]))

    def handoff(self, from_role: str, to_role: str) -> str:
        return f"{from_role} -> {to_role}"

    def reject(self, phase: Phase, reason: str) -> Phase:
        if phase in {"verify", "ship"}:
            return "build"
        return "spec"


class OrchestratorPolicy:
    name = "orchestrator"
    _mapping = {
        "spec": ["orchestrator", "spec"],
        "build": ["orchestrator", "worker"],
        "verify": ["orchestrator", "test"],
        "ship": ["orchestrator", "release"],
        "learn": ["orchestrator", "check"],
        "done": ["orchestrator"],
    }

    def assign(self, phase: Phase, task: str) -> list[str]:
        return list(self._mapping.get(phase, ["orchestrator"]))

    def handoff(self, from_role: str, to_role: str) -> str:
        return f"{from_role} -> {to_role}"

    def reject(self, phase: Phase, reason: str) -> Phase:
        return "build" if phase in {"verify", "ship"} else "spec"


_POLICIES = {
    "solo": SoloPolicy,
    "six-ring": SixRingPolicy,
    "orchestrator": OrchestratorPolicy,
}


def role_policy_for(name: str) -> SoloPolicy | SixRingPolicy | OrchestratorPolicy:
    return _POLICIES[name]()
