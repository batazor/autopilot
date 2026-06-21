"""``exec: scan_heroes_grid`` — hero grid scan into per-player hero state."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from config.heroes import get_hero_registry
from config.state_store import get_state_store
from layout.types import Region
from navigation.hero_grid_search import scan_grid_frame
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import (
        DslExecContext,
    )

logger = logging.getLogger(__name__)

_HERO_SHARD_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_HERO_LEVEL_RE = re.compile(r"[Ll]\s*[Vv]\s*\.?\s*(\d+)")
_HERO_GRID_POSITIONS_KEY_FMT = "wos:instance:{instance_id}:hero_grid_positions"
_HERO_GRID_POSITIONS_TTL_SECONDS = 10 * 60
"""Hero positions in the visible roster — short TTL: the player can re-sort
the grid (Power → Stars → …) at any moment, and we'd rather force a fresh
scan than tap a stale cell."""


async def _exec_scan_heroes_grid(ctx: DslExecContext) -> None:
    """Snapshot every visible hero on the ``heroes`` screen into the SQLite state store.

    Captures the current frame, runs grayscale NCC against every wiki icon
    under ``db/assets/wiki/heroes/<id>/``, and for each match writes to
    ``heroes.entries.<hero_id>``:

    * ``name``: canonical display name from ``db/heroes/index.yaml``.
    * ``available``: True when the matched cell is rendered in full color
      (unlocked); False when the card is dimmed and shows a shard counter.
    * ``shards_current`` / ``shards_required``: parsed from the badge text
      (``"0/10"``, ``"5/40"``, …) via OCR on locked cells only.
    * ``last_seen_at`` / ``last_match_score``: when and how confidently the
      icon was last seen on the grid.

    Existing fields (e.g. ``level`` written by ``sync_hero_unit``) are
    preserved — we read the current entry, merge, and write the whole dict
    back, so the two handlers compose without stomping each other.
    """
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning("dsl exec scan_heroes_grid: empty player_id — skipping")
        return

    actions = dsl_runtime.bot_actions()
    try:
        frame = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec scan_heroes_grid: capture failed instance=%s", ctx.instance_id
        )
        return

    try:
        hits = await asyncio.to_thread(scan_grid_frame, frame)
    except Exception:
        logger.exception(
            "dsl exec scan_heroes_grid: scan failed instance=%s", ctx.instance_id
        )
        return

    if not hits:
        logger.info(
            "dsl exec scan_heroes_grid: no heroes matched on this frame instance=%s",
            ctx.instance_id,
        )
        return

    # Batch-OCR the shard-counter badges on every cell plus the "Lv. X"
    # badges on unlocked cells in one pass (Lv slot is higher than the
    # shard slot — see _LV_DY / _BADGE_DY in hero_grid_search). The badge
    # slot is OCR'd for unlocked cells too because the "Recruit / N/N"
    # ready-to-recruit state renders a fully-colored card (so ``available``
    # reads True) but keeps an "N/N" counter where "Lv. X" would normally
    # sit — the level row reads "Recruit" instead, so falling back to the
    # badge OCR is the only way to capture shard fullness in that state.
    unlocked_items = [(hid, match) for hid, match in hits.items() if match.available]
    badge_text: dict[str, str] = {}
    level_text: dict[str, str] = {}
    regions: list[Region] = []
    ids: list[str] = []
    for hid, match in hits.items():
        regions.append(Region(*match.badge_bbox))
        ids.append(f"hero_shards_{hid}")
    for hid, match in unlocked_items:
        regions.append(Region(*match.level_bbox))
        ids.append(f"hero_level_{hid}")
    ocr_ok = True
    if regions:
        try:
            results = await dsl_runtime.ocr_client().ocr_regions(frame, regions, region_ids=ids)
            n_badges = len(hits)
            for (hid, _), result in zip(hits.items(), results[:n_badges], strict=False):
                badge_text[hid] = (result.text or "").strip()
            for (hid, _), result in zip(unlocked_items, results[n_badges:], strict=False):
                level_text[hid] = (result.text or "").strip()
        except Exception:
            logger.exception(
                "dsl exec scan_heroes_grid: badge OCR failed instance=%s",
                ctx.instance_id,
            )
            ocr_ok = False

    registry = get_hero_registry()
    try:
        store = get_state_store().get_or_create(player_id)
    except Exception:
        logger.exception(
            "dsl exec scan_heroes_grid: state store init failed player=%s", player_id
        )
        return

    snap = store.snapshot()
    existing_entries = dict(snap.heroes.entries) if snap.heroes.entries else {}

    now = time.time()
    flat: dict[str, Any] = {}
    locked_count = 0
    available_count = 0
    recruit_ready_heroes: list[str] = []
    for hid, match in hits.items():
        prev = existing_entries.get(hid)
        entry: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}  # ty: ignore[no-matching-overload]

        hero_def = registry.by_id(hid)
        if hero_def is not None:
            entry["name"] = hero_def.name
        entry["available"] = bool(match.available)
        entry["red_dot"] = bool(match.has_red_dot)
        entry["isUpgradeAvailable"] = bool(match.upgrade_available)
        entry["last_seen_at"] = now
        entry["last_match_score"] = round(match.score, 3)

        if match.available:
            available_count += 1
            text = level_text.get(hid, "")
            lvl_match = _HERO_LEVEL_RE.search(text)
            if lvl_match is not None:
                with contextlib.suppress(ValueError):
                    entry["level"] = int(lvl_match.group(1))
                # Confidently playable hero: stale shard counts from a
                # past locked snapshot are misleading; drop them so
                # callers don't see "needs 9/10" on a hero that's
                # already recruited.
                entry.pop("shards_current", None)
                entry.pop("shards_required", None)
            else:
                # Level slot didn't parse — could be a transient OCR
                # miss, or the "Recruit / N/N" ready-to-recruit state
                # where the level row shows "Recruit" instead of "Lv".
                # Fall through to the badge OCR and preserve shards if
                # an "N/N" reading is present.
                shard_text_v = badge_text.get(hid, "")
                shard_match = _HERO_SHARD_RE.search(shard_text_v)
                if shard_match is not None:
                    try:
                        entry["shards_current"] = int(shard_match.group(1))
                        entry["shards_required"] = int(shard_match.group(2))
                    except ValueError:
                        pass
                else:
                    logger.debug(
                        "dsl exec scan_heroes_grid: hero=%s level OCR unparsed text=%r",
                        hid, text,
                    )
        else:
            locked_count += 1
            text = badge_text.get(hid, "")
            mre = _HERO_SHARD_RE.search(text)
            if mre is not None:
                try:
                    entry["shards_current"] = int(mre.group(1))
                    entry["shards_required"] = int(mre.group(2))
                except ValueError:
                    pass
            else:
                logger.debug(
                    "dsl exec scan_heroes_grid: hero=%s shard OCR unparsed text=%r",
                    hid, text,
                )

        # Shards full to the cap = "Recruit / N/N" state — the hero card
        # is one tap on the Recruit button away from being claimed. Only
        # trust this when the current OCR pass actually succeeded: a failed
        # bulk OCR leaves badge_text/level_text empty, so the entry's
        # shards_* values are carried over from the previous snapshot —
        # firing recruit_ready off stale counts pushes spurious recruit
        # scenarios that the live UI does not back.
        if ocr_ok:
            cur = entry.get("shards_current")
            req = entry.get("shards_required")
            if isinstance(cur, int) and isinstance(req, int) and req > 0 and cur >= req:
                recruit_ready_heroes.append(hid)

        flat[f"heroes.entries.{hid}"] = entry

    try:
        await asyncio.to_thread(store.update_from_flat, flat)
    except Exception:
        logger.exception(
            "dsl exec scan_heroes_grid: persist failed player=%s", player_id
        )
        return

    logger.info(
        "dsl exec scan_heroes_grid: instance=%s player=%s persisted=%d "
        "available=%d locked=%d",
        ctx.instance_id, player_id, len(hits), available_count, locked_count,
    )

    # Publish current hero → cell positions for ``hero_grid`` edge resolver.
    # Resolver reads this hash to translate ``page.heroes.<id>`` routes into
    # the matching ``heroes.grid.r{ri}c{ci}`` tap region. We delete and
    # rewrite so positions vacated by a re-sort drop out cleanly.
    if ctx.redis_client is not None:
        pos_key = _HERO_GRID_POSITIONS_KEY_FMT.format(instance_id=ctx.instance_id)
        mapping = {
            hid: f"r{match.cell[0]}c{match.cell[1]}"
            for hid, match in hits.items()
        }
        try:
            await ctx.redis_client.delete(pos_key)
            if mapping:
                await ctx.redis_client.hset(pos_key, mapping=mapping)
                await ctx.redis_client.expire(
                    pos_key, _HERO_GRID_POSITIONS_TTL_SECONDS
                )
        except Exception:
            logger.exception(
                "dsl exec scan_heroes_grid: position hash write failed instance=%s",
                ctx.instance_id,
            )

    # Analyzer: for every visible hero whose card lit up a red-dot badge,
    # enqueue its per-hero scenario (``games/wos/heroes/heroes/scenarios/{hero}.yaml``
    # template → key = ``<hero_id>`` → navigates to ``page.heroes.<hero_id>``
    # via the ``hero_grid`` edge resolver, which routes through the
    # ``heroes.grid.r{ri}c{ci}`` cell we just wrote above). ``skip_if_duplicate``
    # collapses repeat scans firing on the same dot until the first push runs.
    from dsl.dsl_schema import DEFAULT_SCENARIO_PRIORITY
    from tasks.dsl_scenario_helpers import _enqueue_scenario

    red_dot_heroes = [hid for hid, match in hits.items() if match.has_red_dot]
    pushed: list[str] = []
    for hid in red_dot_heroes:
        try:
            ok = await _enqueue_scenario(
                redis_async=ctx.redis_client,
                instance_id=ctx.instance_id,
                player_id=player_id,
                scenario=hid,
                priority=DEFAULT_SCENARIO_PRIORITY,
                run_at=time.time(),
                skip_if_duplicate=True,
            )
        except Exception:
            logger.exception(
                "dsl exec scan_heroes_grid: enqueue hero scenario failed "
                "instance=%s hero=%s",
                ctx.instance_id, hid,
            )
            continue
        if ok:
            pushed.append(hid)
    if red_dot_heroes:
        logger.info(
            "dsl exec scan_heroes_grid: red-dot analyzer instance=%s player=%s "
            "candidates=%d pushed=%d",
            ctx.instance_id, player_id, len(red_dot_heroes), len(pushed),
        )

    # Recruit-ready analyzer: hero cards in the "Recruit / N/N" state (shards
    # collected to the cap, hero not yet claimed) get the ``<hero_id>.recruit``
    # scenario pushed. Template ``games/wos/heroes/heroes/scenarios/{hero}.recruit.yaml`` is
    # rendered by ``scenarios.template_resolver`` to that hero's recruitment
    # flow. ``skip_if_duplicate`` collapses repeat scans on the same N/N card.
    pushed_recruits: list[str] = []
    for hid in recruit_ready_heroes:
        try:
            ok = await _enqueue_scenario(
                redis_async=ctx.redis_client,
                instance_id=ctx.instance_id,
                player_id=player_id,
                scenario=f"{hid}.recruit",
                priority=DEFAULT_SCENARIO_PRIORITY,
                run_at=time.time(),
                skip_if_duplicate=True,
            )
        except Exception:
            logger.exception(
                "dsl exec scan_heroes_grid: enqueue recruit scenario failed "
                "instance=%s hero=%s",
                ctx.instance_id, hid,
            )
            continue
        if ok:
            pushed_recruits.append(hid)
    if recruit_ready_heroes:
        logger.info(
            "dsl exec scan_heroes_grid: recruit-ready analyzer instance=%s "
            "player=%s candidates=%d pushed=%d",
            ctx.instance_id, player_id,
            len(recruit_ready_heroes), len(pushed_recruits),
        )
