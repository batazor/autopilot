"""Overlay analysis must use merged area manifests (core + modules)."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.overlay import run_overlay_analysis
from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc


def test_merged_area_doc_includes_myriad_bazaar_title_region() -> None:
    doc = load_area_doc(repo_root())
    assert screen_region_by_name(doc, "myriad_bazaar.title") is not None


@pytest.mark.asyncio
async def test_run_overlay_analysis_loads_merged_area_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: overlay analysis must source area from merged module manifests."""
    calls: list[bool] = []
    real = load_area_doc

    def _spy(root):
        calls.append(True)
        return real(root)

    monkeypatch.setattr("analysis.overlay_area.default_area_doc_for_overlay", _spy)
    await run_overlay_analysis(
        np.zeros((128, 72, 3), dtype=np.uint8),
        repo_root=repo_root(),
        state_flat={"active_player": ""},
    )
    assert calls
