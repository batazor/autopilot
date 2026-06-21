"""Farm module wiring — an inert skeleton until the first flow lands."""
from __future__ import annotations

from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return yaml.safe_load((MODULE_DIR / "module.yaml").read_text(encoding="utf-8"))


def test_farm_manifest_is_inert_skeleton() -> None:
    m = _manifest()
    assert m["id"] == "farm"
    # Skeleton: inert until the first flow + labeled regions land.
    assert m["enabled"] is False
