"""Research Center exec handlers — value-greedy planner wiring + tech-level reader.

Mirrors the building-planner spine (``games/wos/core/building/common/exec.py``):

* ``plan_next_research`` — pure compute. Reads ``research.levels.*`` +
  ``research.center.level`` out of the instance-state hash, runs the value-greedy
  research planner, and stashes its pick in ``planner.next_research`` (+ name /
  branch / to-level / reason) so the dispatcher and the operator can see what to
  research next. Safe to run anywhere; no device IO.

* ``sync_research_levels`` — the on-device reader that *fills* those inputs. It
  OCRs each tech-tree tile's (name, level), maps the name to a node id from
  ``db/research.yaml`` via :func:`_match_tile_to_node`, and mirrors
  ``research.levels.<id>`` + ``research.center.level`` to the instance hash.

* ``start_planned_research`` — the dispatcher. Reads ``planner.next_research_*``
  (set by ``plan_next_research``), opens the tech tree, switches to the planned
  tech's branch tab, scroll-locates its tile by name, taps it, and taps the
  in-tree **Research** button (never the gem-spending *Finish*) to start it.

The on-device geometry below was calibrated against a live 720×1280 Research
Center capture (see ``references/`` + ``area.yaml``). Both device handlers tap
through :class:`BotActions` with ``require_approval=True``, so click-approval mode
still gates the real taps; ``botctl drive --no-approval`` clears the gate.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import cv2

from layout.types import Point, Region

if TYPE_CHECKING:
    from games.wos.core.research.planner import ResearchGraph, ResearchNode

logger = logging.getLogger(__name__)

# Combined name+tier score a tile must clear to bind to a node (see
# :func:`_match_tile_to_node`). Lower than a plain fuzzy threshold because the
# frontier (researchable) tiles OCR poorly — "Skirmishing IV" often reads as
# "siting IV" (~70) — but the tier bonus/penalty keeps it unambiguous.
_TILE_MATCH_THRESHOLD = 70.0

_ROMAN_TIERS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

# --- on-device geometry (percent of the 720×1280 game frame) ----------------
# Building-popup ring (after navigate_to_building open:true):
_RC_LEVEL_BBOX = (28.5, 40.0, 7.5, 5.0)    # blue "<lvl>" badge left of "Research Center"
_OPEN_BTN_XY = (65.0, 67.0)                # microscope button → opens the tech tree
# Tech-tree screen:
_TITLE_BBOX = (10.0, 1.6, 34.0, 3.2)       # "Tech Research" (screen discriminator)
_TAB_XY = {                                # branch tab centres (Growth/Economy/Battle)
    "growth": (18.5, 9.5),
    "economy": (48.0, 9.5),
    "battle": (83.5, 9.5),
}
_COL_X_PCT = (19.0, 49.7, 80.8)            # left / centre / right tile columns
# Name-row centres span the visible height; combined with scrolling they catch a
# tile wherever it lands. Decisive calibration (bs3 growth frame): a tile name OCRs
# cleanly only within a ~±1% band of its true centre, and tiles snap to a ~20.7%
# tier pitch at a scroll-dependent phase — so a sparse grid (the old 8 rows at
# ~10.3% pitch) lands in the dead zones for *most* tiles on any given frame (caught
# 0/6 on one real growth frame; the tab read empty). A 3% pitch (~±1.5%) keeps a row
# inside every tile's narrow band regardless of phase (caught 5/6 on that same frame
# vs 0/6; the rest fall to the next scroll step + maxed-predecessor backfill). The
# @13 phase deliberately includes y≈28 so the clipped top-tier tile is read too.
# Costlier per frame, but the sweep is a 6-hourly reader and denser frames hit the
# dry-stop sooner, so it largely self-compensates. Mirrors the dispatcher's dense
# locate grid (_LOCATE_ROW_Y_PCT), which solved the identical garble for tap-targeting.
_NAME_ROW_Y_PCT = tuple(round(13.0 + 3.0 * i, 1) for i in range(27))  # 13.0 … 91.0
_NAME_W_PCT, _NAME_H_PCT = 26.0, 6.0       # tile-name OCR cell (title_line)
# The dispatcher's tile-LOCATE samples a DENSE 2%-pitch grid. Decisive finding: a
# tile name OCRs cleanly only within a ~±1% band of its true centre (2% off → garble),
# and the sweep's sparse rows land in those gaps for half the tiles. A 2% scan
# guarantees every tile's narrow sweet-spot is hit on the frames it's visible. The
# sibling-tab reset puts the tab at the top first; the tap is clamped off the tab bar.
# (Costly — but the dispatcher runs only when a research slot is idle.)
_LOCATE_ROW_Y_PCT = tuple(round(15.0 + 2.0 * i, 1) for i in range(39))  # 15.0 … 91.0
_TILE_ICON_DY_PCT = 7.8                     # tile icon sits this far above the name centre (open target)
_LOCATE_TAP_MIN_Y_PCT = 13.0               # never tap into the tab bar (a near-top match clamps here)
_REFINE_STEP_PCT = 1.0                      # fine sub-scan step when pinpointing a matched name's y
_PILL_DY_PCT = 2.73                        # level pill sits this far ABOVE the name centre
_PILL_HALF_W_PCT, _PILL_HALF_H_PCT = 4.72, 1.10
# Tech-detail popup (after tapping a tile):
_RESEARCH_BTN_XY = (72.5, 77.4)            # blue "Research" (start); NOT orange "Finish"
_RESEARCH_BTN_BBOX = (53.0, 74.0, 39.0, 4.7)  # the blue button's "Research" label (top of the button)

_MAX_SCROLL_STEPS = 8                       # sweep: per tab; early-stops after _DRY_STOP dry frames
_LOCATE_SCROLL_STEPS = 12                   # dispatcher: gentler/longer scan for one target tile
_DRY_STOP = 3                               # consecutive no-new-tile frames → next tab
_PILL_THRESHOLD = 170                       # white pill text/border vs dark interior
_PILL_UPSCALE = 5


# --- instance-state hash helpers (bytes- or str-keyed, like building's) ------
def _read_research_levels(state: dict) -> dict[str, int]:
    """Pull ``research.levels.<node_id>`` ints out of an instance-state hash."""
    prefix = "research.levels."
    levels: dict[str, int] = {}
    for raw_k, raw_v in (state or {}).items():
        k = raw_k.decode() if isinstance(raw_k, bytes) else str(raw_k)
        if not k.startswith(prefix):
            continue
        v = raw_v.decode() if isinstance(raw_v, bytes) else str(raw_v)
        try:
            levels[k[len(prefix):]] = int(v)
        except (TypeError, ValueError):
            continue
    return levels


def _state_get_int(state: dict, field: str, default: int = 0) -> int:
    """One ``field`` out of a bytes- or str-keyed instance-state hash, as int."""
    for raw_k, raw_v in (state or {}).items():
        k = raw_k.decode() if isinstance(raw_k, bytes) else str(raw_k)
        if k != field:
            continue
        v = raw_v.decode() if isinstance(raw_v, bytes) else str(raw_v)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default
    return default


def _state_get_str(state: dict, field: str, default: str = "") -> str:
    """One ``field`` out of a bytes- or str-keyed instance-state hash, as str."""
    for raw_k, raw_v in (state or {}).items():
        k = raw_k.decode() if isinstance(raw_k, bytes) else str(raw_k)
        if k != field:
            continue
        return raw_v.decode() if isinstance(raw_v, bytes) else str(raw_v)
    return default


# --- tile-name → node-id fuzzy matcher (pure, unit-tested) -------------------
def _tile_tier(s: str) -> int | None:
    """Roman-numeral tier of a tech name's trailing token (``"Bandaging III"`` → 3).

    ``None`` when the last token isn't a clean roman numeral — OCR garbles tiers
    (``I`` → ``l``), so an unparseable tier just falls back to full-string fuzz.
    """
    toks = (s or "").lower().split()
    return _ROMAN_TIERS.get(toks[-1]) if toks else None


def _match_tile_to_node(name: str, graph: Any, *, threshold: float = _TILE_MATCH_THRESHOLD) -> str | None:
    """Resolve an OCR'd tech-tree tile name to a research node id.

    Score = ``token_sort_ratio`` over the full name + a roman-tier adjustment:
    when both the OCR'd name and the node have a *clean* trailing tier, an exact
    tier match is rewarded and a mismatch heavily penalised. This is what makes
    the lines distinguishable — tech names differ only by their tier suffix
    (``Skirmishing IV`` vs ``V`` vs ``VI``), and a tier-blind fuzzy match either
    confuses them or (with ``token_set_ratio``) lets a bare ``"iv"`` subset-match
    everything. Garbled tiers fall back to the full-string score. Returns ``None``
    when nothing clears ``threshold`` (unrecognised tiles are skipped, not mis-bound).
    """
    from rapidfuzz import fuzz

    target = (name or "").strip().lower()
    if not target:
        return None
    otier = _tile_tier(target)
    best_id: str | None = None
    best_score = 0.0
    for node_id, node in graph.nodes.items():
        node_name = str(node.name).lower()
        score = float(fuzz.token_sort_ratio(target, node_name))
        ntier = _tile_tier(node_name)
        if otier is not None and ntier is not None:
            score += 8.0 if otier == ntier else -25.0
        if score > best_score:
            best_id, best_score = node_id, score
    return best_id if best_score >= threshold else None


def _research_levels_from_ocr_rows(rows: Any, graph: Any) -> dict[str, int]:
    """Map ``[(tile_name, level), ...]`` OCR rows to ``{node_id: level}``.

    Pure: drops rows whose name doesn't resolve to a node or whose level isn't an
    int. The device sweep produces ``rows``; this is the testable mapping seam.
    """
    out: dict[str, int] = {}
    for row in rows or ():
        try:
            name, level = row
        except (TypeError, ValueError):
            continue
        node_id = _match_tile_to_node(name, graph)
        if node_id is None:
            continue
        try:
            out[node_id] = int(level)
        except (TypeError, ValueError):
            continue
    return out


def _infer_maxed_predecessors(levels: dict[str, int], graph: Any) -> dict[str, int]:
    """Fill maxed lower tiers implied by any tile the reader saw.

    In-game a tech tile is only *visible* once its same-line previous tier is
    **maxed** (the tier ladder — see the research planner). So every node we read,
    at any level (even 0/available), proves all of its same-line predecessors are
    maxed. Walking that chain backfills the maxed prereqs the OCR sweep didn't get
    to (deep trees, scroll gaps) so the planner doesn't re-pick an already-maxed
    low tier. Pure; takes ``max`` with anything already recorded.
    """
    out = dict(levels)
    for node_id in list(levels):
        pred = graph.tier_predecessor(node_id)
        while pred is not None:
            spec = graph.spec(pred)
            if spec is None:
                break
            if out.get(pred, -1) < spec.max_level:
                out[pred] = spec.max_level
            pred = graph.tier_predecessor(pred)
    return out


def _drop_unconfirmed_max(levels: dict[str, int], graph: Any) -> dict[str, int]:
    """Drop a tile claimed at MAX when its tier-successor was never seen.

    MAX means the whole tile is researched — and the game only unlocks (shows) the
    next tier on a line once the current tier is maxed. So a tile read at its max
    level is only *confirmed* MAX when a tier-successor was also read. A frontier
    tile whose current-level digit misread **up** to max (the tiny ``X/Y`` pill is
    unreliable) has no read successor and would otherwise look done — drop it so the
    planner still treats it as researchable. Run this **before**
    :func:`_infer_maxed_predecessors`: any genuinely-maxed tile that is a predecessor
    of a read successor is restored there, so this only strips unconfirmed max claims
    at the research frontier (where the pill misread actually hurts the planner).
    """
    has_successor = {graph.tier_predecessor(n) for n in graph.nodes}
    has_successor.discard(None)
    confirmed_pred = {graph.tier_predecessor(n) for n in levels}
    confirmed_pred.discard(None)
    out = dict(levels)
    for node_id, level in levels.items():
        spec = graph.spec(node_id)
        if spec is None or level < spec.max_level:
            continue
        if node_id in has_successor and node_id not in confirmed_pred:
            del out[node_id]
    return out


def branch_to_tab(branch: str) -> str:
    """Map a planner branch id to the on-screen tab key.

    The tree has three tabs — Growth / Economy / Battle. Troop branches
    (``t11_*`` / ``t12_*`` and anything not growth/economy) live under **Battle**.
    """
    b = (branch or "").strip().lower()
    if b.startswith("growth"):
        return "growth"
    if b.startswith("economy"):
        return "economy"
    return "battle"


# --- device-IO helpers (percent-bbox → pixels, OCR, taps) --------------------
def _px_region(bbox_pct: tuple[float, float, float, float], w: int, h: int) -> Region:
    x, y, bw, bh = bbox_pct
    return Region(int(x / 100 * w), int(y / 100 * h), int(bw / 100 * w), int(bh / 100 * h))


def _tap_pct(actions: Any, iid: str, x_pct: float, y_pct: float, w: int, h: int) -> None:
    actions.tap(iid, Point(int(x_pct / 100 * w), int(y_pct / 100 * h)))


async def _ocr_text(oc: Any, frame: Any, bbox_pct: tuple[float, float, float, float],
                    w: int, h: int, *, preprocess: str, region_id: str) -> str:
    res = await oc.ocr_region(frame, _px_region(bbox_pct, w, h),
                              region_id=region_id, preprocess=preprocess)
    return (res.text or "").strip()


async def _title_is_tree(oc: Any, frame: Any, w: int, h: int) -> bool:
    txt = await _ocr_text(oc, frame, _TITLE_BBOX, w, h,
                          preprocess="title_line", region_id="rc_title")
    return "research" in txt.lower()


async def _read_rc_level(oc: Any, frame: Any, w: int, h: int) -> int:
    """RC level off the popup name-plate's blue ``<lvl>`` badge.

    The level is white digits on a saturated blue circle: plain ``fast_digits`` on
    the raw crop reads it empty (Tesseract's binariser loses white-on-blue, and the
    badge's exact y drifts a few % with how the menu-teleport centres the camera).
    So threshold the bright glyphs out, upscale and read — the same trick the tile
    level pills use. Verified on bs3 (reads "19" across camera-position variance).
    """
    bx, by, bw_pct, bh_pct = _RC_LEVEL_BBOX
    x0, y0 = int(bx / 100 * w), int(by / 100 * h)
    crop = frame[y0:y0 + int(bh_pct / 100 * h), x0:x0 + int(bw_pct / 100 * w)]
    if crop.size == 0:
        return 0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, badge = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    up = cv2.resize(badge, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    tile = cv2.cvtColor(cv2.bitwise_not(up), cv2.COLOR_GRAY2BGR)
    res = await oc.ocr_region(tile, Region(0, 0, tile.shape[1], tile.shape[0]),
                              region_id="rc_center_level", preprocess="fast_digits")
    m = re.search(r"\d+", res.text or "")
    return int(m.group()) if m else 0


async def _read_pill_level(oc: Any, frame: Any, w: int, h: int,
                           cx_px: int, name_y_px: int, node: ResearchNode) -> tuple[int | None, str]:
    """OCR the level pill above a tile's name → current level (``MAX`` → max_level).

    The pill is small white text on a dark rounded badge; native OCR garbles it.
    We threshold the white text out, upscale, and read it — the leading digit of
    an ``X/Y`` pill is the current level; an all-letters read (``MAX``) means the
    node is maxed. Returns ``(level | None, raw_text)`` — ``None`` when nothing
    legible came back (the raw text is kept for diagnostics).
    """
    py = int(name_y_px - _PILL_DY_PCT / 100 * h)
    half_w = int(_PILL_HALF_W_PCT / 100 * w)
    half_h = int(_PILL_HALF_H_PCT / 100 * h)
    x0, x1 = cx_px - half_w, cx_px + half_w
    y0, y1 = py - half_h, py + half_h
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None, ""
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None, ""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, _PILL_THRESHOLD, 255, cv2.THRESH_BINARY)
    inv = cv2.bitwise_not(bw)
    up = cv2.resize(inv, None, fx=_PILL_UPSCALE, fy=_PILL_UPSCALE, interpolation=cv2.INTER_CUBIC)
    up = cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)
    res = await oc.ocr_region(up, Region(0, 0, up.shape[1], up.shape[0]),
                              region_id="rc_tile_level", preprocess="fast_line")
    txt = res.text or ""
    # The pill reads "current/max" (X/Y); the leading number is the current level.
    # Sanity-check it against the node's known max: a current ABOVE max is a digit
    # misread (e.g. "2/3" → "4"), not a real level, and must not be stored — it would
    # tell the planner a tech is further along than it is. Reject it so the
    # multi-frame sweep retries this tile on a better-aligned frame.
    nums = re.findall(r"\d+", txt)
    if nums:
        cur = int(nums[0])
        return (cur, txt) if cur <= node.max_level else (None, txt)
    if re.search(r"[A-Za-z]", txt):        # "MAX" (or a partial letter read) → maxed
        return node.max_level, txt
    # Illegible pill: do NOT assume MAX. The old "present-but-illegible → MAX"
    # fallback mis-stored partially-researched frontier tiles (e.g. a "1/3" whose
    # digits OCR'd to nothing) as done, which made the planner skip a real upgrade.
    # Leave it unread instead — _infer_maxed_predecessors backfills genuinely maxed
    # tiles from any researched successor (the game's tier rule), and the sweep
    # retries this one on a later frame.
    return None, txt


def _is_frontier_band(frame_levels: list[int]) -> bool:
    """True when a freshly-read tile band is entirely unresearched (the frontier).

    Research trees only get deeper — and more locked — as you scroll, so a band
    where every freshly-read tile shows level 0 (``0/Y``) marks the research
    frontier: nothing below it is researched either, so there is no point
    scrolling further down this tab. The ``>= 2`` floor keeps a single misread
    ``0/Y`` pill from cutting a tab short — two independent high-contrast ``0/Y``
    reads agreeing is a reliable "this whole band is unresearched" signal.
    """
    zeros = [lv for lv in frame_levels if lv == 0]
    return len(zeros) >= 2 and max(frame_levels, default=-1) == 0


async def _read_visible_tiles(oc: Any, frame: Any, w: int, h: int, graph: ResearchGraph,
                              rows: list[tuple[str, int]], seen: set[str],
                              diag: dict | None = None) -> tuple[int, list[int]]:
    """OCR every candidate tile cell in ``frame``; append newly-seen (name, level).

    Returns ``(new_count, frame_levels)`` — the number of *new* nodes added this
    frame (drives the scroll's dry early-stop) and the levels of those new tiles
    (drives the ``0/Y`` frontier early-stop, see :func:`_is_frontier_band`). Cells
    that don't resolve to a node, or whose pill is illegible, are skipped — the
    fuzzy matcher's threshold filters icon/tree-line garble.
    """
    new = 0
    frame_levels: list[int] = []
    for col_x in _COL_X_PCT:
        cx_px = int(col_x / 100 * w)
        for name_y in _NAME_ROW_Y_PCT:
            name = await _ocr_text(
                oc, frame,
                (col_x - _NAME_W_PCT / 2, name_y - _NAME_H_PCT / 2, _NAME_W_PCT, _NAME_H_PCT),
                w, h, preprocess="title_line", region_id="rc_tile_name",
            )
            if len(name) < 3:
                continue
            if diag is not None and len(diag.setdefault("names", [])) < 40:
                diag["names"].append(name)
            node_id = _match_tile_to_node(name, graph)
            if node_id is None:
                continue
            if diag is not None:
                diag["matched"] = diag.get("matched", 0) + 1
            if node_id in seen:
                continue
            level, raw_pill = await _read_pill_level(
                oc, frame, w, h, cx_px, int(name_y / 100 * h), graph.nodes[node_id])
            if level is None:
                if diag is not None:
                    diag.setdefault("pill_miss", []).append(f"{node_id}:{raw_pill!r}")
                continue
            rows.append((name, level))
            seen.add(node_id)
            frame_levels.append(level)
            new += 1
    return new, frame_levels


def _scroll_tree_down(actions: Any, iid: str, w: int, h: int) -> None:
    """Scroll the tree down by ~2 tiers per step.

    A very slow swipe barely advances the list (it tracks the finger then snaps
    back), so this uses a moderate drag for real coverage. Residual fling is fine
    *within* a tab (the next capture has a settle sleep) and is defused at tab
    boundaries by :func:`_switch_tab`'s fling-absorbing double tap.
    """
    actions.swipe(
        iid,
        Point(int(0.5 * w), int(0.70 * h)),
        Point(int(0.5 * w), int(0.28 * h)),
        duration_ms=480,
    )


async def _switch_tab(actions: Any, iid: str, tab: str, w: int, h: int) -> None:
    """Switch to a branch tab, defeating any residual scroll fling.

    The first tap absorbs leftover fling momentum (a flinging list swallows taps);
    the second actually selects the tab (and resets it to the top). Harmless when
    the tab is already active.
    """
    x, y = _TAB_XY[tab]
    _tap_pct(actions, iid, x, y, w, h)
    await asyncio.sleep(0.6)
    _tap_pct(actions, iid, x, y, w, h)
    await asyncio.sleep(1.3)


async def _capture(actions: Any, iid: str) -> Any:
    """Capture a frame for OCR via direct ``adb screencap``.

    The tech-tree tile names + level pills are small text; the device's scrcpy
    backend H.264-degrades small text enough to drop short names below the fuzzy
    match threshold (calibration was done on pristine adb frames). adb screencap
    is slower (~300 ms) but lossless, and works alongside scrcpy — worth it for an
    OCR-heavy 6-hourly reader. Falls back to the configured backend if adb capture
    isn't available.
    """
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr_adb, iid)
    except Exception:
        logger.debug("research_center: adb capture failed, trying default backend",
                     exc_info=True)
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, iid)
    except Exception:
        logger.exception("research_center: screen capture failed instance=%s", iid)
        return None


async def _ensure_on_tree(actions: Any, oc: Any, iid: str, frame: Any) -> tuple[Any, int]:
    """From the building-popup ring or the tree, end up on the tree.

    Returns ``(tree_frame_or_None, rc_level)``. When we start on the ring we read
    the Research Center level off the name-plate first (it's gone once the tree is
    open), then tap the microscope to open the tree.
    """
    h, w = frame.shape[:2]
    if await _title_is_tree(oc, frame, w, h):
        return frame, 0
    rc_level = await _read_rc_level(oc, frame, w, h)
    _tap_pct(actions, iid, *_OPEN_BTN_XY, w, h)
    await asyncio.sleep(2.5)
    frame = await _capture(actions, iid)
    if frame is None:
        return None, rc_level
    h, w = frame.shape[:2]
    if not await _title_is_tree(oc, frame, w, h):
        return None, rc_level
    return frame, rc_level


async def _sweep_research_tiles(ctx: Any, graph: ResearchGraph) -> tuple[list[tuple[str, int]], int]:
    """OCR the tech-tree tabs → ``([(tile_name, level), ...], rc_level)``.

    Assumes we start on the Research Center building-popup ring (the
    ``navigate_to_building open:true`` landing) — reads the RC level, opens the
    tree, then for each Growth/Economy/Battle tab scroll-sweeps the tiles,
    OCR'ing (name, level) per tile. Tolerant: any device/OCR failure returns what
    it has so far (possibly nothing) rather than raising.
    """
    try:
        from tasks import dsl_runtime

        actions = dsl_runtime.bot_actions()
        oc = dsl_runtime.ocr_client()
    except Exception:
        # No bot runtime (settings/device unavailable, e.g. unit tests) — no-op.
        logger.debug("research_center: bot runtime unavailable for sweep", exc_info=True)
        return [], 0
    iid = ctx.instance_id

    frame = await _capture(actions, iid)
    if frame is None:
        return [], 0
    frame, rc_level = await _ensure_on_tree(actions, oc, iid, frame)
    if frame is None:
        return [], rc_level

    rows: list[tuple[str, int]] = []
    seen: set[str] = set()
    diag: dict[str, dict] = {}
    h, w = frame.shape[:2]
    for tab in ("growth", "economy", "battle"):
        await _switch_tab(actions, iid, tab, w, h)
        tab_diag = diag.setdefault(tab, {})
        before = len(rows)
        dry = 0
        for _step in range(_MAX_SCROLL_STEPS):
            frame = await _capture(actions, iid)
            if frame is None:
                break
            h, w = frame.shape[:2]
            new, frame_levels = await _read_visible_tiles(
                oc, frame, w, h, graph, rows, seen, tab_diag)
            dry = dry + 1 if new == 0 else 0
            if dry >= _DRY_STOP:
                break
            # 0/Y frontier short-circuit: once a freshly-revealed band reads all
            # level 0, nothing below it is researched — stop sweeping this tab.
            if _is_frontier_band(frame_levels):
                tab_diag["frontier_stop"] = _step
                break
            _scroll_tree_down(actions, iid, w, h)
            await asyncio.sleep(1.2)
        tab_diag["read"] = len(rows) - before

    ctx.result["sweep_debug"] = diag
    # Best-effort: back out of the tree so we don't strand the device on it.
    try:
        actions.system_back(iid)
    except Exception:
        logger.debug("research_center: system_back after sweep failed", exc_info=True)
    return rows, rc_level


async def _exec_sync_research_levels(ctx: Any) -> None:
    """Read tech levels on-device → persist to the durable SQLite profile.

    Research is per-account, so the canonical home is the **SQLite** GamerState
    (tech levels under ``researches.levels.<id>``, the building level under
    ``buildings.levels.research_center``) — not a Redis player hash. A Redis
    *instance*-state mirror (``research.levels.*``) is kept only as the hot path
    the plan_next cron reads within a tick. Levels accumulate across runs:
    ``update_from_flat`` / ``hset`` update the keys read without clearing others.
    """
    from games.wos.core.research.planner import load_research_graph

    from tasks.dsl_exec.context import _resolve_player_id_for_device_level_exec

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return

    graph = load_research_graph()
    rows, rc_level = await _sweep_research_tiles(ctx, graph)
    if not rows and rc_level <= 0:
        ctx.result.update({"reason": "no_tiles_read"})
        return

    levels = _infer_maxed_predecessors(
        _drop_unconfirmed_max(_research_levels_from_ocr_rows(rows, graph), graph), graph)
    mapping: dict[str, str] = {f"research.levels.{nid}": str(lvl) for nid, lvl in levels.items()}
    if rc_level > 0:
        mapping["research.center.level"] = str(rc_level)
    if not mapping:
        ctx.result.update({"reason": "no_tiles_recognized"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    # Durable per-account home: the SQLite GamerState. Tech levels live under
    # ``researches.levels.<id>`` (a dict on the model); the Research Center building
    # level goes with the other building levels.
    if player_id:
        try:
            from config.state_store import get_state_store

            durable: dict[str, Any] = {
                f"researches.levels.{nid}": lvl for nid, lvl in levels.items()
            }
            if rc_level > 0:
                durable["buildings.levels.research_center"] = rc_level
            get_state_store().get_or_create(str(player_id)).update_from_flat(durable)
        except Exception:
            logger.exception(
                "sync_research_levels: durable SQLite write failed player=%s", player_id
            )
    # Redis instance-state mirror — hot path only (plan_next cron / overlay reads
    # it within a tick); NOT the durable home.
    try:
        await r.hset(f"wos:instance:{ctx.instance_id}:state", mapping=mapping)
    except Exception:
        logger.debug("sync_research_levels: redis mirror failed", exc_info=True)

    ctx.result.update({"action": "stored", "levels": levels, "rc_level": rc_level,
                       "player_id": player_id})
    logger.info(
        "sync_research_levels: %d techs read rc=%d player=%s instance=%s",
        len(levels), rc_level, player_id or "-", ctx.instance_id,
    )


async def _read_tile_name(oc: Any, frame: Any, w: int, h: int, col_x: float, name_y: float) -> str:
    """OCR a single tile-name cell (white-outlined title text)."""
    return await _ocr_text(
        oc, frame,
        (col_x - _NAME_W_PCT / 2, name_y - _NAME_H_PCT / 2, _NAME_W_PCT, _NAME_H_PCT),
        w, h, preprocess="title_line", region_id="rc_tile_name",
    )


async def _refine_name_y(oc: Any, frame: Any, w: int, h: int, col_x: float,
                         coarse_y: float, graph: ResearchGraph, target_id: str) -> float:
    """Pinpoint a matched tile name's true vertical centre (% of frame height).

    The coarse locate grid can match a name from a cell that's a few % off-centre.
    Re-OCR a few sub-rows around the coarse hit and average the ones that still
    resolve to ``target_id`` — that mean is a much tighter estimate of the name's
    real centre, so the icon tap (a small target) lands true.
    """
    matched_ys: list[float] = []
    for k in (-2, -1, 0, 1, 2):
        fy = coarse_y + k * _REFINE_STEP_PCT
        if not (0 < fy < 100):
            continue
        name = await _read_tile_name(oc, frame, w, h, col_x, fy)
        if len(name) >= 3 and _match_tile_to_node(name, graph) == target_id:
            matched_ys.append(fy)
    return sum(matched_ys) / len(matched_ys) if matched_ys else coarse_y


async def _locate_and_tap_tile(actions: Any, oc: Any, iid: str, graph: ResearchGraph,
                               target_id: str, tap_log: dict | None = None,
                               diag: list | None = None) -> bool:
    """Scroll the current tab top→down looking for ``target_id``'s tile; tap it.

    Returns ``True`` once the tile is found and tapped open (its detail popup),
    ``False`` if it never appears within the scroll budget. Scrolls in small
    (~1-tier) steps and over more frames than the sweep — it must catch its one
    target, so each tile should linger across several frames (more OCR attempts).
    """
    from rapidfuzz import fuzz

    tgt_spec = graph.spec(target_id)
    tgt_name = (tgt_spec.name if tgt_spec else target_id).lower()
    dry = 0
    for _step in range(_LOCATE_SCROLL_STEPS):
        frame = await _capture(actions, iid)
        if frame is None:
            return False
        h, w = frame.shape[:2]
        hit_any = False
        for col_x in _COL_X_PCT:
            for name_y in _LOCATE_ROW_Y_PCT:
                name = await _read_tile_name(oc, frame, w, h, col_x, name_y)
                if len(name) < 3:
                    continue
                hit_any = True
                if diag is not None:
                    score = round(float(fuzz.token_set_ratio(name.lower(), tgt_name)), 1)
                    if score >= 55:  # only the near-misses + hits, to keep it readable
                        diag.append({"step": _step, "col": col_x, "y": name_y,
                                     "name": name, "score": score})
                if _match_tile_to_node(name, graph) == target_id:
                    # Pinpoint the name's true vertical centre with a fine sub-scan
                    # (the coarse row can sit a few % off the actual name, and the
                    # tile ICON — the only reliable open target — is small), then
                    # tap the icon at the calibrated name→icon distance.
                    precise_y = await _refine_name_y(oc, frame, w, h, col_x, name_y, graph, target_id)
                    tx = int(col_x / 100 * w)
                    ty_pct = max(precise_y - _TILE_ICON_DY_PCT, _LOCATE_TAP_MIN_Y_PCT)
                    ty = int(ty_pct / 100 * h)
                    if tap_log is not None:
                        tap_log.update({"name": name, "col_x": col_x, "name_y": name_y,
                                        "precise_y": round(precise_y, 1), "tap_x": tx, "tap_y": ty})
                    actions.tap(iid, Point(tx, ty))
                    await asyncio.sleep(1.6)
                    return True
        dry = dry + 1 if not hit_any else 0
        if dry >= _DRY_STOP:
            return False
        _scroll_tree_down(actions, iid, w, h)
        await asyncio.sleep(1.2)
    return False


async def _exec_start_planned_research(ctx: Any) -> None:
    """Open the tree → switch to the planned branch tab → find + start the tech.

    Reads ``planner.next_research`` / ``.next_research_name`` / ``.next_research_branch``
    (set by ``plan_next_research``), navigates the tech tree to that tile and taps
    the in-tree **Research** button. Never taps the gem-spending *Finish* button.
    """
    from games.wos.core.research.planner import load_research_graph

    from tasks import dsl_runtime

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await r.hgetall(inst_key)
    except Exception:
        state = {}
    target_id = _state_get_str(state, "planner.next_research")
    target_name = _state_get_str(state, "planner.next_research_name")
    branch = _state_get_str(state, "planner.next_research_branch")
    if not target_id:
        ctx.result.update({"reason": "no_plan"})
        return

    graph = load_research_graph()
    actions = dsl_runtime.bot_actions()
    oc = dsl_runtime.ocr_client()
    iid = ctx.instance_id

    frame = await _capture(actions, iid)
    if frame is None:
        ctx.result.update({"reason": "capture_failed"})
        return
    frame, _rc = await _ensure_on_tree(actions, oc, iid, frame)
    if frame is None:
        ctx.result.update({"reason": "tree_not_opened"})
        return

    h, w = frame.shape[:2]
    tab = branch_to_tab(branch)
    # Force a scroll reset: tapping an already-active tab doesn't scroll it to the
    # top, so the target tile could be stranded scrolled off (its icon under the
    # tab bar, untappable). Switch via a sibling tab first — a real tab change
    # always resets the new tab to the top, where tiles sit at tappable positions.
    sibling = "economy" if tab != "economy" else "growth"
    await _switch_tab(actions, iid, sibling, w, h)
    await _switch_tab(actions, iid, tab, w, h)

    tap_log: dict = {}
    locate_diag: list | None = [] if ctx.args.get("debug") else None
    found = await _locate_and_tap_tile(actions, oc, iid, graph, target_id, tap_log, locate_diag)
    if locate_diag is not None:
        ctx.result["locate_diag"] = locate_diag
    if not found:
        ctx.result.update({"reason": "tile_not_found", "next": target_id, "tab": tab})
        return
    ctx.result["tap_log"] = tap_log

    # On the tech-detail popup. Verify the blue **Research** button is present BEFORE
    # tapping — a maxed tile shows "Tech level maxed!" and a locked one shows "Go"
    # requirement buttons, neither of which we want to blind-tap. The popup title
    # OCRs unreliably (decorated header), so the button label is the signal.
    frame = await _capture(actions, iid)
    btn = ""
    if frame is not None:
        dh, dw = frame.shape[:2]
        btn = await _ocr_text(oc, frame, _RESEARCH_BTN_BBOX, dw, dh,
                              preprocess="word_line", region_id="rc_research_btn")
    ctx.result["btn_ocr"] = btn

    if "research" not in btn.lower():
        # Not researchable here (maxed / locked / popup didn't open). No-op safely —
        # never tap. Maxed-tile coverage is filled by the sweep + tier inference over
        # crons, so the planner stops re-picking it without a fragile self-heal here.
        try:
            actions.system_back(iid)
        except Exception:
            logger.debug("start_planned_research: system_back failed", exc_info=True)
        ctx.result.update({"reason": "not_researchable", "next": target_id,
                           "name": target_name, "tab": tab})
        logger.info("start_planned_research: %s not researchable here (btn=%r)", target_id, btn)
        return

    # Start it: tap the blue Research button (NOT the orange gem-spending Finish).
    _tap_pct(actions, iid, *_RESEARCH_BTN_XY, w, h)
    await asyncio.sleep(1.2)
    # Some techs raise a confirm dialog; its primary action sits where Research did.
    _tap_pct(actions, iid, *_RESEARCH_BTN_XY, w, h)
    await asyncio.sleep(0.8)

    ctx.result.update({"action": "started", "next": target_id, "name": target_name, "tab": tab})
    logger.info("start_planned_research: started=%s tab=%s instance=%s", target_id, tab, iid)


async def _exec_plan_next_research(ctx: Any) -> None:
    """Connect the research planner: read levels + RC level → ``plan_next`` → store.

    Reads ``research.levels.*`` (populated by ``sync_research_levels``) and
    ``research.center.level`` from the instance-state hash, runs the value-greedy
    planner, and writes its pick to ``planner.next_research`` /
    ``.next_research_name`` / ``.next_research_branch`` / ``.next_research_to_level``
    / ``.research_reason``. Pure compute — the recommendation is only as complete
    as the level coverage so far (no reader yet → ``rc_gated`` until tech levels
    are read on-device).
    """
    from games.wos.core.research.planner import load_research_graph, plan_next

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await r.hgetall(inst_key)
    except Exception:
        state = {}
    levels = _read_research_levels(state)
    rc_level = _state_get_int(state, "research.center.level", 0)

    # Self-heal blindness: the instance hash is only a hot mirror, so when it has
    # no research history yet, re-check the durable SQLite profile (the canonical
    # per-account home that sync_research_levels writes) before deciding it's empty
    # — then push the on-device reader to sweep all 3 branches. skip_if_duplicate
    # dedups with its 6h cron.
    if not levels:
        import time

        from config.state_store import get_state_store
        from tasks.dsl_exec.context import _resolve_player_id_for_device_level_exec
        from tasks.dsl_scenario_helpers import _enqueue_scenario

        player_id = await _resolve_player_id_for_device_level_exec(ctx)
        if player_id:
            try:
                snap = get_state_store().get_or_create(str(player_id)).snapshot()
                levels = {
                    str(k): int(v) for k, v in (snap.researches.levels or {}).items()
                }
                if not rc_level:
                    rc_level = int((snap.buildings.levels or {}).get("research_center", 0) or 0)
            except Exception:
                logger.debug("plan_next_research: durable SQLite read failed", exc_info=True)
        if not levels:
            pushed = await _enqueue_scenario(
                redis_async=r,
                instance_id=ctx.instance_id,
                player_id=player_id or "",
                scenario="sync_research_levels",
                priority=60_000,
                run_at=time.time(),
                skip_if_duplicate=True,
            )
            try:
                await r.hset(
                    inst_key,
                    mapping={"planner.research_reason": "needs_reader", "planner.next_research": ""},
                )
            except Exception:
                logger.debug("plan_next_research: needs_reader state write failed", exc_info=True)
            ctx.result.update(
                {
                    "action": "needs_reader",
                    "pushed": "sync_research_levels" if pushed else "",
                    "reason": "research_history_empty",
                }
            )
            logger.info(
                "plan_next_research: research history empty → pushed sync_research_levels "
                "(player=%s instance=%s)",
                player_id or "-",
                ctx.instance_id,
            )
            return

    plan = plan_next(load_research_graph(), levels, rc_level)
    step = plan.step
    mapping: dict[str, str] = {"planner.research_reason": plan.reason}
    if step is not None:
        mapping["planner.next_research"] = step.node_id
        mapping["planner.next_research_name"] = step.name
        mapping["planner.next_research_branch"] = step.branch
        mapping["planner.next_research_to_level"] = str(step.to_level)
    else:
        mapping["planner.next_research"] = ""
    try:
        await r.hset(inst_key, mapping=mapping)
    except Exception:
        logger.debug("plan_next_research: state write failed", exc_info=True)

    nxt = step.node_id if step is not None else None
    ctx.result.update(
        {"action": "planned", "next": nxt, "reason": plan.reason, "rc_level": rc_level, "levels": levels}
    )
    logger.info(
        "plan_next_research: next=%s to=%s reason=%s rc=%d techs=%d instance=%s",
        nxt,
        getattr(step, "to_level", None),
        plan.reason,
        rc_level,
        len(levels),
        ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {
    "plan_next_research": _exec_plan_next_research,
    "sync_research_levels": _exec_sync_research_levels,
    "start_planned_research": _exec_start_planned_research,
}
