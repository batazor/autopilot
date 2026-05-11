"""Named handlers for DSL ``exec:`` steps (see :class:`tasks.dsl_scenario.DslScenarioTask`)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from century.api import CenturyAPIError, CenturyClient, PlayerData
from config.buildings import BuildingDef, get_building_registry
from config.devices import upsert_device_gamer
from config.events import match_event_by_ocr
from config.state_store import get_state_store
from gift.redeemer import run_gift_code_redeemer
from gift.scraper import poll_once
from layout.area_lookup import screen_region_by_name
from layout.types import Region
from navigation.navigator import Navigator
from ocr.client import OcrClient
from ui.notifications import push_ui_notification

_CODES_PATH = Path("db/giftCodes.yaml")
_DEVICES_PATH = Path("db/devices.yaml")

logger = logging.getLogger(__name__)

DslExecHandler = Callable[["DslExecContext"], Awaitable[None]]

_FETCH_PLAYER_TTL_SECONDS = 15 * 60
_GIFT_REDEEM_LOCK_KEY = "wos:gift_code_redeem:lock"
_GIFT_REDEEM_STATE_KEY = "wos:gift_code_redeem:state"
_GIFT_REDEEM_LOCK_TTL_SECONDS = 2 * 60 * 60
_BACKGROUND_GIFT_REDEEM_TASKS: set[asyncio.Task[None]] = set()
_BUILDING_NAME_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?:Lv\.?|Level)\s*\.?\s*(?P<level>\d+)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DslExecContext:
    redis_client: Any | None
    """Async Redis client (same as ``DslScenarioTask.redis_client``)."""

    player_id: str
    """Queue / config player id (Redis hash ``wos:player:<player_id>:state``)."""

    instance_id: str
    """ADB instance id (device)."""


def _decode_redis_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


def _normalise_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _building_by_ocr_name(name: str) -> BuildingDef | None:
    wanted = _normalise_lookup_text(name)
    if not wanted:
        return None
    for building in get_building_registry().buildings:
        if _normalise_lookup_text(building.name) == wanted:
            return building
    return None


def _parse_building_name_level(text: str) -> tuple[BuildingDef, int] | None:
    m = _BUILDING_NAME_RE.match(text or "")
    if not m:
        return None
    building = _building_by_ocr_name(m.group("name"))
    if building is None:
        return None
    try:
        level = int(m.group("level"))
    except ValueError:
        return None
    if level <= 0:
        return None
    return building, level


async def _resolve_player_id(ctx: DslExecContext) -> str:
    player_id = (ctx.player_id or "").strip()
    if player_id or ctx.redis_client is None:
        return player_id
    try:
        raw = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state",
            "active_player",
        )
    except Exception:
        logger.debug("dsl exec: active_player lookup failed", exc_info=True)
        return ""
    return _decode_redis_raw(raw)


async def _exec_fetch_player(ctx: DslExecContext) -> None:
    """POST Century ``/api/player`` using OCR'd ``player_id`` and persist profile fields."""
    if ctx.redis_client is None:
        logger.warning("dsl exec fetch_player: no redis client — skipping")
        return
    if not ctx.player_id.strip():
        logger.warning("dsl exec fetch_player: empty task player_id — skipping")
        return

    state_key = f"wos:player:{ctx.player_id}:state"
    raw_fid = await ctx.redis_client.hget(state_key, "player_id")
    fid_s = _decode_redis_raw(raw_fid)
    if not fid_s:
        logger.warning(
            "dsl exec fetch_player: missing player_id field on %s — run ocr first",
            state_key,
        )
        return
    try:
        fid = int(fid_s)
    except ValueError:
        logger.warning("dsl exec fetch_player: invalid fid %r on %s", fid_s, state_key)
        return

    # TTL guard: skip Century API if we synced recently (UI button is never disabled,
    # but we avoid excessive calls from repeated runs / cron).
    try:
        raw_ts = await ctx.redis_client.hget(state_key, "century_player_sync_at")
        ts_s = _decode_redis_raw(raw_ts)
        ts = float(ts_s) if ts_s else 0.0
    except Exception:
        ts = 0.0
    if ts and (time.time() - ts) < _FETCH_PLAYER_TTL_SECONDS:
        logger.info(
            "dsl exec fetch_player: skip by TTL fid=%s age=%.1fs",
            fid,
            time.time() - ts,
        )
        return

    try:
        data: PlayerData = await CenturyClient().fetch_player(fid)
    except CenturyAPIError as exc:
        logger.warning("dsl exec fetch_player: API error fid=%s: %s", fid, exc)
        return
    except Exception:
        logger.exception("dsl exec fetch_player: unexpected error fid=%s", fid)
        return

    mapping: dict[str, str] = {
        "nickname": data.nickname,
        "stove_level": str(data.stove_level),
        "kid": str(data.kid),
        "stove_lv_content": str(data.stove_lv_content),
        "avatar_image": data.avatar_image or "",
        "century_player_sync_at": str(time.time()),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec fetch_player: redis hset failed key=%s", state_key)
        return

    # Persist to db/state.yaml
    try:
        store = get_state_store().get_or_create(ctx.player_id, nickname=data.nickname)
        store.update_from_flat(
            {
                "nickname": data.nickname,
                "kid": data.kid,
                "avatar": data.avatar_image or "",
                "buildings.furnace.level": data.stove_level,
                "buildings.furnace.power": data.stove_lv_content,
                "buildings.levels.furnace": int(data.stove_level),
                "century_player_sync_at": float(time.time()),
            }
        )
    except Exception:
        logger.exception("dsl exec fetch_player: state.yaml persist failed fid=%s", fid)

    # Persist to db/devices.yaml under current instance_id
    try:
        upsert_device_gamer(
            path=_DEVICES_PATH,
            device_name=ctx.instance_id,
            player_id=ctx.player_id,
            nickname=data.nickname,
        )
    except Exception:
        logger.exception("dsl exec fetch_player: devices.yaml upsert failed fid=%s", fid)

    logger.info(
        "dsl exec fetch_player: synced fid=%s nickname=%r stove_level=%s",
        fid,
        data.nickname,
        data.stove_level,
    )

    # UI toast — fires once per browser tab via the seen-set in click_approvals.
    nick = (data.nickname or "?").strip() or "?"
    msg = f"Player synced: {nick} · stove {data.stove_level} · fid {fid}"
    await push_ui_notification(
        ctx.redis_client,
        ctx.instance_id,
        kind="exec.fetch_player",
        message=msg,
        level="success",
        payload={
            "player_id": ctx.player_id,
            "fid": fid,
            "nickname": data.nickname,
            "stove_level": data.stove_level,
            "kid": data.kid,
        },
    )


async def _exec_sync_building_name(ctx: DslExecContext) -> None:
    """Parse OCR'd ``building.name`` text and persist the current building level."""
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_building_name: no redis client — skipping")
        return

    player_id = await _resolve_player_id(ctx)
    if not player_id:
        logger.warning("dsl exec sync_building_name: empty player_id — skipping")
        return

    state_key = f"wos:player:{player_id}:state"
    raw_text = await ctx.redis_client.hget(state_key, "building.name")
    text = _decode_redis_raw(raw_text)
    text_source = "player"
    if not text:
        raw_text = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state",
            "building.name",
        )
        text = _decode_redis_raw(raw_text)
        text_source = "instance"
    if not text:
        logger.warning("dsl exec sync_building_name: missing building.name OCR text")
        return

    parsed = _parse_building_name_level(text)
    if parsed is None:
        logger.warning("dsl exec sync_building_name: cannot parse building.name=%r", text)
        return

    building, level = parsed
    now = time.time()
    level_field = f"buildings.levels.{building.id}"
    prev_level_raw = await ctx.redis_client.hget(state_key, level_field)
    prev_level = _decode_redis_raw(prev_level_raw)
    mapping = {
        level_field: str(level),
        "building.name.parsed_id": building.id,
        "building.name.parsed_name": building.name,
        "building.name.parsed_level": str(level),
        "building.name.parsed_at": str(now),
    }
    try:
        await ctx.redis_client.hset(state_key, mapping=mapping)
    except Exception:
        logger.exception("dsl exec sync_building_name: redis hset failed key=%s", state_key)
        return

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(
            {
                level_field: level,
                "buildings.state.text": text,
            }
        )
    except Exception:
        logger.exception(
            "dsl exec sync_building_name: state.yaml persist failed player=%s",
            player_id,
        )

    changed = prev_level != str(level)
    logger.info(
        "dsl exec sync_building_name: %s player=%s instance=%s building=%s old=%s new=%s text=%r source=%s",
        "updated" if changed else "unchanged",
        player_id,
        ctx.instance_id,
        building.id,
        prev_level or "?",
        level,
        text,
        text_source,
    )


