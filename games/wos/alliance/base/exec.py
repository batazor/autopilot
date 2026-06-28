"""DSL exec handlers for the Alliance base screen."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from games.wos.alliance.base.members_parser import (
    AllianceMembersParser,
    MemberEntry,
    merge_members_by_name,
)

from config.state_sqlite import (
    record_alliance_members_history,
    record_alliance_members_snapshot,
    record_alliance_stats,
)
from config.state_store import get_state_store
from layout.types import Point
from tasks import dsl_runtime
from tasks.dsl_exec.context import DslExecContext, _decode_redis_raw

logger = logging.getLogger(__name__)


# The Alliance Members screen carries a "Search by Chief ID or name" search box
# right where the overview's alliance-name sits. When screen detection confuses
# the two (both titles start with "Alliance"), the alliance.name OCR lands on
# that placeholder. Its "ID or name" wording can never be a real alliance name,
# so reject it rather than store/resolve garbage (observed reads: "2F ID or
# name", "Chief ID or name").
_SEARCH_PLACEHOLDER_RE = re.compile(r"id\s*or\s*name", re.IGNORECASE)


def _looks_like_search_placeholder(name: object) -> bool:
    return bool(_SEARCH_PLACEHOLDER_RE.search(str(name or "")))


def _parse_int(raw: object) -> int:
    digits = re.sub(r"\D+", "", str(raw or ""))
    return int(digits) if digits else 0


def _parse_members(raw: object) -> tuple[int, int]:
    text = str(raw or "")
    nums = [int(n) for n in re.findall(r"\d+", text)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], 0
    return 0, 0


async def _read_hash(redis_client: Any, key: str, field: str) -> str:
    raw = await redis_client.hget(key, field)
    return _decode_redis_raw(raw)


async def _exec_sync_alliance_stats(ctx: DslExecContext) -> None:
    """Persist the latest Alliance overview OCR snapshot into SQLite.

    The surrounding scenario stores OCR results on the instance hash because it
    is device-level and must work even before an active player is known.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_alliance_stats: no redis client")
        ctx.result.update({"reason": "no_redis_client"})
        return

    key = f"wos:instance:{ctx.instance_id}:state"
    name = await _read_hash(ctx.redis_client, key, "alliance.name")
    if not name:
        logger.warning("dsl exec sync_alliance_stats: missing alliance.name OCR")
        ctx.result.update({"reason": "missing_alliance_name"})
        return
    if _looks_like_search_placeholder(name):
        # We OCR'd the member-search box, not the overview — screen detection
        # put us on alliance.members. Don't persist a bogus alliance.
        logger.warning(
            "dsl exec sync_alliance_stats: alliance.name=%r is the member-search "
            "placeholder (wrong screen) — skipping persist",
            name,
        )
        ctx.result.update({"reason": "search_placeholder", "value": name})
        return

    power = _parse_int(await _read_hash(ctx.redis_client, key, "alliance.power"))
    rank = _parse_int(await _read_hash(ctx.redis_client, key, "alliance.rank"))
    level = _parse_int(await _read_hash(ctx.redis_client, key, "alliance.level.badge"))
    members_count, members_max = _parse_members(
        await _read_hash(ctx.redis_client, key, "alliance.members.count")
    )

    row = record_alliance_stats(
        alliance_name=name,
        power=power,
        rank=rank,
        level=level,
        members_count=members_count,
        members_max=members_max,
    )

    # Mirror the alliance name onto the active player's durable profile state.
    # scan_alliance_members navigates straight to alliance.members (the daily
    # cron never guarantees a fresh overview visit first) and the instance hash
    # is volatile, so _resolve_alliance_name prefers this player-scoped value as
    # its primary source. It carries the exact overview name, so roster
    # snapshots key identically to this stats row. Only the name is mirrored —
    # power/rank/level OCR is unreliable today and would clobber good values.
    player_id = (ctx.player_id or "").strip()
    if player_id:
        try:
            store = get_state_store().get_or_create(player_id)
            await asyncio.to_thread(store.update_from_flat, {"alliance.name": name})
        except Exception:
            logger.exception(
                "dsl exec sync_alliance_stats: player alliance.name persist failed player=%s",
                player_id,
            )

    ctx.result.update({"action": "stored", **row})
    logger.info(
        "dsl exec sync_alliance_stats: alliance=%s power=%d rank=%d level=%d members=%d/%d",
        name,
        power,
        rank,
        level,
        members_count,
        members_max,
    )


