"""Overlay-test entrypoints: screen detect, full overlay run, frame image."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from analysis.overlay import run_overlay_analysis_sync
from analysis.overlay_manifest import load_merged_analyze_yaml
from api.services.overlay_test.breakdown import _collect_push_candidates, _run_module_analyzer_breakdown
from api.services.overlay_test.cache import _detect_screen_from_preview_png, _load_overlay_test_preview
from api.services.overlay_test.common import (
    _coerce_float,
    _decode_png_to_bgr,
    _evaluate_rules_ignoring_screen_gate,
    _rule_metadata,
)
from api.services.overlay_test.drawing import (
    _add_match_rect,
    _add_region_bbox_fallback,
    _add_search_roi,
    _add_tap_marker_if_any,
)
from api.services.overlay_test.types import (
    ModuleAnalyzerRun,
    OverlayRuleRow,
    OverlayTestResult,
    PushScenarioCandidate,
    ScreenDetectResult,
)
from config.paths import repo_root
from layout.area_manifest import (
    load_area_doc,
)

if TYPE_CHECKING:
    import numpy as np

    from api.services.click_approval_overlay import (
        OverlayShape,
    )

_OVERLAY_TEST_PROBE_PLAYER = "overlay-test-probe"


def _overlay_test_cond_context(
    *,
    has_active_player: bool,
) -> tuple[dict[str, Any], bool, str]:
    """Return ``(state_flat, simulated_no_player, active_player_label)`` — UI only, no Redis."""
    if has_active_player:
        return {"active_player": _OVERLAY_TEST_PROBE_PLAYER}, False, _OVERLAY_TEST_PROBE_PLAYER
    return {"active_player": ""}, True, ""


def run_screen_detect(
    *,
    instance_id: str,
    preview_source: str = "live",
    preview_rel: str | None = None,
    client: Any | None = None,
) -> ScreenDetectResult:
    """Detect the current screen on the rolling preview without overlay analysis."""
    png, rel, mtime, frame_source = _load_overlay_test_preview(
        instance_id=instance_id,
        preview_source=preview_source,
        preview_rel=preview_rel,
    )

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    detected_screen, screen_detect_ms = _detect_screen_from_preview_png(
        instance_id=instance_id,
        client=client,
        png=png,
        image_bgr=image_bgr,
    )
    return ScreenDetectResult(
        instance_id=instance_id,
        detected_screen=detected_screen,
        screen_source="detected" if detected_screen else "none",
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
            "source": frame_source,
        },
        duration_ms=screen_detect_ms,
    )


def run_overlay_test(
    *,
    instance_id: str,
    only_current_screen: bool = False,
    ignore_screen_gate: bool = False,
    has_active_player: bool = True,
    detailed_analysis: bool = False,
    preview_source: str = "live",
    preview_rel: str | None = None,
    client: Any | None = None,
) -> OverlayTestResult:
    """Run screen detect on the frame, then overlay rules (static PNG probe).

    The reported ``current_screen`` still comes only from frame detection;
    ``active_player`` for ``cond`` / push dry-run comes from the
    ``has_active_player`` request flag (UI), not live ``active_player``.

    When ``client`` is provided, the instance's last known ``current_screen``
    is read from Redis and forwarded to the detector as a sticky hint so the
    fast path can short-circuit the full multi-screen template scan.

    ``only_current_screen``: post-filter to rules whose ``screens`` includes the
    detected screen. Pure UI noise reduction; doesn't change what runs.

    ``ignore_screen_gate``: bypass the engine's ``screens`` short-circuit so every
    rule actually executes (operator "would this match?" probe). Mutually exclusive
    with ``only_current_screen`` (the filter is meaningless when nothing was gated).
    """
    state_flat, simulated_no_player, active_player = _overlay_test_cond_context(
        has_active_player=has_active_player,
    )

    png, rel, mtime, frame_source = _load_overlay_test_preview(
        instance_id=instance_id,
        preview_source=preview_source,
        preview_rel=preview_rel,
    )

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    detected_screen, screen_detect_ms = _detect_screen_from_preview_png(
        instance_id=instance_id,
        client=client,
        png=png,
        image_bgr=image_bgr,
    )
    overlay_screen = detected_screen
    screen_source = "detected" if overlay_screen else "none"

    repo = repo_root()
    area_doc = load_area_doc(repo)
    merged = load_merged_analyze_yaml(repo)
    rules_raw_obj = merged.get("overlay") if isinstance(merged, dict) else None
    rules_raw = (
        [r for r in rules_raw_obj if isinstance(r, dict)]
        if isinstance(rules_raw_obj, list)
        else []
    )
    rule_node, rule_search, rule_action = _rule_metadata(
        rules_raw, area_doc=area_doc, state_flat=state_flat
    )

    rules: list[OverlayRuleRow] = []
    overlays: list[OverlayShape] = []
    matched_count = 0
    module_runs: list[ModuleAnalyzerRun] = []
    modules_total_ms = 0
    full_run_ms = 0
    push_candidates: list[PushScenarioCandidate] = []

    boot_device_level_only = simulated_no_player

    if image_bgr is not None and rules_raw:
        # When no screen is detected and the screen gate is active, virtually
        # all rules get filtered by ``screens:`` — the per-module breakdown
        # then records ~40 empty rows at ~50 ms setup each. Skip it; the full
        # run below still surfaces any device-level matches at a fraction of
        # the cost.
        if detailed_analysis and (overlay_screen or ignore_screen_gate):
            module_runs, modules_total_ms = _run_module_analyzer_breakdown(
                image_bgr,
                repo=repo,
                area_doc=area_doc,
                current_screen=overlay_screen or None,
                state_flat=state_flat,
                instance_id=None,
                device_level_only=boot_device_level_only,
            )
        full_started = time.perf_counter()
        if ignore_screen_gate:
            results = _evaluate_rules_ignoring_screen_gate(
                image_bgr,
                area_doc=area_doc,
                rules_raw=rules_raw,
                repo=repo,
                state_flat=state_flat,
            )
        else:
            results = run_overlay_analysis_sync(
                image_bgr,
                repo_root=repo,
                current_screen=overlay_screen or None,
                state_flat=state_flat,
                instance_id=None,
                device_level_only=boot_device_level_only,
            )
        full_run_ms = int((time.perf_counter() - full_started) * 1000)
        push_candidates = _collect_push_candidates(
            results,
            repo=repo,
            active_player=active_player,
            current_screen=overlay_screen,
        )
        for r in rules_raw:
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            payload = results.get(name)
            if not isinstance(payload, dict):
                continue
            node = rule_node.get(name, "")
            if only_current_screen and overlay_screen and node and node != overlay_screen:
                continue
            matched = bool(payload.get("matched"))
            if matched:
                matched_count += 1

            region_name = str(payload.get("region") or r.get("region") or "").strip()
            reason = str(payload.get("reason") or "")
            detail = str(payload.get("detail") or "")
            notes_parts = [reason] if reason else []
            if detail and detail != reason:
                notes_parts.append(detail)

            rules.append(
                OverlayRuleRow(
                    name=name,
                    node=node or "",
                    region=region_name,
                    action=rule_action.get(name, ""),
                    search_region=str(
                        payload.get("search_region") or rule_search.get(name, "")
                    ),
                    matched=matched,
                    score=_coerce_float(payload.get("score")),
                    threshold=_coerce_float(payload.get("threshold")),
                    reason=reason,
                    notes=": ".join(notes_parts).strip(),
                )
            )

            if matched and width > 0 and height > 0:
                if payload.get("top_left") is not None:
                    _add_match_rect(
                        overlays,
                        payload=payload,
                        rule_name=name,
                        matched=True,
                        w=width,
                        h=height,
                    )
                    _add_tap_marker_if_any(
                        overlays, payload=payload, w=width, h=height
                    )
                elif region_name:
                    _add_region_bbox_fallback(
                        overlays,
                        region_name=region_name,
                        area_doc=area_doc,
                        state_flat=state_flat,
                        w=width,
                        h=height,
                        rule_name=name,
                        matched=True,
                    )
                # Search ROI is always informative if the rule defined one.
                _add_search_roi(
                    overlays,
                    payload=payload,
                    rule_search_name=rule_search.get(name, ""),
                    area_doc=area_doc,
                    w=width,
                    h=height,
                )

    return OverlayTestResult(
        instance_id=instance_id,
        current_screen=overlay_screen,
        detected_screen=detected_screen,
        active_player=active_player,
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
            "source": frame_source,
        },
        rules=rules,
        overlays=overlays,
        total_rules=len(rules),
        matched_count=matched_count,
        analysis={
            "module_runs": module_runs,
            "modules_total_ms": modules_total_ms,
            "full_run_ms": full_run_ms,
            "screen_detect_ms": screen_detect_ms,
            "screen_source": screen_source,
            "push_candidates": push_candidates,
            "has_active_player": has_active_player,
            "simulated_no_player": simulated_no_player,
            "device_level_only": boot_device_level_only,
        },
    )


def load_overlay_test_image(
    instance_id: str,
    *,
    preview_source: str = "live",
    preview_rel: str | None = None,
) -> tuple[bytes | None, str, float | None]:
    """Return overlay-test frame PNG bytes (rolling or reference)."""
    png, rel, mtime, _src = _load_overlay_test_preview(
        instance_id=instance_id,
        preview_source=preview_source,
        preview_rel=preview_rel,
    )
    return png, rel, mtime