async def _exec_sync_hero_unit(ctx: DslExecContext) -> None:
    """Snapshot the currently-open hero card into ``db/state.yaml``.

    Expects the surrounding scenario to have just OCR'd
    ``page.heroes.unit.name`` and ``page.heroes.unit.level`` via ``store:``,
    so the values are sitting in ``wos:player:<pid>:state``. The hero ID is
    a normalised name slug (e.g. ``"Bahiti"`` → ``"bahiti"``) and the snapshot
    overwrites ``heroes.entries.<id>`` — re-running on the same card just
    refreshes the level, no merge.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_hero_unit: no redis client — skipping")
        return

    player_id = await _resolve_player_id(ctx)
    if not player_id:
        logger.warning("dsl exec sync_hero_unit: empty player_id — skipping")
        return

    state_key = f"wos:player:{player_id}:state"
    raw_name = await ctx.redis_client.hget(state_key, "page.heroes.unit.name")
    name = _decode_redis_raw(raw_name)
    if not name:
        logger.warning(
            "dsl exec sync_hero_unit: missing page.heroes.unit.name in %s — "
            "did the surrounding scenario OCR it with store:?",
            state_key,
        )
        return

    raw_level = await ctx.redis_client.hget(state_key, "page.heroes.unit.level")
    level_text = _decode_redis_raw(raw_level)
    try:
        level = int(level_text)
    except (TypeError, ValueError):
        logger.warning(
            "dsl exec sync_hero_unit: cannot parse level %r for hero %r",
            level_text, name,
        )
        return

    hero_id = _normalise_lookup_text(name)
    if not hero_id:
        logger.warning("dsl exec sync_hero_unit: empty hero id from name=%r", name)
        return

    now = time.time()
    entry_path = f"heroes.entries.{hero_id}"
    snapshot = {
        "name": name,
        "level": level,
        "seen_at": now,
    }
    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat({entry_path: snapshot})
    except Exception:
        logger.exception(
            "dsl exec sync_hero_unit: state.yaml persist failed player=%s hero=%s",
            player_id, hero_id,
        )
        return

    logger.info(
        "dsl exec sync_hero_unit: hero=%s name=%r level=%d player=%s",
        hero_id, name, level, player_id,
    )


async def _exec_detect_screen(ctx: DslExecContext) -> None:
    """Detect current page and persist ``wos:instance:<id>:state.current_screen``."""
    actions = BotActions()
    navigator = Navigator(
        actions.capture_screen_bgr,
        actions.tap,
        redis_client=ctx.redis_client,
    )
    detected = await navigator.detect_current_screen(ctx.instance_id)
    logger.info(
        "dsl exec detect_screen: instance=%s detected=%s",
        ctx.instance_id,
        detected or "(unknown)",
    )


async def _exec_gift_code_scrape(ctx: DslExecContext) -> None:
    """Scrape wosrewards.com for new gift codes and append them to giftCodes.yaml."""
    try:
        new = await poll_once(_CODES_PATH)
    except Exception:
        logger.exception("dsl exec gift_code_scrape: scraper failed")
        return
    if new:
        logger.info("dsl exec gift_code_scrape: found %d new code(s): %s", len(new), ", ".join(new))
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_scrape",
            message=f"New gift codes found: {', '.join(new)}",
            level="info",
            payload={"codes": new},
        )
    else:
        logger.info("dsl exec gift_code_scrape: no new codes")


async def _acquire_gift_redeem_lock(ctx: DslExecContext, token: str) -> bool:
    if ctx.redis_client is None:
        return not any(not t.done() for t in _BACKGROUND_GIFT_REDEEM_TASKS)
    try:
        ok = await ctx.redis_client.set(
            _GIFT_REDEEM_LOCK_KEY,
            token,
            nx=True,
            ex=_GIFT_REDEEM_LOCK_TTL_SECONDS,
        )
    except Exception:
        logger.exception("dsl exec gift_code_redeem: lock acquire failed")
        return False
    return bool(ok)


async def _release_gift_redeem_lock(ctx: DslExecContext, token: str) -> None:
    if ctx.redis_client is None:
        return
    try:
        raw = await ctx.redis_client.get(_GIFT_REDEEM_LOCK_KEY)
        if _decode_redis_raw(raw) == token:
            await ctx.redis_client.delete(_GIFT_REDEEM_LOCK_KEY)
    except Exception:
        logger.debug("dsl exec gift_code_redeem: lock release failed", exc_info=True)


async def _write_gift_redeem_state(ctx: DslExecContext, **fields: object) -> None:
    if ctx.redis_client is None:
        return
    mapping = {str(k): str(v) for k, v in fields.items() if v is not None}
    if not mapping:
        return
    try:
        await ctx.redis_client.hset(_GIFT_REDEEM_STATE_KEY, mapping=mapping)
        await ctx.redis_client.expire(_GIFT_REDEEM_STATE_KEY, 7 * 24 * 60 * 60)
    except Exception:
        logger.debug("dsl exec gift_code_redeem: state write failed", exc_info=True)


async def _run_gift_code_redeem_background(ctx: DslExecContext, token: str) -> None:
    started_at = time.time()
    await _write_gift_redeem_state(
        ctx,
        status="running",
        started_at=started_at,
        instance_id=ctx.instance_id,
        token=token,
    )
    await push_ui_notification(
        ctx.redis_client,
        ctx.instance_id,
        kind="exec.gift_code_redeem.started",
        message="Gift code redeem started in background",
        level="info",
        payload={"started_at": started_at},
    )

    try:
        summary = await run_gift_code_redeemer(_CODES_PATH, _DEVICES_PATH)
    except Exception as exc:
        finished_at = time.time()
        logger.exception("dsl exec gift_code_redeem: background redeemer failed")
        await _write_gift_redeem_state(
            ctx,
            status="failed",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            error=f"{type(exc).__name__}: {exc!s}",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem.failed",
            message=f"Gift code redeem failed: {type(exc).__name__}",
            level="error",
            payload={"error": f"{type(exc).__name__}: {exc!s}"},
        )
        return
    finally:
        await _release_gift_redeem_lock(ctx, token)

    finished_at = time.time()
    counts = summary.counts_by_status()
    total = len(summary.results)
    if total:
        counts_s = ", ".join(f"{k}={v}" for k, v in counts.items())
        logger.info("dsl exec gift_code_redeem: background done total=%d %s", total, counts_s)
        await _write_gift_redeem_state(
            ctx,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=total,
            counts=counts_s,
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem",
            message=f"Gift code redeem done: {counts_s}",
            level="info",
            payload=summary.to_dict(),
        )
    else:
        logger.info("dsl exec gift_code_redeem: background done, nothing pending")
        await _write_gift_redeem_state(
            ctx,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=0,
            counts="",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem",
            message="Gift code redeem done: nothing pending",
            level="info",
            payload=summary.to_dict(),
        )


async def _exec_gift_code_redeem(ctx: DslExecContext) -> None:
    """Start gift-code redemption in the background.

    The Century API flow does not require ADB or active game UI. Running it as a
    background task lets the instance worker return to normal bot control
    immediately while the API redeem continues on the shared asyncio loop.
    """
    if not _CODES_PATH.exists():
        logger.warning("dsl exec gift_code_redeem: %s not found — skipping", _CODES_PATH)
        return
    if not _DEVICES_PATH.exists():
        logger.warning("dsl exec gift_code_redeem: %s not found — skipping", _DEVICES_PATH)
        return

    token = uuid.uuid4().hex
    if not await _acquire_gift_redeem_lock(ctx, token):
        logger.info("dsl exec gift_code_redeem: already running — skip background start")
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem.already_running",
            message="Gift code redeem is already running",
            level="info",
        )
        return

    await _write_gift_redeem_state(
        ctx,
        status="queued",
        queued_at=time.time(),
        instance_id=ctx.instance_id,
        token=token,
    )
    task = asyncio.create_task(
        _run_gift_code_redeem_background(ctx, token),
        name="gift-code-redeem-background",
    )
    _BACKGROUND_GIFT_REDEEM_TASKS.add(task)

    def _on_done(done: asyncio.Task[None]) -> None:
        _BACKGROUND_GIFT_REDEEM_TASKS.discard(done)
        with suppress(asyncio.CancelledError):
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "gift-code redeem background task crashed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

    task.add_done_callback(_on_done)
    logger.info("dsl exec gift_code_redeem: started background task")


_SCAN_EVENT_BLOCKS_REGIONS: tuple[str, ...] = (
    "main_city.event.block.1",
    "main_city.event.block.2",
    "main_city.event.block.3",
    "main_city.event.block.4",
)
_EVENT_BLOCKS_HASH_TTL_SECONDS = 30 * 60


def _load_area_doc() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "area.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("dsl exec: failed to load area.json")
        return {}


async def _exec_scan_event_blocks(ctx: DslExecContext) -> None:
    """OCR the four main_city event-block bboxes and record which event each
    currently shows in Redis hash ``wos:instance:<id>:event_blocks``.

    Hash entry per block index: ``"<N>" -> "event.<name>"`` (only blocks whose
    OCR fuzzy-matches an entry in ``config/events.yaml`` are written; chest
    cooldowns / promo offers leave their slot empty).
    """
    area_doc = _load_area_doc()
    if not area_doc:
        return

    regions: list[tuple[int, Region]] = []
    for idx, region_name in enumerate(_SCAN_EVENT_BLOCKS_REGIONS, start=1):
        pair = screen_region_by_name(area_doc, region_name)
        if pair is None:
            continue
        bbox = pair[1].get("bbox") if isinstance(pair[1], dict) else None
        if not isinstance(bbox, dict):
            continue
        try:
            x = float(bbox["x"]); y = float(bbox["y"])
            w = float(bbox["width"]); h = float(bbox["height"])
        except (KeyError, TypeError, ValueError):
            continue
        regions.append((idx, Region(int(round(x)), int(round(y)), int(round(w)), int(round(h)))))

    if not regions:
        logger.warning("dsl exec scan_event_blocks: no event-block regions resolvable")
        return

    actions = BotActions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec scan_event_blocks: capture_screen_bgr failed instance=%s",
            ctx.instance_id,
        )
        return

    H, W = image.shape[:2]
    # area.json bboxes are in percentage units; convert per-image.
    pixel_regions = [
        Region(
            int(round(r.x / 100.0 * W)),
            int(round(r.y / 100.0 * H)),
            int(round(r.w / 100.0 * W)),
            int(round(r.h / 100.0 * H)),
        )
        for _idx, r in regions
    ]

    client = OcrClient()
    try:
        results = await client.ocr_regions(image, pixel_regions)
    except Exception:
        logger.exception(
            "dsl exec scan_event_blocks: OCR call failed instance=%s",
            ctx.instance_id,
        )
        return

    mapping: dict[str, str] = {}
    for (idx, _r), result in zip(regions, results, strict=False):
        text = (result.text or "").strip()
        event = match_event_by_ocr(text)
        logger.info(
            "dsl exec scan_event_blocks: instance=%s block=%d ocr=%r resolved=%s",
            ctx.instance_id,
            idx,
            text,
            event.name if event else "(none)",
        )
        if event is not None:
            mapping[str(idx)] = event.name

    if ctx.redis_client is None:
        return

    hash_key = f"wos:instance:{ctx.instance_id}:event_blocks"
    try:
        # Drop stale entries so an event that vanished from a block doesn't
        # linger past the next scan; only blocks resolved in this run get
        # written back.
        await ctx.redis_client.delete(hash_key)
        if mapping:
            await ctx.redis_client.hset(hash_key, mapping=mapping)
            await ctx.redis_client.expire(hash_key, _EVENT_BLOCKS_HASH_TTL_SECONDS)
    except Exception:
        logger.exception(
            "dsl exec scan_event_blocks: redis write failed key=%s", hash_key
        )

    # Mirror the per-block resolution into the instance state hash so DSL
    # `cond:` filters can gate clicks on it (e.g. skip block.2 when it shows
    # the "1st Purchase" promo). Fields written: `event_blocks.1`..`.4`. Each
    # scan first HDEL-s all four fields, then HSET-s only the resolved ones.
    instance_state_key = f"wos:instance:{ctx.instance_id}:state"
    field_names = [
        f"event_blocks.{idx}" for idx in range(1, len(_SCAN_EVENT_BLOCKS_REGIONS) + 1)
    ]
    try:
        await ctx.redis_client.hdel(instance_state_key, *field_names)
        if mapping:
            await ctx.redis_client.hset(
                instance_state_key,
                mapping={f"event_blocks.{idx}": val for idx, val in mapping.items()},
            )
    except Exception:
        logger.exception(
            "dsl exec scan_event_blocks: instance-state mirror failed key=%s",
            instance_state_key,
        )


DSL_EXEC_REGISTRY: dict[str, DslExecHandler] = {
    "detect_screen": _exec_detect_screen,
    "fetch_player": _exec_fetch_player,
    "gift_code_scrape": _exec_gift_code_scrape,
    "gift_code_redeem": _exec_gift_code_redeem,
    "sync_building_name": _exec_sync_building_name,
    "sync_hero_unit": _exec_sync_hero_unit,
    "scan_event_blocks": _exec_scan_event_blocks,
}
