"""``exec: scan_hero_details`` — chevron-walk the hero roster, reading each card.

Steps through every owned hero's detail page via the **next-unit chevron** in the
grid order ``scan_heroes_grid`` recorded (``hero_grid_positions``), OCR'ing name +
level per card and refreshing ``heroes.entries.<id>``. Stops when it wraps back to
a hero already seen (or the roster count is reached).

This is the routing the grid order enables: because we know who's next, a single
chevron walk visits the whole roster without bouncing back to the grid each time.
The surrounding scenario opens the FIRST hero (``heroes.grid.r0c0``) before calling
this; the walk takes over from there. Star/skill reads (detail-only data the grid
can't see) are a deliberate follow-up — this pass establishes the walk + refreshes
level from the authoritative detail card.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from config.building_name_parser import normalise_building_lookup_text as _norm
from config.heroes import get_hero_registry
from config.paths import repo_root
from config.state_store import get_state_store
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Point, Region
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_HERO_GRID_POS_FMT = "wos:instance:{instance_id}:hero_grid_positions"
_CELL_RE = re.compile(r"^r(\d+)c(\d+)$")
_STEP_SETTLE_S = 0.9

# Star-tier detection (segment-level, calibrated on-device at 720×1280). Each hero
# shows a row of 5 stars; each star is 6 cyan segments. We count bright-cyan pixels
# per fixed star box and divide by one segment's area, so a partially-filled star
# reads its real fraction (e.g. Charlie = [6,6,6,6,1] → 4 stars + 1/6). Star colour
# is the game's, not hero-specific, so the mask generalises across the roster.
_STAR_CENTERS = (30.0, 40.0, 50.0, 60.0, 70.0)  # x% of the 5 star icons
_STAR_HALF_W = 4.0                               # half-width of each star box (%)
_STAR_Y = (65.5, 71.5)                           # star row band (y%)
_SEG_PX = 230                                    # bright pixels per filled segment


def detect_star_segments(frame: Any) -> list[int]:
    """Filled segments (0–6) for each of the 5 stars, by bright-cyan pixel area."""
    h, w = frame.shape[:2]
    band = frame[int(_STAR_Y[0] / 100 * h):int(_STAR_Y[1] / 100 * h)].astype(int)
    b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    bright = (b > 140) & (g > 140) & ((b - r) > 20)
    segs: list[int] = []
    for c in _STAR_CENTERS:
        x0, x1 = int((c - _STAR_HALF_W) / 100 * w), int((c + _STAR_HALF_W) / 100 * w)
        segs.append(min(6, round(int(bright[:, x0:x1].sum()) / _SEG_PX)))
    return segs


def _region_px(area: dict[str, Any], name: str, w: int, h: int) -> tuple[int, int, int, int] | None:
    pair = screen_region_by_name(area, name)
    if pair is None or not isinstance(pair[1].get("bbox"), dict):
        return None
    b = pair[1]["bbox"]
    try:
        return (
            int(float(b["x"]) / 100 * w),
            int(float(b["y"]) / 100 * h),
            int(float(b["width"]) / 100 * w),
            int(float(b["height"]) / 100 * h),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _ordered_hids(redis: Any, instance_id: str) -> list[str]:
    """Owned heroes in grid (chevron) order: sorted by (row, col)."""
    key = _HERO_GRID_POS_FMT.format(instance_id=instance_id)
    try:
        raw = await redis.hgetall(key)
    except Exception:
        return []
    rows: list[tuple[int, int, str]] = []
    for k, v in (raw or {}).items():
        hid = k.decode() if isinstance(k, bytes) else str(k)
        cell = (v.decode() if isinstance(v, bytes) else str(v)).strip()
        m = _CELL_RE.match(cell)
        if m:
            rows.append((int(m.group(1)), int(m.group(2)), hid))
    rows.sort()
    return [hid for _, _, hid in rows]


async def _exec_scan_hero_details(ctx: DslExecContext) -> None:
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning("dsl exec scan_hero_details: empty player_id — skipping")
        return
    redis = ctx.redis_client

    # Each card is identified by its NAME (OCR'd with the title_line preprocess that
    # reads the white outlined header) — NOT by grid order: the chevron's next/prev
    # sequence does NOT match the grid's power sort, so ordered[i] would mislabel
    # heroes. ``ordered`` is used only for the walk length / wrap cap.
    ordered = await _ordered_hids(redis, ctx.instance_id) if redis is not None else []
    cap = (len(ordered) or 30) + 2

    actions = dsl_runtime.bot_actions()
    oc = dsl_runtime.ocr_client()
    area = load_area_doc(repo_root())
    registry = get_hero_registry()
    try:
        store = get_state_store().get_or_create(player_id)
    except Exception:
        logger.exception("dsl exec scan_hero_details: state store init failed player=%s", player_id)
        return

    visited: list[str] = []
    seen: set[str] = set()
    for _ in range(cap):
        try:
            frame = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        except Exception:
            logger.exception("dsl exec scan_hero_details: capture failed")
            break
        if frame is None:
            break
        h, w = frame.shape[:2]

        name_box = _region_px(area, "page.heroes.unit.name", w, h)
        if name_box is None:
            break
        name = ((await oc.ocr_region(
            frame, Region(*name_box), region_id="hero.name", preprocess="title_line"
        )).text or "").strip()
        hid = _norm(name)
        if not hid or hid in seen:
            break  # wrapped back to a seen hero (or an unreadable card)

        level: int | None = None
        lvl_box = _region_px(area, "page.heroes.unit.level", w, h)
        if lvl_box is not None:
            lt = (await oc.ocr_region(
                frame, Region(*lvl_box), region_id="hero.level", preprocess="fast_digits"
            )).text or ""
            digits = "".join(c for c in lt if c.isdigit())
            level = int(digits) if digits else None

        snap = store.snapshot()
        entry: dict[str, Any] = dict(snap.heroes.entries.get(hid, {})) if snap.heroes.entries else {}
        hero_def = registry.by_id(hid)
        entry["name"] = hero_def.name if hero_def is not None else name
        if level is not None:
            entry["level"] = level
        # Star tier from the 6-segment-per-star row (detail-only — the grid can't
        # see it). total filled segments → full stars + the in-progress segment.
        total_seg = sum(detect_star_segments(frame))
        entry["star"] = total_seg // 6
        entry["star_segment"] = total_seg % 6
        entry["detail_seen_at"] = time.time()
        try:
            await asyncio.to_thread(store.update_from_flat, {f"heroes.entries.{hid}": entry})
        except Exception:
            logger.exception("dsl exec scan_hero_details: persist failed hero=%s", hid)
            break
        seen.add(hid)
        visited.append(hid)

        # Step to the next hero via the chevron.
        next_box = _region_px(area, "page.heroes.unit.next_unit", w, h)
        if next_box is None:
            break
        cx, cy = next_box[0] + next_box[2] // 2, next_box[1] + next_box[3] // 2
        try:
            await asyncio.to_thread(actions.tap, ctx.instance_id, Point(cx, cy))
        except Exception:
            logger.exception("dsl exec scan_hero_details: next-chevron tap failed")
            break
        await asyncio.sleep(_STEP_SETTLE_S)

    ctx.result.update({"action": "walked", "visited": visited, "count": len(visited)})
    logger.info(
        "dsl exec scan_hero_details: player=%s visited=%d order=%s",
        player_id, len(visited), visited,
    )
