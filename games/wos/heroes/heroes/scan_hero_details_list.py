"""``exec: scan_hero_details_list`` — parse the hero **Details** popup into state.

The Details popup is a scrollable list. Each visible row shows a hero PORTRAIT (left)
with ``Lv. N`` + star snowflakes beneath it, then ``Skill Details`` (3 exploration-skill
icons, each with a ``Lv. K`` badge) and ``Gear Details`` (a widget + 4 gear pieces, each
with a ``Lv. G`` badge). The screen carries **no hero names**, and later game versions also
list **Experts** at the top — so identity is by portrait template match (the SAME
wiki-portrait NCC the heroes-grid scan uses, :func:`navigation.hero_grid_search`). Expert
rows simply don't match the hero library and are skipped.

**Portrait-anchored**: we slide the matcher down the left column to find each hero's
portrait (id + exact y), then read its fields at fixed offsets from that portrait — robust
to scroll position, the Expert header, and small per-row drift (no fixed row pitch).

The level / skill / gear numbers feed the arena optimizer's stat-based strength estimate
(``games/wos/core/arena/optimizer._stat_strength``) — the investment signal this screen
exposes that the roster grid does not.

Split in two:
* :func:`detect_hero_rows` + :func:`parse_hero_details` — PURE: frame (+ an int-reader) →
  per-hero dict. Unit-tested with a synthetic frame; no device / OCR / Redis.
* :func:`_exec_scan_hero_details_list` — the thin DSL handler that captures, batch-OCRs the
  field bboxes (``digits`` preprocess) and persists.

Geometry below was measured on a live **720×1280** bs1 capture (portrait 138 px at x≈46;
rows at y≈460 / 736 / 1000; field offsets relative to the portrait) — identity + bboxes
are live-verified.

OCR: all numbers use the ``badge_digits`` preprocess (HSV-isolate the white/yellow glyphs →
black-on-white → 6× upscale; ``src/ocr/preprocess.badge_digits_for_ocr``), which the
grayscale modes couldn't read on the colored badges. Verified live: level ``80`` and gear
``20`` read cleanly. Recall is not yet 100 % across every row/piece (the badges are ~14 px,
so a few read one digit) — the per-row field bboxes below are the knob to tighten if a piece
is dropped; the optimizer averages whatever lands.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cv2

from config.state_store import get_state_store
from layout.types import Point, Region
from navigation.hero_grid_search import _load_hero_template_gray, match_hero_portrait
from tasks import dsl_runtime

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# --- Geometry (measured on a live 720×1280 Details capture) ------------------
_PORTRAIT_SCALE_PX = 138          # on-screen hero portrait size
_PORTRAIT_X_BAND = (0, 210)       # left column the portrait sits in
_DETECT_THRESHOLD = 0.6           # NCC floor to accept a portrait as a hero
_DETECT_Y_STEP = 4                # slide step down the column

# Field bboxes as (dx, dy, w, h) offsets from the portrait's top-left corner. The dy is
# the centre of the field's valid window; the OCR pass sweeps ±_Y_JITTERS around it, so
# the exact value is forgiving (badge positions drift a few px between rows / accounts).
_LEVEL_OFF = (9, 154, 90, 18)     # "Lv. 80" under the portrait (tight: avoid portrait/star bleed)
_SKILL_OFFS = ((172, 80, 56, 22), (240, 80, 56, 22), (308, 80, 56, 22))
# The 4 real gear pieces (the leftmost icon is the widget, scale 1-10 with a "+N"
# badge not a "Lv. N" — excluded so it doesn't skew the gear-level average).
_GEAR_OFFS = (
    (263, 180, 66, 26), (354, 180, 66, 26),
    (445, 180, 66, 26), (536, 180, 66, 26),
)
# Vertical jitters (px) tried per field; the first that OCRs a number wins.
_Y_JITTERS = (0, -8, 8)
# Details popup is scrollable (shows ~3 of 5 heroes). Swipe up within the body to reveal
# the rows below the fold; from/to are frame pixels (a ~420 px upward flick ≈ 1.5 rows).
_SCROLL_FROM = (360, 860)
_SCROLL_TO = (360, 440)

_NUM_RE = re.compile(r"\d{1,3}")
# Plausible ranges — reject OCR misreads outside them (e.g. a stray "54" for a skill).
_LEVEL_MAX = 99
_SKILL_MAX = 10
_GEAR_MAX = 100


@dataclass(frozen=True, slots=True)
class DetectedRow:
    hero_id: str
    x: int           # portrait top-left
    y: int
    score: float


def detect_hero_rows(
    frame_bgr: np.ndarray,
    *,
    scale_px: int = _PORTRAIT_SCALE_PX,
    threshold: float = _DETECT_THRESHOLD,
) -> list[DetectedRow]:
    """Find each hero portrait by sliding the matcher down the left column.

    Returns rows top-to-bottom, one per identified hero (the strongest match in each
    portrait-height band). Expert / blank rows that don't match the hero library are
    omitted — exactly what we want for a heroes-only roster read.
    """
    if frame_bgr.ndim != 3:
        msg = "frame_bgr must be HxWx3 BGR"
        raise ValueError(msg)
    h = frame_bgr.shape[0]
    x0, x1 = _PORTRAIT_X_BAND
    hits: list[DetectedRow] = []
    y = 0
    while y + scale_px <= h:
        patch = frame_bgr[y:y + scale_px, x0:x1]
        r = match_hero_portrait(patch, threshold=threshold, scale_px=scale_px)
        if r is not None:
            # Refine the portrait's exact top-left from the matched template's peak
            # (the band starts at x0, but the portrait sits a margin in) so the field
            # offsets anchor on the real portrait, not the band edge.
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            tpl = _load_hero_template_gray(r[0], scale_px)
            _, _, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
            hits.append(DetectedRow(hero_id=r[0], x=x0 + loc[0], y=y + loc[1], score=r[1]))
        y += _DETECT_Y_STEP
    # Non-max suppression by y: collapse a run of overlapping windows to its peak.
    rows: list[DetectedRow] = []
    for hit in hits:
        if rows and hit.y - rows[-1].y < scale_px // 2:
            if hit.score > rows[-1].score:
                rows[-1] = hit
        else:
            rows.append(hit)
    return rows


def _abs(row: DetectedRow, off: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    dx, dy, w, h = off
    return (row.x + dx, row.y + dy, w, h)


def row_field_bboxes(row: DetectedRow) -> dict[str, Any]:
    """Absolute (x, y, w, h) bboxes for a row's level / skills / gears."""
    return {
        "level": _abs(row, _LEVEL_OFF),
        "skills": [_abs(row, o) for o in _SKILL_OFFS],
        "gears": [_abs(row, o) for o in _GEAR_OFFS],
    }


