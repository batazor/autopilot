"""Repository layout anchors (src layout: package root is ``src/<pkg>/``)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Project root (``pyproject.toml``, ``area.json``, ``modules/``, ``db/``)."""

    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def src_root() -> Path:
    """``src/`` directory containing installable Python packages."""
    return Path(__file__).resolve().parents[1]


def balance_config_dir(repo_root_path: Path | None = None) -> Path:
    """``src/config/balance/`` (solver weights, profiles, hero meta)."""
    root = (repo_root_path or repo_root()).resolve()
    src_balance = root / "src" / "config" / "balance"
    if src_balance.is_dir():
        return src_balance
    legacy = root / "config" / "balance"
    if legacy.is_dir():
        return legacy
    return src_root() / "config" / "balance"