def _snapshot_to_result(snapshot: Any) -> dict[str, Any]:
    return {
        "online_count": snapshot.online_count,
        "total_count": snapshot.total_count,
        "rank_counts": {
            str(rank): {
                "count": group.count,
                "max": group.max_count,
                "expanded": group.expanded,
                "online_marker": group.online_marker,
            }
            for rank, group in sorted(snapshot.ranks.items(), reverse=True)
        },
        "members": [
            {
                "rank": member.rank,
                "name": member.name,
                "power": member.power,
                "level": member.level,
                "status": member.status,
                "online": member.online,
                "last_online_text": member.last_online_text,
                "last_online_seconds": member.last_online_seconds,
            }
            for member in snapshot.members
        ],
    }


def _member_to_dict(member: MemberEntry) -> dict[str, Any]:
    return {
        "rank": member.rank,
        "name": member.name,
        "power": member.power,
        "level": member.level,
        "status": member.status,
        "online": member.online,
        "last_online_text": member.last_online_text,
        "last_online_seconds": member.last_online_seconds,
    }


def _rank_expected_total(group: Any) -> int:
    return int(group.max_count or group.count or 0)


def _scan_int_arg(ctx: DslExecContext, key: str, default: int) -> int:
    raw = ctx.args.get(key)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def _capture_members_snapshot(
    parser: AllianceMembersParser,
    actions: Any,
    ocr_client: Any,
    *,
    instance_id: str,
    rank_hint: int | None = None,
) -> Any:
    image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    if image is None or not hasattr(image, "shape"):
        return None
    return await parser.parse_with_ocr(
        image,
        ocr_client,
        expanded_rank_hint=rank_hint,
    )


async def _tap_members_point(actions: Any, instance_id: str, point: Point) -> bool:
    return bool(await asyncio.to_thread(actions.tap, instance_id, point))


async def _swipe_members_down(actions: Any, instance_id: str) -> bool:
    return bool(
        await asyncio.to_thread(
            actions.swipe,
            instance_id,
            Point(360, 930),
            Point(360, 560),
            450,
        )
    )


async def _resolve_alliance_name(ctx: DslExecContext, player_store: Any) -> str:
    """Resolve the alliance name for the roster scan.

    Resolution order (first non-empty wins):

    1. ``alliance_name`` scenario arg (explicit operator override).
    2. The active player's durable profile state — ``sync_alliance_stats``
       mirrors the overview OCR here, so it survives restarts and is fresh even
       when the scan navigates straight to alliance.members without a fresh
       overview visit (the daily cron path).
    3. The volatile instance hash (the most recent overview OCR), as a last
       resort when the player has never had a successful overview sync.
    """
    raw_arg = ctx.args.get("alliance_name")
    if raw_arg:
        return str(raw_arg).strip()
    try:
        alliance_name = str(player_store.snapshot().alliance.name or "").strip()
    except Exception:
        alliance_name = ""
    if _looks_like_search_placeholder(alliance_name):
        alliance_name = ""
    if alliance_name or ctx.redis_client is None:
        return alliance_name
    hashed = await _read_hash(
        ctx.redis_client,
        f"wos:instance:{ctx.instance_id}:state",
        "alliance.name",
    )
    return "" if _looks_like_search_placeholder(hashed) else hashed