def _num(text: str) -> int | None:
    m = _NUM_RE.search(text or "")
    return int(m.group(0)) if m else None


def _vote(values: list[int]) -> int | None:
    """Resolve a field from its jitter reads: the most frequent value, ties broken toward
    more digits. The true number usually lands in ≥2 of the 3 jitter positions, so a
    single-jitter misread (often a dropped digit, 20→2) loses on frequency."""
    if not values:
        return None
    counts = Counter(values)
    return max(set(values), key=lambda v: (counts[v], len(str(v))))


def parse_hero_details(
    frame_bgr: np.ndarray,
    read_int: Callable[[tuple[int, int, int, int]], int | None],
) -> dict[str, dict[str, Any]]:
    """Identify each row by portrait and read its level / skills / gear.

    PURE: ``read_int(bbox)`` returns the integer OCR'd from a field bbox (the caller
    batches the real digit-OCR; tests pass a lookup). Returns ``{hero_id: {level, skill,
    gear, match_score}}``. ``skill`` is the min of the 3 skill levels (the limiting one);
    ``gear`` is the list of gear-piece levels read (the widget yields nothing → omitted).
    """
    out: dict[str, dict[str, Any]] = {}
    for row in detect_hero_rows(frame_bgr):
        boxes = row_field_bboxes(row)
        entry: dict[str, Any] = {"match_score": round(row.score, 3)}
        level = read_int(boxes["level"])
        if level is not None and 1 <= level <= _LEVEL_MAX:
            entry["level"] = level
        skills = [
            s for s in (read_int(b) for b in boxes["skills"]) if s is not None and 1 <= s <= _SKILL_MAX
        ]
        if skills:
            entry["skill"] = sorted(skills)[len(skills) // 2]  # median — robust to a misread
        gears = [
            g for g in (read_int(b) for b in boxes["gears"]) if g is not None and 1 <= g <= _GEAR_MAX
        ]
        if gears:
            entry["gear"] = gears
        prev = out.get(row.hero_id)
        if prev is None or row.score > prev.get("match_score", 0.0):
            out[row.hero_id] = entry
    return out


async def read_detail_rows(frame_bgr: np.ndarray) -> dict[str, dict[str, Any]]:
    """Detect hero rows by portrait and OCR each row's level/skill/gear (sweep + vote).

    Shared by the own-roster reader and the enemy-scout reader (the Details popup is the
    same for your own and an opponent's Defensive Lineup). Returns ``{hero_id: entry}``.

    Each field bbox is OCR'd at a few y-jitters (badges drift a few px between rows /
    accounts) and resolved by mode vote; level/skill use the white colour mask, gear the
    yellow one (a combined mask cross-contaminates — yellow catches the portrait fire-glow
    in the level band).
    """
    rows = detect_hero_rows(frame_bgr)
    if not rows:
        return {}
    base_fields: list[tuple[tuple[int, int, int, int], str]] = []
    for row in rows:
        fb = row_field_bboxes(row)
        base_fields.append((fb["level"], "badge_white"))
        base_fields.extend((b, "badge_white") for b in fb["skills"])
        base_fields.extend((b, "badge_yellow") for b in fb["gears"])
    candidates = [
        (base, mode, (base[0], base[1] + dj, base[2], base[3]))
        for base, mode in base_fields
        for dj in _Y_JITTERS
    ]
    votes: dict[tuple[int, int, int, int], list[int]] = {b: [] for b, _ in base_fields}
    try:
        regions = [Region(*jb) for _, _, jb in candidates]
        results = await dsl_runtime.ocr_client().ocr_regions(
            frame_bgr, regions,
            region_ids=[f"hd_{i}" for i in range(len(regions))],
            region_preprocess=[mode for _, mode, _ in candidates],
        )
        for (base, _, _), res in zip(candidates, results, strict=False):
            n = _num((res.text or "").strip())
            if n is not None:
                votes[base].append(n)
    except Exception:
        logger.exception("read_detail_rows: OCR failed")
    nums = {b: _vote(v) for b, v in votes.items()}
    return parse_hero_details(frame_bgr, lambda b: nums.get(b))


async def read_detail_rows_scrolled(
    actions: Any, instance_id: str, *, swipes: int = 2, settle_s: float = 1.2
) -> list[tuple[str, dict[str, Any]]]:
    """Read EVERY hero across the scrollable Details popup (it shows ~3 of 5 at once).

    Capture → read the visible rows → swipe up to reveal the next → repeat, merging by
    hero id (the better-matched read wins; a field one frame missed is filled from the
    other). Returns ``[(hero_id, entry), …]`` in first-seen order — top-to-bottom, i.e.
    the defensive-lineup slot order.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for i in range(max(0, swipes) + 1):
        frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        for hid, e in (await read_detail_rows(frame)).items():
            if hid not in merged:
                order.append(hid)
                merged[hid] = e
                continue
            cur = merged[hid]
            base, fill = (e, cur) if e.get("match_score", 0) >= cur.get("match_score", 0) else (cur, e)
            out = dict(base)
            for k, v in fill.items():
                if out.get(k) is None and v is not None:
                    out[k] = v
            merged[hid] = out
        if i < swipes:
            await asyncio.to_thread(
                actions.swipe, instance_id, Point(*_SCROLL_FROM), Point(*_SCROLL_TO), 400
            )
            await asyncio.sleep(settle_s)
    return [(hid, merged[hid]) for hid in order]


async def _exec_scan_hero_details_list(ctx: DslExecContext) -> None:
    """Capture the Details popup, parse every visible hero row, merge into hero state."""
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning("scan_hero_details_list: empty player_id — skipping")
        return

    actions = dsl_runtime.bot_actions()
    try:
        frame = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception("scan_hero_details_list: capture failed instance=%s", ctx.instance_id)
        return

    parsed = await read_detail_rows(frame)
    if not parsed:
        logger.info("scan_hero_details_list: no hero rows matched instance=%s", ctx.instance_id)
        return

    try:
        store = get_state_store().get_or_create(player_id)
    except Exception:
        logger.exception("scan_hero_details_list: state store init failed player=%s", player_id)
        return

    snap = store.snapshot()
    existing = dict(snap.heroes.entries) if snap.heroes.entries else {}
    now = time.time()
    flat: dict[str, Any] = {}
    for hid, fresh in parsed.items():
        prev = existing.get(hid)
        entry: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}
        entry.update(fresh)
        entry["details_seen_at"] = now
        flat[f"heroes.entries.{hid}"] = entry

    try:
        await asyncio.to_thread(store.update_from_flat, flat)
    except Exception:
        logger.exception("scan_hero_details_list: persist failed player=%s", player_id)
        return

    logger.info(
        "scan_hero_details_list: instance=%s player=%s heroes=%d",
        ctx.instance_id, player_id, len(parsed),
    )
