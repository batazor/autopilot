"""Repository layout anchors (src layout: package root is ``src/<pkg>/``)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Project root (``pyproject.toml``, ``area.json``, ``modules/``, ``db/``)."""

    return Path(__file__).resolve().parents[2]


def ensure_repo_on_sys_path() -> Path:
    """Insert repo root on ``sys.path`` so top-level ``modules.*`` imports resolve.

    Installed entry points (``uv run api``, ``uv run bot``) only put ``src/`` on the
    path; feature code under ``modules/`` is imported as ``modules.<id>.…``.
    """
    root = repo_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


@lru_cache(maxsize=1)
def src_root() -> Path:
    """``src/`` directory containing installable Python packages."""
    return Path(__file__).resolve().parents[1]


