"""Balance read API.

Reads from :mod:`config.balance._data` (baked-in Python) instead of the
legacy ``config/balance/*.yaml`` files, which were retired so Nuitka can
absorb the values into the compiled ``config.so``.
"""
from __future__ import annotations

import copy
from typing import Any

from config.balance._data import BY_ID

# Display labels mirror the original filenames so existing UI links / docs
# still read sensibly even though the on-disk files no longer exist.
_DISPLAY_PATHS = {
    "defaults":    "config/balance/defaults.yaml",
    "profiles":    "config/balance/profiles.yaml",
    "hero_meta":   "config/balance/hero_meta.yaml",
    "cost_tables": "config/balance/cost_tables.yaml",
}


def list_balance_files() -> list[dict[str, str]]:
    return [
        {"id": file_id, "filename": _DISPLAY_PATHS[file_id].rsplit("/", 1)[-1]}
        for file_id in BY_ID
    ]


def read_balance_file(file_id: str) -> dict[str, Any]:
    raw = BY_ID.get(file_id)
    if raw is None:
        msg = f"unknown balance file: {file_id}"
        raise KeyError(msg)
    return {
        "id": file_id,
        "path": _DISPLAY_PATHS[file_id],
        # Deep-copy so the API caller can't mutate the baked-in module globals.
        "content": copy.deepcopy(raw),
    }
