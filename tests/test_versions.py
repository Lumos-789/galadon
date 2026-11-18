from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon import __version__  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_all_version_sources_match(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        zcode = json.loads((ROOT / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(package["version"], __version__)
        self.assertEqual(package["version"], codex["version"])
        self.assertEqual(package["version"], zcode["version"])
        self.assertIn(f'version = "{__version__}"', pyproject)


if __name__ == "__main__":
    unittest.main()
