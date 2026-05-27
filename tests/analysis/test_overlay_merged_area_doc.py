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


def test_core_area_json_alone_lacks_module_regions() -> None:
    import json

    core_path = repo_root() / "area.json"
    if not core_path.is_file():
        # Root area.json was drained during the modules migration; the
        # "core-only" view is now intrinsically empty.
        return
    core_only = json.loads(core_path.read_text(encoding="utf-8"))
    assert screen_region_by_name(core_only, "myriad_bazaar.title") is None


@pytest.mark.asyncio
async def test_run_overlay_analysis_loads_merged_area_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: must call ``load_area_doc``, not read ``area.json`` only."""
    calls: list[bool] = []
    real = load_area_doc

    def _spy(root, area_path=None):
        calls.append(True)
        return real(root, area_path)

    monkeypatch.setattr("analysis.overlay_area.default_area_doc_for_overlay", _spy)
    await run_overlay_analysis(
        np.zeros((128, 72, 3), dtype=np.uint8),
        repo_root=repo_root(),
        state_flat={"active_player": ""},
    )
    assert calls
