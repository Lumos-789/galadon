from __future__ import annotations

from typing import Any

COMMIT_FIELDS = ("commit",)


def extract_commit(payload: dict[str, Any]) -> str | None:
    """Return the version field a probe response exposes, if any."""
    for field in COMMIT_FIELDS:
        if field in payload:
            value = payload[field]
            return "" if value is None else str(value).strip()
    return None
