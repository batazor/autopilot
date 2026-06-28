"""``exec: sync_troop_pool`` — read live troop counts off the Troops Preview screen.

Reached from chief_profile → Troops. The screen lays the roster out as a 2-column
grid of cards (``<Tier> <Type>`` + a count), sorted by count descending. The blue
count digits are low-contrast and don't OCR at native scale, so each card cell is
cropped and **upscaled 4×** before OCR (calibrated on-device: native reads garbage,
4× reads clean).

We don't need every tier — the roster is sorted descending, so the highest-count
card of each type is its top tier, which holds ~99% of that type's troops (lower
tiers are hundreds vs the top tier's tens of thousands). So we take the **max count
per type**: robust to lower cells mis-reading, and accurate to <1% of the on-screen
total. Writes ``troops.{infantry,lancer,marksman}.available`` to the Redis player +
instance hashes — the un-blind input the shared-resource allocator's typed pool reads
(its cost lines are all ``type: any``, i.e. the sum across types).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

import cv2

from config.state_store import get_state_store
from layout.types import Region
from ocr.fuzzy import match as fuzzy_match
from tasks import dsl_runtime

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

TROOP_TYPES: tuple[str, ...] = ("infantry", "lancer", "marksman")

# Troops Preview grid geometry (% of 720×1280), calibrated on-device. Two columns
# of cards; each card's name+count fit in a CELL_W×CELL_H box anchored at the name
# top. The whole cell is OCR'd at 4× — fast_digits-only count windows drifted out of
# alignment row-to-row (non-uniform pitch), but a tall whole-cell read is tolerant.
_COLS: tuple[float, float] = (19.5, 63.5)
# "All" tab: the grid sits right under the Total/March/Injured bar.
_NAME_TOPS: tuple[float, ...] = (27.5, 37.3, 47.3, 57.0, 66.8, 76.3)
# "City" / "Wilderness" tabs carry an extra "Troops: N" sub-header that pushes the
# grid down ~15.5%; fewer rows fit, and the top tiers dominate, so 5 is enough.
_NAME_TOPS_SUB: tuple[float, ...] = (43.0, 52.8, 62.8, 72.5, 82.3)
_CELL_W, _CELL_H = 26.0, 7.6
_UPSCALE = 4

# Title landmark — confirms we're on Troops Preview before writing, so a genuinely
# empty Wilderness tab records 0 instead of being mistaken for a navigation miss.
_TITLE = (4.0, 0.8, 40.0, 4.5)

# ``tab:`` step arg → (grid geometry, state-key suffix). City = troops at home =
# the allocator's "available"; All = total; Wilderness = deployed in the field.
_TAB_CONFIG: dict[str, tuple[tuple[float, ...], str]] = {
    "all": (_NAME_TOPS, "total"),
    "city": (_NAME_TOPS_SUB, "available"),
    "wilderness": (_NAME_TOPS_SUB, "wilderness"),
}


def _parse_cell(text: str) -> tuple[str | None, int]:
    """One card's OCR text → ``(troop_type | None, count)``.

    Type comes from the first line (the unit name, e.g. ``"Supreme Lancer"``) via a
    fuzzy match — OCR garbles letters (``Infantry`` → ``Inrantry``) and the count
    digits would poison a whole-text match. Count is the largest number anywhere in
    the cell (the card has exactly one).
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    name = lines[0] if lines else ""
    hit = fuzzy_match(re.sub(r"[^a-z ]", "", name.lower()), list(TROOP_TYPES), threshold=0.45)
    typ = hit.candidate if hit else None
    nums = [int(re.sub(r"[^0-9]", "", n)) for n in re.findall(r"[0-9][0-9,]{1,}", text or "")]
    return typ, (max(nums) if nums else 0)


