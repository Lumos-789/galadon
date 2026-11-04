from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.roles import role_policy_for  # noqa: E402


class RolePolicyTests(unittest.TestCase):
    def test_solo_uses_one_agent(self) -> None:
        policy = role_policy_for("solo")
        self.assertEqual(policy.assign("verify", ""), ["agent"])

    def test_six_ring_maps_five_stages(self) -> None:
        policy = role_policy_for("six-ring")
        self.assertEqual(policy.assign("spec", ""), ["spec"])
        self.assertEqual(policy.assign("build", ""), ["dev"])
        self.assertEqual(policy.assign("verify", ""), ["test", "check"])
        self.assertEqual(policy.assign("ship", ""), ["git"])
        self.assertEqual(policy.reject("verify", "tests failed"), "build")

    def test_orchestrator_keeps_a_gatekeeper(self) -> None:
        policy = role_policy_for("orchestrator")
        self.assertEqual(policy.assign("build", "")[0], "orchestrator")


if __name__ == "__main__":
    unittest.main()
