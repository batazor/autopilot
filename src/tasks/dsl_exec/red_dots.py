"""``exec: put_all_red_dots`` / ``click_next_red_dot_tab`` — red-dot badge sweeps."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.red_dot_detector import find_red_dots
from layout.tabs_strip_navigator import pick_next_strip_action
from layout.tabs_strip_segmenter import detect_tabs_in_strip
from layout.types import Point
from tasks import dsl_runtime
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
)

logger = logging.getLogger(__name__)

def _load_area_doc() -> dict[str, Any]:
    from layout.area_manifest import load_area_doc

    try:
        return load_area_doc(repo_root())
    except Exception:
        logger.exception("dsl exec: failed to load area manifest")
        return {}


# Hard cap so a runaway frame (badge graphics misclassified as red dots) can't
# spam the device — the bot taps at most this many dots in one ``put_all_red_dots``
# call. Per-iteration tap count is bounded by what the detector returns; we
# break early as soon as a fresh frame has zero detections.
_PUT_ALL_RED_DOTS_MAX_TAPS = 20
# Cool-down between taps so each tap settles (popups dismiss, screen redraws)
# before the next frame is captured.
_PUT_ALL_RED_DOTS_TAP_DELAY_S = 0.4
# Settling pause after the batch of taps before re-scanning, so the next frame
# reflects the current UI state and we don't double-tap a stale dot.
_PUT_ALL_RED_DOTS_RESCAN_DELAY_S = 0.6
# Per-sweep cycle guard: if a tap reopens the same popup and the detector
# surfaces the same dot again, we'd loop until the global cap. We treat any
# two detections within this radius as "the same spot"; after the 2nd tap on
# that spot the area joins a sweep-local filter and subsequent detections in
# the radius are skipped before tapping.
_PUT_ALL_RED_DOTS_DUP_RADIUS_PX = 5
_PUT_ALL_RED_DOTS_DUP_MAX_HITS = 2


async def _exec_click_next_red_dot_tab(ctx: DslExecContext) -> None:
    """Click the first non-active red-dot tab in a dynamic tab strip.

    Args:
      region: area region containing the whole tab strip.
      next_region: optional area region for a strip "next page" control. Used
        when no visible inactive red-dot tab is available but the strip can be
        paged forward.

    The active tab is intentionally skipped: the active page's own analyzer
    should push its claim scenario, while this handler only moves the bot to
    another visible tab that advertises pending work.
    """
    region_name = str((ctx.args or {}).get("region") or "").strip()
    if not region_name:
        logger.warning("dsl exec click_next_red_dot_tab: missing region arg")
        ctx.result.update({"action": "missing_region"})
        return
    region_prefix = region_name.split(".", 1)[0].strip()
    if region_prefix and "." in region_name and ctx.redis_client is not None:
        try:
            raw_screen = await ctx.redis_client.hget(
                f"wos:instance:{ctx.instance_id}:state",
                "current_screen",
            )
        except Exception:
            raw_screen = None
        current_screen = _decode_redis_raw(raw_screen)
        screen_prefix = current_screen.split(".", 1)[0].strip() if current_screen else ""
        if screen_prefix and screen_prefix != region_prefix:
            logger.info(
                "dsl exec click_next_red_dot_tab: skip region=%s on current_screen=%s",
                region_name,
                current_screen,
            )
            ctx.result.update(
                {
                    "action": "screen_mismatch",
                    "region": region_name,
                    "current_screen": current_screen,
                }
            )
            return

    area_doc = _load_area_doc()
    pair = screen_region_by_name(area_doc, region_name) if area_doc else None
    bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
    if not isinstance(bbox, dict):
        logger.warning(
            "dsl exec click_next_red_dot_tab: region=%r not found in area.json",
            region_name,
        )
        ctx.result.update({"action": "unknown_region", "region": region_name})
        return

    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec click_next_red_dot_tab: capture_screen_bgr failed instance=%s",
            ctx.instance_id,
        )
        ctx.result.update({"action": "capture_failed", "region": region_name})
        return

    tabs = detect_tabs_in_strip(image, bbox)
    decision = pick_next_strip_action(tabs)
    ctx.result.update(
        {
            "action": decision.kind,
            "region": region_name,
            "tab_count": len(tabs),
            "red_dot_indices": [t.index for t in tabs if t.has_red_dot],
            "active_indices": [t.index for t in tabs if t.active],
        }
    )
    if decision.kind != "click_tab" or decision.tab_index is None:
        next_region = str((ctx.args or {}).get("next_region") or "").strip()
        if decision.kind == "advance_page" and next_region:
            pair_next = screen_region_by_name(area_doc, next_region) if area_doc else None
            bbox_next = (
                pair_next[1].get("bbox")
                if pair_next and isinstance(pair_next[1], dict)
                else None
            )
            if not isinstance(bbox_next, dict):
                logger.warning(
                    "dsl exec click_next_red_dot_tab: next_region=%r not found in area.json",
                    next_region,
                )
                ctx.result.update(
                    {"action": "unknown_next_region", "next_region": next_region}
                )
                return

            h, w = image.shape[:2]
            x = int(
                round(
                    (float(bbox_next["x"]) + float(bbox_next["width"]) / 2.0)
                    / 100.0
                    * w
                )
            )
            y = int(
                round(
                    (float(bbox_next["y"]) + float(bbox_next["height"]) / 2.0)
                    / 100.0
                    * h
                )
            )
            tapped = False
            try:
                tapped = bool(
                    await asyncio.to_thread(
                        actions.tap,
                        ctx.instance_id,
                        Point(x, y),
                        approval_region=next_region,
                    )
                )
            except Exception:
                logger.exception(
                    "dsl exec click_next_red_dot_tab: next tap failed at "
                    "(%d,%d) instance=%s",
                    x,
                    y,
                    ctx.instance_id,
                )
                ctx.result.update(
                    {"action": "next_tap_failed", "next_region": next_region}
                )
                return
            ctx.result.update(
                {
                    "action": "advanced_page" if tapped else "next_tap_blocked",
                    "next_region": next_region,
                    "tap_x": x,
                    "tap_y": y,
                }
            )
            logger.info(
                "dsl exec click_next_red_dot_tab: instance=%s region=%s "
                "next_region=%s tap=(%d,%d) tapped=%s",
                ctx.instance_id,
                region_name,
                next_region,
                x,
                y,
                tapped,
            )
            return
        logger.info(
            "dsl exec click_next_red_dot_tab: instance=%s region=%s action=%s "
            "tabs=%d red_dot_indices=%s",
            ctx.instance_id,
            region_name,
            decision.kind,
            len(tabs),
            ctx.result["red_dot_indices"],
        )
        return

    tab = next((t for t in tabs if t.index == decision.tab_index), None)
    if tab is None:
        logger.warning(
            "dsl exec click_next_red_dot_tab: selected tab=%s disappeared",
            decision.tab_index,
        )
        ctx.result.update({"action": "selected_tab_missing"})
        return

    b = tab.bbox_percent
    h, w = image.shape[:2]
    x = int(round((float(b["x"]) + float(b["width"]) / 2.0) / 100.0 * w))
    y = int(round((float(b["y"]) + float(b["height"]) / 2.0) / 100.0 * h))
    point = Point(x, y)
    tapped = False
    try:
        tapped = bool(
            await asyncio.to_thread(
                actions.tap,
                ctx.instance_id,
                point,
                approval_region=region_name,
            )
        )
    except Exception:
        logger.exception(
            "dsl exec click_next_red_dot_tab: tap failed at (%d,%d) instance=%s",
            point.x,
            point.y,
            ctx.instance_id,
        )
        ctx.result.update({"action": "tap_failed", "tab_index": tab.index})
        return

    ctx.result.update(
        {
            "action": "clicked_tab" if tapped else "tap_blocked",
            "tab_index": tab.index,
            "tap_x": point.x,
            "tap_y": point.y,
        }
    )
    logger.info(
        "dsl exec click_next_red_dot_tab: instance=%s region=%s tab=%d "
        "tap=(%d,%d) tapped=%s",
        ctx.instance_id,
        region_name,
        tab.index,
        point.x,
        point.y,
        tapped,
    )


async def _exec_put_all_red_dots(ctx: DslExecContext) -> None:
    """Tap every red dot the detector finds on the current frame, then re-scan.

    Repeats until either:
    * a fresh frame returns zero red-dot detections, or
    * the global ``_PUT_ALL_RED_DOTS_MAX_TAPS`` cap is reached.

    Default: full-screen ``find_red_dots``. With ``region: <name>`` on the
    ``exec:`` step, the search is restricted to that named region from
    ``area.json`` — useful when a screen has dots outside the panel you want
    to drain (e.g. the bottom nav bar) and you don't want to chase them.
    """
    region_name = str((ctx.args or {}).get("region") or "").strip()
    region_pct: tuple[float, float, float, float] | None = None
    if region_name:
        area_doc = _load_area_doc()
        pair = screen_region_by_name(area_doc, region_name) if area_doc else None
        bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
        if not isinstance(bbox, dict):
            logger.warning(
                "dsl exec put_all_red_dots: region=%r not found in area.json — aborting",
                region_name,
            )
            return
        try:
            region_pct = (
                float(bbox["x"]),
                float(bbox["y"]),
                float(bbox["width"]),
                float(bbox["height"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dsl exec put_all_red_dots: region=%r has malformed bbox — aborting",
                region_name,
            )
            return

    actions = dsl_runtime.bot_actions()
    taps_total = 0
    iteration = 0
    # (x, y, hits) — taps within ``_PUT_ALL_RED_DOTS_DUP_RADIUS_PX`` of an entry
    # increment its ``hits``; once it reaches the cap the (x, y) is appended to
    # ``filtered_points`` and future detections in the radius are skipped.
    # Coords are absolute (full-frame) regardless of the region crop.
    tap_history: list[tuple[int, int, int]] = []
    filtered_points: list[tuple[int, int]] = []
    radius_sq = _PUT_ALL_RED_DOTS_DUP_RADIUS_PX * _PUT_ALL_RED_DOTS_DUP_RADIUS_PX

    def _near(ax: int, ay: int, bx: int, by: int) -> bool:
        dx, dy = ax - bx, ay - by
        return dx * dx + dy * dy <= radius_sq

    while taps_total < _PUT_ALL_RED_DOTS_MAX_TAPS:
        iteration += 1
        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        except Exception:
            logger.exception(
                "dsl exec put_all_red_dots: capture_screen_bgr failed instance=%s",
                ctx.instance_id,
            )
            return
        hi = int(image.shape[0])
        if region_pct is not None:
            H, W = image.shape[:2]
            px = max(0, int(round(region_pct[0] / 100.0 * W)))
            py = max(0, int(round(region_pct[1] / 100.0 * H)))
            pw = max(0, int(round(region_pct[2] / 100.0 * W)))
            ph = max(0, int(round(region_pct[3] / 100.0 * H)))
            px2 = min(W, px + pw)
            py2 = min(H, py + ph)
            patch = image[py:py2, px:px2]
            if patch.size == 0:
                logger.warning(
                    "dsl exec put_all_red_dots: region=%r resolved to empty crop "
                    "(%d,%d %dx%d in %dx%d) — aborting",
                    region_name, px, py, pw, ph, W, H,
                )
                return
            # ``image_h_for_norm`` stays the full screen height so the radius
            # bounds match dots seen at full resolution — without this a small
            # ROI would silently shrink the expected dot size.
            dots_local = find_red_dots(patch, image_h_for_norm=hi)
            dots = [
                SimpleNamespace(
                    cx=d.cx + px,
                    cy=d.cy + py,
                    radius=d.radius,
                    score=d.score,
                )
                for d in dots_local
            ]
        else:
            dots = find_red_dots(image, image_h_for_norm=hi)
        if filtered_points:
            dots = [
                d
                for d in dots
                if not any(
                    _near(int(round(d.cx)), int(round(d.cy)), fx, fy)
                    for fx, fy in filtered_points
                )
            ]
        if not dots:
            logger.info(
                "dsl exec put_all_red_dots: instance=%s done iter=%d taps=%d (no dots)",
                ctx.instance_id,
                iteration,
                taps_total,
            )
            return
        # Tap one dot per iteration so each tap settles before we re-scan; tapping
        # all detections in a single frame risks tapping into a popup that opened
        # from the previous tap.
        dot = dots[0]
        point = Point(int(round(dot.cx)), int(round(dot.cy)))
        hit_idx = next(
            (
                i
                for i, (hx, hy, _) in enumerate(tap_history)
                if _near(point.x, point.y, hx, hy)
            ),
            None,
        )
        if hit_idx is None:
            tap_history.append((point.x, point.y, 1))
        else:
            hx, hy, hits = tap_history[hit_idx]
            hits += 1
            tap_history[hit_idx] = (hx, hy, hits)
            if hits >= _PUT_ALL_RED_DOTS_DUP_MAX_HITS:
                filtered_points.append((hx, hy))
                logger.info(
                    "dsl exec put_all_red_dots: instance=%s point=(%d,%d) "
                    "tapped %d× within %dpx — filtering area for the rest of this sweep",
                    ctx.instance_id,
                    hx,
                    hy,
                    hits,
                    _PUT_ALL_RED_DOTS_DUP_RADIUS_PX,
                )
        try:
            tapped = bool(
                await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    point,
                    approval_region="put_all_red_dots",
                )
            )
        except Exception:
            logger.exception(
                "dsl exec put_all_red_dots: tap failed at (%d,%d) instance=%s",
                point.x,
                point.y,
                ctx.instance_id,
            )
            return
        # ``BotActions.tap`` returns ``False`` when the operator rejects the
        # approval (or the slot is busy). Without this check the loop would
        # re-capture the same frame, find the same dot, and re-prompt forever
        # until the global tap cap kicks in. Bail immediately so the operator's
        # "no" actually stops the sweep.
        if not tapped:
            logger.info(
                "dsl exec put_all_red_dots: instance=%s tap at (%d,%d) "
                "blocked/rejected — aborting sweep (taps=%d)",
                ctx.instance_id, point.x, point.y, taps_total,
            )
            return
        taps_total += 1
        logger.info(
            "dsl exec put_all_red_dots: instance=%s iter=%d tap=(%d,%d) score=%.2f total=%d",
            ctx.instance_id,
            iteration,
            point.x,
            point.y,
            dot.score,
            taps_total,
        )
        await asyncio.sleep(_PUT_ALL_RED_DOTS_TAP_DELAY_S)
        await asyncio.sleep(_PUT_ALL_RED_DOTS_RESCAN_DELAY_S)
    logger.info(
        "dsl exec put_all_red_dots: instance=%s reached max-taps cap (%d) — stopping",
        ctx.instance_id,
        _PUT_ALL_RED_DOTS_MAX_TAPS,
    )


# Pop-ups stack (reward → next-reward → "tap to continue"); bound the
# dismiss loop so a misclassified frame can't spam the device.