def parse_troop_cells(texts: Iterable[str]) -> dict[str, int]:
    """Max count per type across all card cells (top tier ≈ that type's total)."""
    best = dict.fromkeys(TROOP_TYPES, 0)
    for text in texts:
        typ, num = _parse_cell(text)
        if typ and num > best[typ]:
            best[typ] = num
    return best


async def _exec_sync_troop_pool(ctx: DslExecContext) -> None:
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning("dsl exec sync_troop_pool: empty player_id — skipping")
        return

    # ``tab:`` sibling key picks which tab's grid we're on → geometry + key suffix.
    # Default "all" so a bare ``exec: sync_troop_pool`` still works.
    tab = (str(ctx.args.get("tab") or "all")).strip().lower()
    name_tops, suffix = _TAB_CONFIG.get(tab, _TAB_CONFIG["all"])

    actions = dsl_runtime.bot_actions()
    try:
        frame = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception("dsl exec sync_troop_pool: capture failed instance=%s", ctx.instance_id)
        return
    if frame is None:
        return
    h, w = frame.shape[:2]
    oc = dsl_runtime.ocr_client()

    # Confirm we're actually on Troops Preview (the title is identical across tabs).
    tx, ty, tw, th = _TITLE
    title = await oc.ocr_region(
        frame,
        Region(int(tx / 100 * w), int(ty / 100 * h), int(tw / 100 * w), int(th / 100 * h)),
        region_id="troops_preview.title",
    )
    if "troops" not in (title.text or "").lower():
        logger.info(
            "dsl exec sync_troop_pool: not on Troops Preview (title=%r) tab=%s player=%s",
            (title.text or "").strip(), tab, player_id,
        )
        return

    async def read_cell(x_pct: float, top_pct: float) -> str:
        x0, y0 = int(x_pct / 100 * w), int(top_pct / 100 * h)
        x1, y1 = int((x_pct + _CELL_W) / 100 * w), int((top_pct + _CELL_H) / 100 * h)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return ""
        up = cv2.resize(crop, None, fx=_UPSCALE, fy=_UPSCALE, interpolation=cv2.INTER_CUBIC)
        res = await oc.ocr_region(up, Region(0, 0, up.shape[1], up.shape[0]), region_id="troop_cell")
        return res.text or ""

    try:
        texts = await asyncio.gather(*(read_cell(x, top) for top in name_tops for x in _COLS))
    except Exception:
        logger.exception("dsl exec sync_troop_pool: OCR failed instance=%s", ctx.instance_id)
        return

    counts = parse_troop_cells(texts)
    # On Troops Preview, write even all-zero (a genuinely empty Wilderness tab).
    # Durable per-account home first: the SQLite GamerState (``troops.<type>.<suffix>``
    # is reader-authoritative). The Redis hashes below are only the allocator's hot
    # mirror — cold after a flush/restart, which blinds the typed pool until the next
    # on-device sweep; ``scheduler.runner._load_player_states`` self-heals from here.
    try:
        get_state_store().get_or_create(player_id).update_from_flat(
            {f"troops.{t}.{suffix}": counts[t] for t in TROOP_TYPES}
        )
    except Exception:
        logger.exception(
            "dsl exec sync_troop_pool: durable SQLite write failed player=%s", player_id
        )
    mapping = {f"troops.{t}.{suffix}": str(counts[t]) for t in TROOP_TYPES}
    mapping["troops.synced_at"] = str(time.time())
    if ctx.redis_client is not None:
        try:
            await ctx.redis_client.hset(f"wos:player:{player_id}:state", mapping=mapping)
            await ctx.redis_client.hset(f"wos:instance:{ctx.instance_id}:state", mapping=mapping)
        except Exception:
            logger.exception("dsl exec sync_troop_pool: redis write failed player=%s", player_id)
            return

    logger.info(
        "dsl exec sync_troop_pool: tab=%s suffix=%s player=%s counts=%s total=%d",
        tab, suffix, player_id, counts, sum(counts.values()),
    )
