"""Overlay rules from ``analyze/analyze.yaml``, evaluated before screen-specific logic.

This module is a stable public facade. Implementation is split into small
`analysis/overlay_*.py` modules.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from analysis.overlay_compile import CompiledOverlayPlan, compile_overlay_plan
from analysis.overlay_duration import parse_duration_seconds
from analysis.overlay_engine import (
    _apply_min_saturation_gate,
    evaluate_overlay_rules_async,
)
from analysis.overlay_manifest import (
    compiled_overlay_plan,
    load_analyze_yaml,
    load_merged_analyze_yaml,
)
from analysis.overlay_rules import centers_delta_pct_between_regions

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

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
    device_level_only: bool = False,
    module_scope: str | None = None,
    instance_id: str | None = None,
    redis_async: Any | None = None,
) -> dict[str, Any]:
    """Load module overlay manifests (unless overridden) and evaluate ``overlay`` rules."""
    if analyze_yaml is None:
        plan = compiled_overlay_plan(
            repo_root,
            module_scope=module_scope,
            device_level_only=device_level_only,
        )
    elif analyze_yaml.is_file():
        cfg = load_analyze_yaml(analyze_yaml)
        overlay = cfg.get("overlay")
        rules = overlay if isinstance(overlay, list) else []
        if device_level_only:
            rules = [
                rule
                for rule in rules
                if isinstance(rule, dict) and rule.get("device_level") is True
            ]
        plan = compile_overlay_plan(rules)
    else:
        plan = compile_overlay_plan([])

    if area_doc is None:
        from analysis.overlay_area import default_area_doc_for_overlay

        area_doc = default_area_doc_for_overlay(repo_root)

    return await evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        plan,
        current_screen=current_screen,
        rule_eval_state=rule_eval_state,
        state_flat=state_flat,
        ocr_client=ocr_client,
        instance_id=instance_id,
        redis_async=redis_async,
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
    device_level_only: bool = False,
    module_scope: str | None = None,
    instance_id: str | None = None,
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
            device_level_only=device_level_only,
            module_scope=module_scope,
            instance_id=instance_id,
        )
    )


def evaluate_overlay_rules(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]] | CompiledOverlayPlan,
    *,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
    instance_id: str | None = None,
    redis_async: Any | None = None,
) -> dict[str, Any]:
    """Sync wrapper kept for tests and non-async callers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: safe to asyncio.run
        pass
    else:
        if loop.is_running():
            msg = (
                "evaluate_overlay_rules() called from an event loop; "
                "use await evaluate_overlay_rules_async(...) instead."
            )
            raise RuntimeError(
                msg
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
            instance_id=instance_id,
            redis_async=redis_async,
        )
    )


__all__ = [
    "_apply_min_saturation_gate",
    "centers_delta_pct_between_regions",
    "evaluate_overlay_rules",
    "evaluate_overlay_rules_async",
    "load_analyze_yaml",
    "load_merged_analyze_yaml",
    "parse_duration_seconds",
    "run_overlay_analysis",
    "run_overlay_analysis_sync",
]
