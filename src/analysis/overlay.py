"""Overlay rules from ``analyze/analyze.yaml``, evaluated before screen-specific logic.

This module is a stable public facade. Implementation is split into small
`analysis/overlay_*.py` modules.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np

from analysis.overlay_duration import parse_duration_seconds
from analysis.overlay_engine import (
    _apply_min_saturation_gate,
    evaluate_overlay_rules_async,
)
from analysis.overlay_manifest import load_analyze_yaml, load_merged_analyze_yaml
from analysis.overlay_rules import centers_delta_pct_between_regions
from ocr.client import OcrClient


async def run_overlay_analysis(
    image_bgr: np.ndarray,
    *,
    repo_root: Path,
    analyze_yaml: Path | None = None,
    area_doc: dict[str, Any] | None = None,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
) -> dict[str, Any]:
    """Load module overlay manifests (unless overridden) and evaluate ``overlay`` rules."""
    if analyze_yaml is None:
        cfg = load_merged_analyze_yaml(repo_root)
    elif analyze_yaml.is_file():
        cfg = load_analyze_yaml(analyze_yaml)
    else:
        cfg = {}
    overlay = cfg.get("overlay")
    rules = overlay if isinstance(overlay, list) else []

    if area_doc is None:
        import json

        area_path = repo_root / "area.json"
        area_doc = json.loads(area_path.read_text(encoding="utf-8"))

    return await evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        rules,
        current_screen=current_screen,
        rule_eval_state=rule_eval_state,
        state_flat=state_flat,
        ocr_client=ocr_client,
    )


def run_overlay_analysis_sync(
    image_bgr: np.ndarray,
    *,
    repo_root: Path,
    analyze_yaml: Path | None = None,
    area_doc: dict[str, Any] | None = None,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
) -> dict[str, Any]:
    """Sync wrapper for contexts that cannot await (e.g. some Streamlit pages)."""
    return asyncio.run(
        run_overlay_analysis(
            image_bgr,
            repo_root=repo_root,
            analyze_yaml=analyze_yaml,
            area_doc=area_doc,
            current_screen=current_screen,
            rule_eval_state=rule_eval_state,
            state_flat=state_flat,
            ocr_client=ocr_client,
        )
    )


def evaluate_overlay_rules(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]],
    *,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
) -> dict[str, Any]:
    """Sync wrapper kept for tests and non-async callers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: safe to asyncio.run
        pass
    else:
        if loop.is_running():
            raise RuntimeError(
                "evaluate_overlay_rules() called from an event loop; "
                "use await evaluate_overlay_rules_async(...) instead."
            )

    return asyncio.run(
        evaluate_overlay_rules_async(
            image_bgr,
            area_doc,
            repo_root,
            overlay_rules,
            current_screen=current_screen,
            rule_eval_state=rule_eval_state,
            state_flat=state_flat,
            ocr_client=ocr_client,
        )
    )


__all__ = [
    "parse_duration_seconds",
    "load_analyze_yaml",
    "load_merged_analyze_yaml",
    "centers_delta_pct_between_regions",
    "evaluate_overlay_rules_async",
    "evaluate_overlay_rules",
    "run_overlay_analysis",
    "run_overlay_analysis_sync",
    "_apply_min_saturation_gate",
]
