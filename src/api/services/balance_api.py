"""Balance YAML read API."""
from __future__ import annotations

from typing import Any

import yaml

from config.paths import balance_config_dir

_BALANCE = balance_config_dir()
_FILES = {
    "defaults": "defaults.yaml",
    "profiles": "profiles.yaml",
    "hero_meta": "hero_meta.yaml",
}


def list_balance_files() -> list[dict[str, str]]:
    return [{"id": k, "filename": v} for k, v in _FILES.items()]


def read_balance_file(file_id: str) -> dict[str, Any]:
    name = _FILES.get(file_id)
    if not name:
        msg = f"unknown balance file: {file_id}"
        raise KeyError(msg)
    path = _BALANCE / name
    if not path.is_file():
        msg = "file not found"
        raise FileNotFoundError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        "id": file_id,
        "path": path.as_posix(),
        "content": raw if isinstance(raw, (dict, list)) else {},
    }