async def _exec_scan_alliance_members_frame(ctx: DslExecContext) -> None:
    """Parse the currently visible Alliance Members frame.

    This is intentionally a single-frame parser entrypoint. The higher-level
    scanner can call it after opening each rank group / after each swipe and
    merge members by name.
    """
    actions = dsl_runtime.bot_actions()
    image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    if image is None or not hasattr(image, "shape"):
        ctx.result.update({"reason": "capture_failed"})
        return

    parser = AllianceMembersParser()
    snapshot = await parser.parse_with_ocr(image, dsl_runtime.ocr_client())
    result = _snapshot_to_result(snapshot)
    ctx.result.update({"action": "parsed", **result})
    logger.info(
        "dsl exec scan_alliance_members_frame: online=%s/%s ranks=%s members=%d",
        snapshot.online_count,
        snapshot.total_count,
        result["rank_counts"],
        len(snapshot.members),
    )


async def _exec_scan_alliance_members(ctx: DslExecContext) -> None:
    """Expand rank groups, scroll their visible member lists, and persist a roster snapshot."""
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        ctx.result.update({"reason": "missing_player_id"})
        logger.warning("dsl exec scan_alliance_members: missing player_id")
        return

    parser = AllianceMembersParser()
    actions = dsl_runtime.bot_actions()
    ocr_client = dsl_runtime.ocr_client()
    max_swipes_per_rank = _scan_int_arg(ctx, "max_swipes_per_rank", 30)
    settle_s = 0.7

    snapshot = await _capture_members_snapshot(
        parser,
        actions,
        ocr_client,
        instance_id=ctx.instance_id,
    )
    if snapshot is None:
        ctx.result.update({"reason": "capture_failed"})
        return

    ranks = dict(snapshot.ranks)
    scanned_ranks: list[int] = []
    incomplete: dict[str, dict[str, int]] = {}
    # Seed with R5 only — the pinned special leader card (rank 5), not part of a
    # rank group. (Seeding with the whole first frame, as before, tagged still
    # collapsed members rank 0 and let those survive the merge.)
    collected: dict[str, MemberEntry] = dict(
        merge_members_by_name([m for m in snapshot.members if m.rank == 5 and m.name])
    )

    # Walk member ranks top to bottom. R0 is the join "Application List"
    # (applicants, not members), so it is excluded.
    for rank in (4, 3, 2, 1):
        group = ranks.get(rank)
        expected_total = _rank_expected_total(group) if group else 0
        if expected_total <= 0:
            continue

        current = snapshot
        for _attempt in range(8):
            group = current.ranks.get(rank)
            if group and group.count:
                ranks[rank] = group
            if group and group.expanded:
                break
            if group:
                if not await _tap_members_point(actions, ctx.instance_id, group.tap):
                    ctx.result.update({"reason": "tap_rejected", "rank": rank})
                    return
                await asyncio.sleep(settle_s)
                next_snapshot = await _capture_members_snapshot(
                    parser,
                    actions,
                    ocr_client,
                    instance_id=ctx.instance_id,
                    rank_hint=rank,
                )
                if next_snapshot is None:
                    ctx.result.update({"reason": "capture_failed", "rank": rank})
                    return
                current = next_snapshot
                snapshot = next_snapshot
                break
            if not await _swipe_members_down(actions, ctx.instance_id):
                ctx.result.update({"reason": "swipe_rejected", "rank": rank})
                return
            await asyncio.sleep(settle_s)
            next_snapshot = await _capture_members_snapshot(
                parser,
                actions,
                ocr_client,
                instance_id=ctx.instance_id,
            )
            if next_snapshot is None:
                ctx.result.update({"reason": "capture_failed", "rank": rank})
                return
            current = next_snapshot
            snapshot = next_snapshot

        # Collect only cards the parser tags as THIS rank (its rank_hint during
        # scroll); a boundary card from an adjacent group keeps its own rank and
        # is excluded, so there is no cross-rank contamination.
        rank_members: list[MemberEntry] = []
        no_new_swipes = 0
        for _swipe_index in range(max_swipes_per_rank + 1):
            before = len(merge_members_by_name(rank_members))
            rank_members.extend(m for m in current.members if m.rank == rank and m.name)
            after = len(merge_members_by_name(rank_members))
            if after >= expected_total:
                break
            if after <= before:
                no_new_swipes += 1
            else:
                no_new_swipes = 0
            if no_new_swipes >= 2:
                break
            if not await _swipe_members_down(actions, ctx.instance_id):
                ctx.result.update({"reason": "swipe_rejected", "rank": rank})
                return
            await asyncio.sleep(settle_s)
            next_snapshot = await _capture_members_snapshot(
                parser,
                actions,
                ocr_client,
                instance_id=ctx.instance_id,
                rank_hint=rank,
            )
            if next_snapshot is None:
                ctx.result.update({"reason": "capture_failed", "rank": rank})
                return
            current = next_snapshot
            snapshot = next_snapshot

        merged = merge_members_by_name(rank_members)
        # First-seen (top-down) wins, so a member can't be re-tagged by a lower
        # group whose frames briefly showed them.
        collected.update({k: v for k, v in merged.items() if k not in collected})
        if len(merged) < expected_total:
            incomplete[str(rank)] = {"parsed": len(merged), "expected": expected_total}
        scanned_ranks.append(rank)
        ranks.update(current.ranks)

    unique_members = collected
    rank_state = {
        str(rank): {
            "online": group.count,
            "total": group.max_count or group.count,
            "expanded": group.expanded,
        }
        for rank, group in sorted(ranks.items(), reverse=True)
    }
    entries_state = {
        key: _member_to_dict(member)
        for key, member in sorted(unique_members.items())
    }

    try:
        store = get_state_store().get_or_create(player_id)
        alliance_name = await _resolve_alliance_name(ctx, store)
        if not alliance_name:
            ctx.result.update(
                {
                    "reason": "missing_alliance_name",
                    "online_count": snapshot.online_count,
                    "total_count": snapshot.total_count,
                    "members_count": len(entries_state),
                }
            )
            logger.warning("dsl exec scan_alliance_members: missing alliance_name player=%s", player_id)
            return
        await asyncio.to_thread(
            store.update_from_flat,
            {
                "alliance.members.count": snapshot.total_count,
                "alliance.members.max": snapshot.total_count,
                "alliance.members.online": snapshot.online_count,
                "alliance.members.total": snapshot.total_count,
            },
        )
        roster_row = await asyncio.to_thread(
            record_alliance_members_snapshot,
            alliance_name=alliance_name,
            members=entries_state.values(),
        )
        # Append an immutable per-scan snapshot so churn / history can be
        # derived by diffing consecutive scans (the snapshot above only keeps
        # the latest roster). total_count guards churn against partial scans.
        await asyncio.to_thread(
            record_alliance_members_history,
            alliance_name=alliance_name,
            members=entries_state.values(),
            total_count=snapshot.total_count,
        )
    except Exception:
        logger.exception("dsl exec scan_alliance_members: alliance roster persist failed player=%s", player_id)
        ctx.result.update({"reason": "alliance_roster_persist_failed"})
        return

    ctx.result.update(
        {
            "action": "stored",
            "alliance_name": alliance_name,
            "online_count": snapshot.online_count,
            "total_count": snapshot.total_count,
            "rank_counts": rank_state,
            "members_count": len(entries_state),
            "stored_members_count": roster_row["members_count"],
            "scanned_ranks": scanned_ranks,
            "incomplete": incomplete,
        }
    )
    logger.info(
        "dsl exec scan_alliance_members: player=%s online=%d/%d members=%d incomplete=%s",
        player_id,
        snapshot.online_count,
        snapshot.total_count,
        len(entries_state),
        incomplete,
    )


DSL_EXEC_HANDLERS = {
    "scan_alliance_members": _exec_scan_alliance_members,
    "scan_alliance_members_frame": _exec_scan_alliance_members_frame,
    "sync_alliance_stats": _exec_sync_alliance_stats,
}
