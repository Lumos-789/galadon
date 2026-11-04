from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntryPointTests(unittest.TestCase):
    def test_missing_pyyaml_reports_the_requirement(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        proc = subprocess.run(
            [sys.executable, "-S", "-m", "galadon", "--version"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 69, proc.stdout + proc.stderr)
        self.assertIn("PyYAML is required", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
