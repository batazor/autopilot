"""Named handlers for DSL ``exec:`` steps (see :class:`tasks.dsl_scenario.DslScenarioTask`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from actions.tap import BotActions
from century.api import CenturyAPIError, CenturyClient, PlayerData
from config.buildings import BuildingDef, get_building_registry
from config.devices import upsert_device_gamer
from config.events import match_event_by_ocr
from config.heroes import get_hero_registry
from config.state_store import get_state_store
from gift.redeemer import run_gift_code_redeemer
from gift.scraper import poll_once
from layout.area_lookup import screen_region_by_name
from layout.red_dot_detector import find_red_dots
from layout.types import Point, Region
from navigation.hero_grid_search import scan_grid_frame
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

    args: dict[str, Any] = field(default_factory=dict)
    """Sibling YAML keys on the ``exec:`` step (everything except ``exec`` /
    ``cond``). Each handler reads what it needs; unknown keys are silently
    ignored so adding a new arg never breaks older handlers."""


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


async def _resolve_player_id_for_device_level_exec(ctx: DslExecContext) -> str:
    """Resolve a player binding for execs called from ``device_level: true``
    scenarios.

    Device-level scenarios (``who_i_am``, ``building.upgrade`` during
    tutorial, popup dismissers) are queued with ``player_id=""``. Some of
    their exec handlers still want to write into a specific player's state
    once ``who_i_am`` has run and ``active_player`` is set on the instance
    hash — this helper resolves that binding.

    Player-bound scenarios MUST NOT use this — the implicit identity gate in
    ``DslScenarioExecuteMixin.execute`` already guarantees ``ctx.player_id``
    is non-empty there, and reading the helper buys nothing but a stale
    fallback path.
    """
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
    """Parse OCR'd ``building.name`` text and persist the current building level.

    Called from ``building.upgrade.yaml`` which is ``device_level: true``
    (tutorial-driven flow runs before identity is established). So the
    ``ctx.player_id`` may be empty here and we fall back to
    ``active_player`` via :func:`_resolve_player_id_for_device_level_exec`.
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec sync_building_name: no redis client — skipping")
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
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

    # ``sync_hero_unit.yaml`` is NOT ``device_level: true`` so the identity
    # gate in ``DslScenarioExecuteMixin.execute`` guarantees a non-empty
    # ``ctx.player_id`` before we get here. The defensive check stays for
    # robustness — an empty pid here means the gate was bypassed and the
    # scenario should have been skipped upstream.
    player_id = (ctx.player_id or "").strip()
    if not player_id:
        logger.warning(
            "dsl exec sync_hero_unit: empty player_id — gate bypass? skipping"
        )
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
            x = float(bbox["x"])
            y = float(bbox["y"])
            w = float(bbox["width"])
            h = float(bbox["height"])
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
        results = await client.ocr_regions(
            image,
            pixel_regions,
            region_ids=[f"event_block_{idx}" for idx, _ in regions],
        )
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

    actions = BotActions()
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
                await asyncio.to_thread(actions.tap, ctx.instance_id, point)
            )
        except Exception:
            logger.exception(
                "dsl exec put_all_red_dots: tap failed at (%d,%d) instance=%s",
                point.x,
                point.y,
                ctx.instance_id,
            )
            return
        # ``actions.tap`` returns ``False`` when the operator rejects the
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


_HERO_SHARD_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_HERO_LEVEL_RE = re.compile(r"[Ll]\s*[Vv]\s*\.?\s*(\d+)")
_HERO_GRID_POSITIONS_KEY_FMT = "wos:instance:{instance_id}:hero_grid_positions"
_HERO_GRID_POSITIONS_TTL_SECONDS = 10 * 60
"""Hero positions in the visible roster — short TTL: the player can re-sort
the grid (Power → Stars → …) at any moment, and we'd rather force a fresh
scan than tap a stale cell."""


async def _exec_scan_heroes_grid(ctx: DslExecContext) -> None:
    """Snapshot every visible hero on the ``heroes`` screen into state.yaml.

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

    actions = BotActions()
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

    # Batch-OCR the shard-counter badges on locked cells and the "Lv. X"
    # badges on unlocked cells in one pass (Lv slot is higher than the
    # shard slot — see _LV_DY / _BADGE_DY in hero_grid_search). Mixing both
    # kinds in one call halves the OCR round-trips for a typical grid that
    # has a few unlocked and a few locked heroes.
    locked_items = [(hid, match) for hid, match in hits.items() if not match.available]
    unlocked_items = [(hid, match) for hid, match in hits.items() if match.available]
    badge_text: dict[str, str] = {}
    level_text: dict[str, str] = {}
    regions: list[Region] = []
    ids: list[str] = []
    for hid, match in locked_items:
        regions.append(Region(*match.badge_bbox))
        ids.append(f"hero_shards_{hid}")
    for hid, match in unlocked_items:
        regions.append(Region(*match.level_bbox))
        ids.append(f"hero_level_{hid}")
    if regions:
        try:
            results = await OcrClient().ocr_regions(frame, regions, region_ids=ids)
            for (hid, _), result in zip(locked_items, results[: len(locked_items)], strict=False):
                badge_text[hid] = (result.text or "").strip()
            for (hid, _), result in zip(unlocked_items, results[len(locked_items) :], strict=False):
                level_text[hid] = (result.text or "").strip()
        except Exception:
            logger.exception(
                "dsl exec scan_heroes_grid: badge OCR failed instance=%s",
                ctx.instance_id,
            )

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
    for hid, match in hits.items():
        prev = existing_entries.get(hid)
        entry: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}

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
            # Stale shard counts on a now-unlocked card are misleading;
            # drop them so callers don't see "needs 9/10" on a hero that's
            # already playable.
            entry.pop("shards_current", None)
            entry.pop("shards_required", None)
            text = level_text.get(hid, "")
            lvl_match = _HERO_LEVEL_RE.search(text)
            if lvl_match is not None:
                with contextlib.suppress(ValueError):
                    entry["level"] = int(lvl_match.group(1))
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


DSL_EXEC_REGISTRY: dict[str, DslExecHandler] = {
    "detect_screen": _exec_detect_screen,
    "fetch_player": _exec_fetch_player,
    "gift_code_scrape": _exec_gift_code_scrape,
    "gift_code_redeem": _exec_gift_code_redeem,
    "sync_building_name": _exec_sync_building_name,
    "sync_hero_unit": _exec_sync_hero_unit,
    "scan_event_blocks": _exec_scan_event_blocks,
    "scan_heroes_grid": _exec_scan_heroes_grid,
    "put_all_red_dots": _exec_put_all_red_dots,
}
