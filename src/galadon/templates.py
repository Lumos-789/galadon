from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class TemplateError(ValueError):
    pass


class FileTemplateProvider:
    def __init__(self, plugin_root: Path | None = None) -> None:
        self.plugin_root = plugin_root or PLUGIN_ROOT

    def _files(self, directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        return sorted(path for path in directory.rglob("*") if path.is_file())

    def render(
        self,
        root: Path,
        profile: str,
        role: str,
        name: str,
        force: bool = False,
    ) -> dict[str, list[str]]:
        base = self.plugin_root / "templates" / "base"
        overlay = self.plugin_root / "templates" / "profiles" / profile
        if not base.is_dir():
            raise TemplateError(f"missing template base: {base}")
        if not overlay.is_dir():
            raise TemplateError(f"unknown template profile: {profile}")

        values = {
            "{{PROJECT}}": name,
            "{{TEMPLATE}}": profile,
            "{{ROLE}}": role,
            "{{DATE}}": date.today().isoformat(),
        }
        written: list[str] = []
        skipped: list[str] = []
        for source in self._files(base) + self._files(overlay):
            relative = source.relative_to(base) if source.is_relative_to(base) else source.relative_to(overlay)
            target = root / relative
            if source.suffix == ".tmpl":
                target = target.with_suffix("")
            if target.exists() and not force:
                skipped.append(str(relative))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".tmpl":
                text = source.read_text(encoding="utf-8")
                for key, value in values.items():
                    text = text.replace(key, value)
                target.write_text(text, encoding="utf-8")
            else:
                shutil.copyfile(source, target)
            written.append(str(target.relative_to(root)))
        return {"written": sorted(set(written)), "skipped": sorted(set(skipped))}
