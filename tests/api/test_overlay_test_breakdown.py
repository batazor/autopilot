"""Per-module overlay-test breakdown optimizations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import numpy as np
import pytest

from api.services.overlay_test import (
    _module_has_overlay_rules,
    _run_module_analyzer_breakdown_async,
)
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def test_module_has_overlay_rules_false_for_empty_list(tmp_path: Path) -> None:
    mod = tmp_path / "heroes"
    (mod / "analyze").mkdir(parents=True)
    (mod / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (mod / "module.yaml").write_text("id: heroes_feature\n", encoding="utf-8")
    assert _module_has_overlay_rules(tmp_path, "heroes") is False


def test_module_has_overlay_rules_boot_mode_requires_device_level(tmp_path: Path) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "building"
    (mod / "analyze").mkdir(parents=True)
    (mod / "analyze" / "analyze.yaml").write_text(
        "overlay:\n"
        "  - name: build_button.visible\n"
        "    region: build_button\n"
        "    action: findIcon\n",
        encoding="utf-8",
    )
    (mod / "module.yaml").write_text("id: building\n", encoding="utf-8")
    assert _module_has_overlay_rules(tmp_path, "building") is True
    assert _module_has_overlay_rules(tmp_path, "building", device_level_only=True) is False


@pytest.mark.asyncio
async def test_breakdown_skips_empty_overlay_without_calling_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = tmp_path / "heroes"
    (mod / "analyze").mkdir(parents=True)
    (mod / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (mod / "module.yaml").write_text(
        "id: heroes_feature\ntitle: Heroes (feature)\n", encoding="utf-8"
    )

    analyze = AsyncMock(side_effect=AssertionError("run_overlay_analysis should not run"))
    monkeypatch.setattr("api.services.overlay_test.run_overlay_analysis", analyze)

    def _one_manifest(_repo: object, _scope: str | None = None) -> list[object]:
        return [mod / "analyze" / "analyze.yaml"]

    monkeypatch.setattr(
        "dsl.registry.iter_module_analyze_manifests",
        _one_manifest,
    )
    monkeypatch.setattr(
        "config.module_discovery.module_storage_key",
        lambda _d, _r: "heroes",
    )

    frame = np.zeros((64, 48, 3), dtype=np.uint8)
    runs = await _run_module_analyzer_breakdown_async(
        frame,
        repo=tmp_path,
        area_doc={},
        current_screen="main_city",
        state_flat=None,
        instance_id=None,
    )
    assert len(runs) == 1
    assert runs[0]["module_id"] == "heroes_feature"
    assert runs[0]["duration_ms"] == 0
    assert runs[0]["rule_count"] == 0
    analyze.assert_not_called()
