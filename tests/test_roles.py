from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.roles import POLICIES  # noqa: E402


class RolePolicyTests(unittest.TestCase):
    def policy(self, name: str):
        return POLICIES[name]()

    def test_solo_uses_one_agent(self) -> None:
        self.assertEqual(self.policy("solo").assign("verify"), ["agent"])

    def test_six_ring_covers_every_stage_including_fix(self) -> None:
        policy = self.policy("six-ring")
        self.assertEqual(policy.assign("spec"), ["spec"])
        self.assertEqual(policy.assign("build"), ["dev"])
        # The verify stage is test, then fix what failed, then check the fix.
        self.assertEqual(policy.assign("verify"), ["test", "fix", "check"])
        self.assertEqual(policy.assign("ship"), ["git"])
        self.assertEqual(policy.assign("learn"), ["spec", "check"])

    def test_orchestrator_keeps_a_gatekeeper(self) -> None:
        self.assertEqual(self.policy("orchestrator").assign("build")[0], "orchestrator")

    def test_a_policy_returns_a_copy(self) -> None:
        policy = self.policy("six-ring")
        policy.assign("verify").append("intruder")
        self.assertEqual(policy.assign("verify"), ["test", "fix", "check"])


if __name__ == "__main__":
    unittest.main()
