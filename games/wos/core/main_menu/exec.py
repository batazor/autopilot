"""DSL exec handlers for the main menu panel."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
from rapidfuzz import fuzz

from config.state_store import get_state_store
from dashboard.dashboard_events import publish_dashboard_event_throttled_async
from layout.types import Point, Region
from tasks import dsl_runtime
from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)
from tasks.dsl_scenario_helpers import _enqueue_scenario, _parse_hms_to_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

    from ocr.client import OcrClient

logger = logging.getLogger(__name__)

_TROOP_TYPES: tuple[str, ...] = ("infantry", "lancer", "marksman")
_MARCH_SLOT_COUNT = 6


async def _read_hash(redis_client: Any, key: str, field: str) -> str:
    raw = await redis_client.hget(key, field)
    return _decode_redis_raw(raw)


def _parse_remaining_seconds(raw_seconds: str, raw_text: str) -> int | None:
    try:
        seconds = int(str(raw_seconds or "").strip())
    except (TypeError, ValueError):
        seconds = -1
    if seconds >= 0:
        return seconds
    parsed = _parse_hms_to_seconds(raw_text)
    return int(parsed) if parsed is not None else None


def _row_seen(troop_type: str, status_text: str, timer_text: str) -> bool:
    haystack = f"{status_text} {timer_text}".lower()
    if troop_type == "marksman":
        return "marksman" in haystack or "mark" in haystack
    return troop_type in haystack


def _parse_marching_count(text: str) -> tuple[int, int]:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], 0
    return 0, 0


def _is_idle_text(text: str) -> bool:
    return "idle" in (text or "").lower()


async def _resolve_sync_target(
    ctx: DslExecContext, label: str
) -> tuple[str, str, float] | None:
    """Shared preamble for the main-menu state-sync handlers.

    Returns ``(player_id, redis_key, now)`` or ``None`` after writing the
    matching ``reason`` to ``ctx.result`` (no redis client / no active player).
    """
    if ctx.redis_client is None:
        logger.warning("dsl exec %s: no redis client", label)
        ctx.result.update({"reason": "no_redis_client"})
        return None
    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        logger.warning("dsl exec %s: empty player_id", label)
        ctx.result.update({"reason": "empty_player_id"})
        return None
    return player_id, f"wos:player:{player_id}:state", time.time()


async def _exec_sync_main_menu_training_status(ctx: DslExecContext) -> None:
    """Persist visible troop-training timers from the main menu into player state."""
    target = await _resolve_sync_target(ctx, "sync_main_menu_training_status")
    if target is None:
        return
    player_id, redis_key, now = target
    updates: dict[str, object] = {}
    result: dict[str, object] = {}

    for troop_type in _TROOP_TYPES:
        prefix = f"main_menu.training.{troop_type}"
        raw_seconds = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.remaining_s")
        timer_text = await _read_hash(
            ctx.redis_client, redis_key, f"{prefix}.remaining_s_text"
        )
        status_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status")
        status_text_raw = await _read_hash(
            ctx.redis_client, redis_key, f"{prefix}.status_text"
        )
        status = status_text or status_text_raw
        remaining_s = _parse_remaining_seconds(raw_seconds, timer_text)
        seen = remaining_s is not None or _row_seen(troop_type, status, timer_text)
        if not seen:
            result[troop_type] = {"action": "skipped", "reason": "row_not_seen"}
            continue

        remaining = max(0, int(remaining_s or 0))
        is_available = remaining <= 0
        text_status = timer_text.strip() if remaining > 0 else ""
        ends_at = now + remaining if remaining > 0 else 0.0
        state_prefix = f"troops.{troop_type}.state"
        updates.update(
            {
                f"{state_prefix}.isAvailable": is_available,
                f"{state_prefix}.TextStatus": text_status,
                f"{state_prefix}.training_remaining_s": remaining,
                f"{state_prefix}.training_ends_at": ends_at,
                f"{state_prefix}.training_checked_at": now,
            }
        )
        result[troop_type] = {
            "available": is_available,
            "remaining_s": remaining,
            "text": text_status,
            "ends_at": ends_at,
        }

    if not updates:
        logger.warning(
            "dsl exec sync_main_menu_training_status: no troop rows recognized player=%s",
            player_id,
        )
        ctx.result.update({"reason": "no_rows_recognized", "troops": result})
        return

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec sync_main_menu_training_status: state persist failed player=%s",
            player_id,
        )
        ctx.result.update({"reason": "state_persist_failed", "troops": result})
        return

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_main_menu_training_status",
    )
    ctx.result.update({"action": "stored", "troops": result})
    logger.info(
        "dsl exec sync_main_menu_training_status: stored player=%s troops=%s",
        player_id,
        result,
    )


async def _exec_sync_main_menu_research_status(ctx: DslExecContext) -> None:
    """Persist the visible Tech Research row from the main menu into player state.

    Mirrors the troop-training sync: the City tab shows the active research
    (name + "4d 11:59:43"-style bar timer, OCR'd via the ``bar_timer``
    preprocess). An empty/idle row means the research slot is available.
    """
    target = await _resolve_sync_target(ctx, "sync_main_menu_research_status")
    if target is None:
        return
    player_id, redis_key, now = target
    prefix = "main_menu.research.slot"
    raw_seconds = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.remaining_s")
    timer_text = await _read_hash(
        ctx.redis_client, redis_key, f"{prefix}.remaining_s_text"
    )
    status_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status")
    status_text_raw = await _read_hash(
        ctx.redis_client, redis_key, f"{prefix}.status_text"
    )
    name = (status_text or status_text_raw).strip()
    remaining_s = _parse_remaining_seconds(raw_seconds, timer_text)

    if remaining_s is None and not name:
        logger.warning(
            "dsl exec sync_main_menu_research_status: row not recognized player=%s",
            player_id,
        )
        ctx.result.update({"reason": "row_not_recognized"})
        return

    remaining = max(0, int(remaining_s or 0))
    is_available = remaining <= 0
    ends_at = now + remaining if remaining > 0 else 0.0
    updates: dict[str, object] = {
        "research.center.state.isAvailable": is_available,
        "research.center.state.current": "" if is_available else name,
        "research.center.state.TextStatus": timer_text.strip() if remaining > 0 else "",
        "research.center.state.research_remaining_s": remaining,
        "research.center.state.research_ends_at": ends_at,
        "research.center.state.research_checked_at": now,
    }
    result = {
        "available": is_available,
        "current": name,
        "remaining_s": remaining,
        "ends_at": ends_at,
    }

    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec sync_main_menu_research_status: state persist failed player=%s",
            player_id,
        )
        ctx.result.update({"reason": "state_persist_failed", "research": result})
        return

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_main_menu_research_status",
    )
    ctx.result.update({"action": "stored", "research": result})
    logger.info(
        "dsl exec sync_main_menu_research_status: stored player=%s research=%s",
        player_id,
        result,
    )


async def _exec_sync_main_menu_marching_status(ctx: DslExecContext) -> None:
    """Persist visible Wilderness march queue rows from the main menu."""
    target = await _resolve_sync_target(ctx, "sync_main_menu_marching_status")
    if target is None:
        return
    player_id, redis_key, now = target
    count_text = await _read_hash(
        ctx.redis_client,
        redis_key,
        "main_menu.marching.count_text",
    )
    if not count_text:
        count_text = await _read_hash(ctx.redis_client, redis_key, "main_menu.marching.count")
    active_count, capacity = _parse_marching_count(count_text)

    slots: dict[str, object] = {}
    recognized = 0
    for slot_no in range(1, _MARCH_SLOT_COUNT + 1):
        prefix = f"main_menu.marching.slot.{slot_no}"
        title = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.title")
        title_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.title_text")
        status = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status")
        status_text = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.status_text")
        raw_seconds = await _read_hash(ctx.redis_client, redis_key, f"{prefix}.remaining_s")
        timer_text = await _read_hash(
            ctx.redis_client,
            redis_key,
            f"{prefix}.remaining_s_text",
        )

        label = (title or title_text or "").strip()
        row_text = (status or status_text or label or timer_text).strip()
        remaining_s = _parse_remaining_seconds(raw_seconds, timer_text)
        is_idle = _is_idle_text(row_text) and remaining_s is None
        is_active = bool(label) or remaining_s is not None
        if not (is_idle or is_active):
            continue

        recognized += 1
        remaining = max(0, int(remaining_s or 0))
        ends_at = now + remaining if remaining > 0 else 0.0
        slots[str(slot_no)] = {
            "slot": slot_no,
            "status": "idle" if is_idle else "marching",
            "label": "" if is_idle else label,
            "raw_text": row_text,
            "time_text": timer_text.strip(),
            "remaining_s": remaining,
            "ends_at": ends_at,
            "checked_at": now,
        }

    if recognized == 0:
        logger.warning(
            "dsl exec sync_main_menu_marching_status: no march rows recognized player=%s",
            player_id,
        )
        ctx.result.update({"reason": "no_rows_recognized"})
        return

    if capacity <= 0:
        capacity = _MARCH_SLOT_COUNT
    updates: dict[str, object] = {
        "marches.active_count": active_count,
        "marches.capacity": capacity,
        "marches.checked_at": now,
        "marches.slots": slots,
    }
    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec sync_main_menu_marching_status: state persist failed player=%s",
            player_id,
        )
        ctx.result.update({"reason": "state_persist_failed", "slots": slots})
        return

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="sync_main_menu_marching_status",
    )
    ctx.result.update(
        {
            "action": "stored",
            "active_count": active_count,
            "capacity": capacity,
            "slots": slots,
        }
    )
    logger.info(
        "dsl exec sync_main_menu_marching_status: stored player=%s active=%d/%d slots=%s",
        player_id,
        active_count,
        capacity,
        slots,
    )


# --- City-panel scanner ------------------------------------------------------
#
# The City tab of the main menu is one scrollable list: section headers
# ("Building Queue", "Training", "Tech Research", …) over uniform task cards
# (left icon, centered title + status line, right action button). Fixed
# bboxes are scroll-fragile — sections shift as content appears — so the
# scanner segments whatever is on screen: row anchors come from the bright
# left icons, sections from the header gaps (with title-keyword inference as
# the primary signal), statuses from OCR with bar-timer / red-text recovery.

_PANEL_TOP_Y_PCT = 22.5
_PANEL_BOTTOM_Y_PCT = 68.0
_ICON_X0_PCT, _ICON_X1_PCT = 3.9, 11.7
_TEXT_X0_PCT, _TEXT_X1_PCT = 13.2, 54.2
_HEADER_X0_PCT, _HEADER_X1_PCT = 4.2, 47.0
_BUTTON_X0_PCT, _BUTTON_X1_PCT = 54.9, 64.2

# Row-title keywords → (section, row_slug). Title inference beats header OCR:
# headers sit on busy translucent background and the first visible row often
# has its header scrolled off-screen.
_ROW_TITLE_MAP: tuple[tuple[str, str, str], ...] = (
    ("building queue", "building_queue", ""),  # slug derived from the queue no.
    ("infantry", "training", "infantry"),
    ("lancer", "training", "lancer"),
    ("marksman", "training", "marksman"),
    ("center research", "tech_research", "center"),
    ("war academy", "tech_research", "war_academy"),
    ("learn skills", "expert", "learn_skills"),
    ("alliance contribution", "alliance_contribution", "alliance_contribution"),
    ("online rewards", "my_rewards", "online_rewards"),
    ("pet adventure", "pet_adventure", "pet_adventure"),
    ("tree of life", "life_essence", "tree_of_life"),
    ("land of heroes", "labyrinth", "land_of_heroes"),
    ("cave of monsters", "labyrinth", "cave_of_monsters"),
    ("charm mine", "labyrinth", "charm_mine"),
    ("research center", "labyrinth", "research_center"),
    ("gear forge", "labyrinth", "gear_forge"),
    ("gaia heart", "labyrinth", "gaia_heart"),
    ("tundra trek", "trek", "tundra_trek"),
    ("children's day", "childrens_day", "childrens_day"),
    ("childrens day", "childrens_day", "childrens_day"),
    ("popularity king competition", "popularity_king", "popularity_king_competition"),
    ("polar popularity", "popularity_king", "polar_popularity"),
    ("sweet heart castle", "popularity_king", "sweet_heart_castle"),
    ("heart belongs castle", "popularity_king", "heart_belongs_castle"),
    ("rose defense battle", "rose_defense", "rose_defense_battle"),
    ("bloom battle", "rose_defense", "bloom_battle"),
    ("flower-eating beasts", "rose_defense", "flower_eating_beasts"),
    ("flower eating beasts", "rose_defense", "flower_eating_beasts"),
    ("honey language mall", "honey_language_mall", "honey_language_mall"),
    ("sweet whispers shop", "honey_language_mall", "sweet_whispers_shop"),
    ("honeymoon trip", "honeymoon_trip", "honeymoon_trip"),
)

_HEADER_SECTION_MAP: tuple[tuple[str, str], ...] = (
    ("building queue", "building_queue"),
    ("training", "training"),
    ("tech research", "tech_research"),
    ("expert", "expert"),
    ("alliance contribution", "alliance_contribution"),
    ("recruit heroes", "recruit_heroes"),
    ("my rewards", "my_rewards"),
    ("pet adventure", "pet_adventure"),
    ("life essence", "life_essence"),
    ("labyrinth", "labyrinth"),
    ("trek", "trek"),
    ("children's day", "childrens_day"),
    ("childrens day", "childrens_day"),
    ("popularity king", "popularity_king"),
    ("polar popularity", "popularity_king"),
    ("rose defense", "rose_defense"),
    ("bloom battle", "rose_defense"),
    ("honey language mall", "honey_language_mall"),
    ("sweet whispers shop", "honey_language_mall"),
    ("honeymoon trip", "honeymoon_trip"),
)

# Recruit Heroes rows keyed by title once the section is known (the bare
# titles "Advanced" / "Epic" are too generic for the global title map).
_RECRUIT_ROW_TITLES = ("advanced", "epic")

_STATUS_FUZZ_THRESHOLD = 80.0
_IDLE_BUILDING_QUEUE_SCENARIOS = {
    "queue_1": "building_queue_1_empty",
    "queue_2": "building_queue_2_empty",
}

# Declarative per-row dispatch: each actionable (section, kind) row pushes its OWN
# dedicated scenario. ``scenario`` is a fixed key, a ``{row}`` template, or
# ``scenario_map`` (per-row). ``rows`` (optional) restricts which row slugs match.
# EVERY push self-gates on the TARGET scenario's YAML ``enabled`` flag, so a
# scaffolded (``enabled: false``) scenario is wired here but never pushed until it
# is flipped on (after on-device labeling) — the single switch that activates a row.
# ``kinds`` = the actionable states that warrant a push (never in_progress/locked/
# empty). Priorities: starts (build/train/research) 78-80k sit ABOVE claims (74k)
# so a free build/training queue is filled before opportunistic claims.
_PANEL_DISPATCH: tuple[dict[str, Any], ...] = (
    # --- starts (idle slot → begin work) ---
    {"section": "training", "kinds": ("completed",), "scenario": "accept_troops_{row}",
     "priority": 80_000, "rows": _TROOP_TYPES},
    {"section": "training", "kinds": ("idle",), "scenario": "troops.{row}.train",
     "priority": 78_000, "rows": _TROOP_TYPES},
    {"section": "building_queue", "kinds": ("idle",), "scenario_map": _IDLE_BUILDING_QUEUE_SCENARIOS,
     "priority": 80_000},
    {"section": "tech_research", "kinds": ("idle",), "scenario": "start_idle_research",
     "priority": 78_000, "rows": ("center",)},
    {"section": "tech_research", "kinds": ("idle",), "scenario": "start_idle_war_academy",
     "priority": 78_000, "rows": ("war_academy",)},  # scaffold (enabled:false)
    # --- claims/collects (green button / claimable) ---
    {"section": "alliance_contribution", "kinds": ("claimable",), "scenario": "alliance.tech.contribute",
     "priority": 74_000},
    {"section": "recruit_heroes", "kinds": ("free",), "scenario": "free_recruitments_today",
     "priority": 74_000},
    {"section": "pet_adventure", "kinds": ("completed", "free"), "scenario": "journey_of_light",
     "priority": 74_000},
    {"section": "labyrinth", "kinds": ("claimable",), "scenario": "event.labyrinth.{row}",
     "priority": 74_000},
    {"section": "trek", "kinds": ("claimable",), "scenario": "event.tundra_trek",
     "priority": 74_000},
    {"section": "my_rewards", "kinds": ("completed", "claimable"), "scenario": "claim_online_rewards",
     "priority": 74_000},  # scaffold (enabled:false)
    {"section": "life_essence", "kinds": ("claimable",), "scenario": "claim_life_essence",
     "priority": 74_000},  # scaffold (enabled:false)
    {"section": "expert", "kinds": ("idle", "claimable"), "scenario": "learn_skills",
     "priority": 74_000},  # scaffold (enabled:false)
    # --- seasonal events (row visible only while the event is live → self-gating) ---
    {"section": "childrens_day", "kinds": ("claimable",), "scenario": "event.childrens_day",
     "priority": 74_000},
    {"section": "popularity_king", "kinds": ("claimable",), "scenario": "event.popularity_king_competition",
     "priority": 74_000},
    {"section": "rose_defense", "kinds": ("claimable",), "scenario": "event.rose_defense_battle",
     "priority": 74_000},
    {"section": "honey_language_mall", "kinds": ("claimable",), "scenario": "event.honey_language_mall",
     "priority": 74_000},
    {"section": "honeymoon_trip", "kinds": ("claimable",), "scenario": "event.honeymoon_trip",
     "priority": 74_000},
)


def _resolve_dispatch_scenario(rule: dict[str, Any], row: str) -> str:
    """Resolve a dispatch rule's scenario key for a given row slug (or '' if none)."""
    smap = rule.get("scenario_map")
    if smap is not None:
        return str(smap.get(row) or "")
    return str(rule.get("scenario") or "").format(row=row)


def _dispatch_rule_for(section: str, kind: str, row: str) -> dict[str, Any] | None:
    """First dispatch rule matching (section, kind, row) — the row's owning rule."""
    for rule in _PANEL_DISPATCH:
        if rule["section"] != section or kind not in rule["kinds"]:
            continue
        allowed = rule.get("rows")
        if allowed is not None and row not in allowed:
            continue
        return rule
    return None


def _slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    return out.strip("_") or "unknown"


def _clean_ocr_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip(" \\|/‘'`).,!(").strip()


def _panel_row_anchors(image_bgr: np.ndarray) -> list[tuple[int, int]]:
    """Task-card anchors: the bright circular icons on the card's left edge."""
    h, w = image_bgr.shape[:2]
    y0, y1 = int(_PANEL_TOP_Y_PCT / 100 * h), int(_PANEL_BOTTOM_Y_PCT / 100 * h)
    x0, x1 = int(_ICON_X0_PCT / 100 * w), int(_ICON_X1_PCT / 100 * w)
    hsv = cv2.cvtColor(image_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 2] > 150).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    anchors: list[tuple[int, int]] = []
    for c in contours:
        _bx, by, bw, bh = cv2.boundingRect(c)
        if bw >= 28 and 36 <= bh <= 58:
            anchors.append((y0 + by, y0 + by + bh))
    return sorted(anchors)


def _row_button(image_bgr: np.ndarray, cy: int) -> tuple[str, bool]:
    """(kind, has_red_dot) for the action button right of the card at ``cy``.

    kind: ``green`` (claim/collect check), ``blue`` (navigate arrow), ``""``
    when the row has no button (e.g. a locked "Not yet built" research slot).
    """
    h, w = image_bgr.shape[:2]
    bx0, bx1 = int(_BUTTON_X0_PCT / 100 * w), int(_BUTTON_X1_PCT / 100 * w)
    by0, by1 = max(0, cy - 27), min(h, cy + 27)
    hsv = cv2.cvtColor(image_bgr[by0:by1, bx0:bx1], cv2.COLOR_BGR2HSV)
    saturated = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)
    if int(saturated.sum()) < 250:
        return "", False
    hue = float(np.median(hsv[:, :, 0][saturated]))
    if 35 <= hue <= 85:
        kind = "green"
    elif 86 <= hue <= 125:
        kind = "blue"
    else:
        kind = "other"
    red = (
        ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))
        & (hsv[:, :, 1] > 150)
        & (hsv[:, :, 2] > 150)
    )
    return kind, int(red.sum()) > 60


async def _ocr_colored_text(
    ocr: OcrClient, image_bgr: np.ndarray, x: int, y: int, w: int, h: int
) -> str:
    """Recover saturated status text plain OCR misses on the translucent card.

    Red "Not yet built" and green "Free" / "Raid Available" glyphs blend into
    the card for Tesseract; a saturation mask isolates them cleanly.
    """
    crop = image_bgr[y : y + h, x : x + w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    colored = (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 110)
    if int(colored.sum()) < 300:
        return ""
    mask = colored.astype(np.uint8) * 255
    inverted = 255 - cv2.resize(mask, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    tile = cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR)
    res = await ocr.ocr_region(
        tile, Region(0, 0, tile.shape[1], tile.shape[0]), preprocess="fast_line"
    )
    return _clean_ocr_line(res.text)


def _classify_status(text: str) -> tuple[str, int]:
    """(kind, remaining_s) from a status line."""
    t = (text or "").strip().lower()
    if not t:
        return "empty", 0
    secs = _parse_hms_to_seconds(t)
    if secs is not None:
        return "in_progress", int(secs)
    if "completed" in t or fuzz.partial_ratio("completed", t) >= _STATUS_FUZZ_THRESHOLD:
        return "completed", 0
    if "idle" in t:
        return "idle", 0
    if (
        "not yet bui" in t
        or "locked" in t
        or fuzz.partial_ratio("not yet built", t) >= _STATUS_FUZZ_THRESHOLD
    ):
        return "locked", 0
    if "free" in t:
        return "free", 0
    if (
        "contribute" in t
        or "available" in t
        or "raid" in t
        or fuzz.partial_ratio("available", t) >= _STATUS_FUZZ_THRESHOLD
        or fuzz.partial_ratio("explorable", t) >= _STATUS_FUZZ_THRESHOLD
    ):
        return "claimable", 0
    return "unknown", 0


def _section_for_row(title: str, header_text: str, prev_section: str) -> tuple[str, str]:
    """(section, row_slug) — title keywords first, then header fuzz, then carry."""
    t = (title or "").strip().lower()
    for needle, section, row_slug in _ROW_TITLE_MAP:
        if needle in t or fuzz.partial_ratio(needle, t) >= 88:
            if section == "building_queue":
                m = re.search(r"(\d+)", t)
                return section, f"queue_{m.group(1)}" if m else "queue_1"
            return section, row_slug
    header = (header_text or "").strip().lower()
    section = ""
    for needle, slug in _HEADER_SECTION_MAP:
        if needle in header or fuzz.partial_ratio(needle, header) >= 85:
            section = slug
            break
    if not section:
        section = prev_section
    if section == "recruit_heroes":
        for known in _RECRUIT_ROW_TITLES:
            if known in t:
                return section, known
    return section or "unknown", _slugify(title)


async def _scan_panel_rows(
    image_bgr: np.ndarray, *, ocr: OcrClient, with_status: bool = True
) -> list[dict[str, Any]]:
    """OCR every fully-visible task card on the current frame."""
    h, w = image_bgr.shape[:2]
    tx0, tx1 = int(_TEXT_X0_PCT / 100 * w), int(_TEXT_X1_PCT / 100 * w)
    hx0, hx1 = int(_HEADER_X0_PCT / 100 * w), int(_HEADER_X1_PCT / 100 * w)
    rows: list[dict[str, Any]] = []
    prev_bottom = int(_PANEL_TOP_Y_PCT / 100 * h) + int(0.025 * h)
    section = ""
    for iy0, iy1 in _panel_row_anchors(image_bgr):
        cy = (iy0 + iy1) // 2
        header_text = ""
        gap_top, gap_bot = prev_bottom + 2, iy0 - 6
        prev_bottom = iy1
        if gap_bot - gap_top >= 22:
            res = await ocr.ocr_region(
                image_bgr, Region(hx0, gap_top, hx1 - hx0, gap_bot - gap_top)
            )
            header_text = _clean_ocr_line(res.text)
        title_res = await ocr.ocr_region(image_bgr, Region(tx0, cy - 26, tx1 - tx0, 26))
        title = _clean_ocr_line(title_res.text)
        if not title:
            continue
        section, row_slug = _section_for_row(title, header_text, section)

        status_text, kind, remaining = "", "unknown", 0
        if with_status:
            status_res = await ocr.ocr_region(image_bgr, Region(tx0, cy, tx1 - tx0, 26))
            status_text = _clean_ocr_line(status_res.text)
            # Timer glyphs over a saturated progress bar lose the day prefix
            # in plain OCR ("4d" → "Ad") — retry with the bar_timer pipeline.
            zone = cv2.cvtColor(
                image_bgr[cy : cy + 26, tx0:tx1], cv2.COLOR_BGR2HSV
            )
            bar_ratio = float(((zone[:, :, 1] > 120) & (zone[:, :, 2] > 120)).mean())
            if bar_ratio > 0.2:
                bar_res = await ocr.ocr_region(
                    image_bgr,
                    Region(tx0, cy, tx1 - tx0, 26),
                    preprocess="bar_timer",
                )
                bar_text = _clean_ocr_line(bar_res.text)
                if _parse_hms_to_seconds(bar_text) is not None:
                    status_text = bar_text
            kind, remaining = _classify_status(status_text)
            if kind in ("empty", "unknown"):
                colored_text = await _ocr_colored_text(
                    ocr, image_bgr, tx0, cy, tx1 - tx0, 28
                )
                if colored_text:
                    status_text = colored_text
                    kind, remaining = _classify_status(colored_text)

        button, red_dot = _row_button(image_bgr, cy)
        rows.append(
            {
                "section": section,
                "row": row_slug,
                "title": title,
                "status_text": status_text,
                "kind": kind,
                "remaining_s": remaining,
                "button": button,
                "red_dot": red_dot,
                "cy": cy,
            }
        )
    # An in-progress research row is titled by the tech name ("Tool
    # Enhancement VII"), not the slot — assign such rows the first free slot
    # in section order (center first, War Academy second).
    research_slots = ("center", "war_academy")
    research_rows = [r for r in rows if r["section"] == "tech_research"]
    taken = {r["row"] for r in research_rows if r["row"] in research_slots}
    for r in research_rows:
        if r["row"] in research_slots:
            continue
        slot = next((s for s in research_slots if s not in taken), "center")
        r["row"] = slot
        taken.add(slot)
    return rows


def _panel_state_updates(rows: list[dict[str, Any]], now: float) -> dict[str, object]:
    """Canonical player-state writes for recognized rows + the generic map."""
    updates: dict[str, object] = {}
    for r in rows:
        section, row = str(r["section"]), str(r["row"])
        kind = str(r["kind"])
        remaining = max(0, int(r["remaining_s"] or 0))
        ends_at = now + remaining if remaining > 0 else 0.0
        base = f"main_menu.panel.{section}.{row}"
        updates.update(
            {
                f"{base}.title": r["title"],
                f"{base}.status_text": r["status_text"],
                f"{base}.kind": kind,
                f"{base}.remaining_s": remaining,
                f"{base}.ends_at": ends_at,
                f"{base}.button": r["button"],
                f"{base}.has_red_dot": bool(r["red_dot"]),
                f"{base}.isClaimable": kind in ("completed", "claimable", "free"),
                f"{base}.checked_at": now,
            }
        )
        if section == "building_queue":
            m = re.search(r"(\d+)", row)
            n = m.group(1) if m else "1"
            p = f"buildings.queue.{n}.state"
            updates.update(
                {
                    f"{p}.TextStatus": r["status_text"],
                    f"{p}.isIdle": kind == "idle",
                    f"{p}.remaining_s": remaining,
                    f"{p}.ends_at": ends_at,
                    f"{p}.checked_at": now,
                }
            )
        elif section == "training" and row in _TROOP_TYPES:
            p = f"troops.{row}.state"
            updates.update(
                {
                    f"{p}.isAvailable": remaining <= 0,
                    f"{p}.isReady": kind == "completed",
                    f"{p}.TextStatus": r["status_text"] if remaining > 0 else "",
                    f"{p}.training_remaining_s": remaining,
                    f"{p}.training_ends_at": ends_at,
                    f"{p}.training_checked_at": now,
                }
            )
        elif section == "tech_research":
            p = f"research.{row}.state"
            updates.update(
                {
                    f"{p}.TextStatus": r["status_text"],
                    f"{p}.isAvailable": kind == "idle",
                    f"{p}.isLocked": kind == "locked",
                    f"{p}.current": r["title"] if kind == "in_progress" else "",
                    f"{p}.research_remaining_s": remaining,
                    f"{p}.research_ends_at": ends_at,
                    f"{p}.research_checked_at": now,
                }
            )
        elif section == "expert":
            p = f"expert.{row}.state"
            updates.update(
                {
                    f"{p}.TextStatus": r["status_text"],
                    f"{p}.isAvailable": kind == "idle",
                    f"{p}.remaining_s": remaining,
                    f"{p}.ends_at": ends_at,
                    f"{p}.checked_at": now,
                }
            )
        elif section == "alliance_contribution":
            p = "alliance.contribution.state"
            updates.update(
                {
                    f"{p}.TextStatus": r["status_text"],
                    f"{p}.isAvailable": kind == "claimable",
                    f"{p}.checked_at": now,
                }
            )
        elif section == "recruit_heroes":
            p = f"heroes.recruit.{row}.state"
            updates.update(
                {
                    f"{p}.TextStatus": r["status_text"],
                    f"{p}.isFree": kind == "free",
                    f"{p}.remaining_s": remaining,
                    f"{p}.ends_at": ends_at,
                    f"{p}.checked_at": now,
                }
            )
    return updates


async def _exec_scan_main_menu_panel(ctx: DslExecContext) -> None:
    """Scan visible City-panel rows, upsert state, push accepts for ready troops.

    Designed to run repeatedly inside a ``while_scroll`` sweep — each call
    upserts only the rows currently on screen, so a full sweep covers the
    whole panel without fixed per-section bboxes.
    """
    if ctx.redis_client is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        ctx.result.update({"reason": "empty_player_id"})
        return

    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec scan_main_menu_panel: capture failed instance=%s",
            ctx.instance_id,
        )
        ctx.result.update({"reason": "capture_failed"})
        return

    rows = await _scan_panel_rows(image, ocr=dsl_runtime.ocr_client())
    if not rows:
        ctx.result.update({"reason": "no_rows_recognized"})
        return

    now = time.time()
    updates = _panel_state_updates(rows, now)
    try:
        store = get_state_store().get_or_create(player_id)
        store.update_from_flat(updates)
    except Exception:
        logger.exception(
            "dsl exec scan_main_menu_panel: state persist failed player=%s", player_id
        )
        ctx.result.update({"reason": "state_persist_failed"})
        return

    # Per-row dispatch: each actionable row pushes its OWN scenario (see
    # _PANEL_DISPATCH). Every push self-gates on the target scenario's `enabled`
    # flag — scaffolded (enabled:false) rows are wired but stay dormant until
    # flipped on. The flag is read once per distinct scenario via a local cache.
    enabled_cache: dict[str, bool] = {}

    def _scenario_enabled(key: str) -> bool:
        if key not in enabled_cache:
            try:
                from config.paths import repo_root as _repo_root
                from dsl.dsl_schema import dsl_scenario_yaml_enabled as _yaml_enabled

                enabled_cache[key] = _yaml_enabled(_repo_root(), key) is True
            except Exception:
                enabled_cache[key] = False
        return enabled_cache[key]

    pushed: list[str] = []
    for r in rows:
        rule = _dispatch_rule_for(str(r["section"]), str(r["kind"]), str(r["row"]))
        if rule is None:
            continue
        scenario = _resolve_dispatch_scenario(rule, str(r["row"]))
        if not scenario or not _scenario_enabled(scenario):
            continue
        ok = await _enqueue_scenario(
            redis_async=ctx.redis_client,
            instance_id=ctx.instance_id,
            player_id=player_id,
            scenario=scenario,
            priority=int(rule["priority"]),
            run_at=now,
            skip_if_duplicate=True,
        )
        if ok:
            pushed.append(scenario)

    await publish_dashboard_event_throttled_async(
        ctx.redis_client,
        topic="player",
        player_id=player_id,
        reason="scan_main_menu_panel",
    )
    ctx.result.update(
        {
            "action": "stored",
            "rows": [
                {k: r[k] for k in ("section", "row", "title", "kind", "status_text")}
                for r in rows
            ],
            "pushed": pushed,
        }
    )


_TAP_ACCEPT_MAX_SWEEPS = 8
_TAP_PANEL_ROW_MAX_SWEEPS = 8


async def _scroll_find_and_tap(
    ctx: DslExecContext,
    *,
    max_sweeps: int,
    match: Callable[[dict[str, Any]], bool],
    approval_region: str,
    log_label: str,
    found_context: dict[str, Any],
) -> dict[str, Any]:
    """Reset a City scroll-panel to the top, then sweep-scroll hunting a row that
    ``match``es and tap its action button.

    Returns a result-dict mirroring the legacy handlers: a tapped/`tap_failed`
    success and `row_not_found` carry ``found_context`` (e.g. section/row or
    troop); capture / swipe-rejection failures return a bare ``reason``.
    """
    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()

    # Return to the top first — the hunt below only scrolls forward (content up),
    # so start from a known scroll origin.
    for _ in range(4):
        ok = await asyncio.to_thread(
            actions.swipe_direction,
            ctx.instance_id,
            direction="down",
            delta=500,
            duration_ms=350,
        )
        if not ok:
            return {"reason": "swipe_not_approved"}
        await asyncio.sleep(0.5)

    for sweep in range(max_sweeps):
        try:
            image = await asyncio.to_thread(
                actions.capture_screen_bgr, ctx.instance_id
            )
        except Exception:
            logger.exception(
                "dsl exec %s: capture failed instance=%s", log_label, ctx.instance_id
            )
            return {"reason": "capture_failed"}
        _h, w = image.shape[:2]
        rows = await _scan_panel_rows(image, ocr=ocr, with_status=False)
        target = next((r for r in rows if match(r)), None)
        if target is not None:
            bx = int((_BUTTON_X0_PCT + _BUTTON_X1_PCT) / 2 / 100 * w)
            tapped = bool(
                await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    Point(bx, int(target["cy"])),
                    approval_region=approval_region,
                )
            )
            return {
                "action": "tapped" if tapped else "tap_failed",
                "sweep": sweep,
                **found_context,
            }
        ok = await asyncio.to_thread(
            actions.swipe_direction,
            ctx.instance_id,
            direction="up",
            delta=400,
            duration_ms=350,
        )
        if not ok:
            return {"reason": "swipe_not_approved"}
        await asyncio.sleep(0.6)

    return {"reason": "row_not_found", **found_context}


async def _exec_tap_main_menu_panel_row(ctx: DslExecContext) -> None:
    """Scroll-find a City-panel row by section/row slug and tap its action button."""
    section = str(ctx.args.get("section") or "").strip().lower()
    row = str(ctx.args.get("row") or "").strip().lower()
    approval_region = str(ctx.args.get("approval_region") or "").strip()
    if not section or not row:
        ctx.result.update({"reason": "missing_section_or_row"})
        return
    ctx.result.update(
        await _scroll_find_and_tap(
            ctx,
            max_sweeps=_TAP_PANEL_ROW_MAX_SWEEPS,
            match=lambda r: r["section"] == section
            and r["row"] == row
            and r["button"],
            approval_region=approval_region or f"main_menu.panel.{section}.{row}",
            log_label="tap_main_menu_panel_row",
            found_context={"section": section, "row": row},
        )
    )


async def _exec_tap_training_accept(ctx: DslExecContext) -> None:
    """Scroll-find the troop's Training row and tap its green collect check.

    Args: ``troop: infantry|lancer|marksman``. The green check jumps the game
    to main_city centered on the troop's training building; the calling
    scenario handles the collection tap there.
    """
    troop = str(ctx.args.get("troop") or "").strip().lower()
    if troop not in _TROOP_TYPES:
        ctx.result.update({"reason": "bad_troop_arg", "troop": troop})
        return
    ctx.result.update(
        await _scroll_find_and_tap(
            ctx,
            max_sweeps=_TAP_ACCEPT_MAX_SWEEPS,
            match=lambda r: r["section"] == "training"
            and r["row"] == troop
            and r["button"],
            approval_region=f"main_menu.training.{troop}.accept",
            log_label="tap_training_accept",
            found_context={"troop": troop},
        )
    )


async def _exec_find_idle_training_slot(ctx: DslExecContext) -> None:
    """Pick the best idle troop camp to train → ``troops.train_next``.

    Reads ``troops.<type>.state.isAvailable`` from the player profile (set by
    ``sync_main_menu_training_status``) and, among the idle camps, asks the troop
    planner which type the army most needs (army-composition greedy; falls back
    to the meta order until troop counts are read). Writes the choice to instance
    state so the driver scenario branches and kicks its train scenario. A stale
    "busy" just misses one tick; a stale "idle" kicks a camp whose own train
    scenario re-reads the timer — both self-correct, so no double-training.
    """
    from games.wos.troops.planner import plan_training

    if ctx.redis_client is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    if not player_id:
        ctx.result.update({"reason": "empty_player_id"})
        return
    try:
        snap = get_state_store().get_or_create(player_id).snapshot()
        idle = [t for t in _TROOP_TYPES if getattr(snap.troops, t).state.isAvailable]
        counts = {
            t: int(getattr(getattr(snap.troops, t), "available", 0) or 0) for t in _TROOP_TYPES
        }
    except Exception:
        logger.exception("dsl exec find_idle_training_slot: state read failed player=%s", player_id)
        ctx.result.update({"reason": "state_read_failed"})
        return

    # Counts only steer the ranking once the (still-missing) pool reader fills
    # them; until then `any` is false → planner uses the meta order.
    pick = plan_training(idle, counts=counts if any(counts.values()) else None) or ""
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state", "troops.train_next", pick
        )
    except Exception:
        logger.debug("dsl exec find_idle_training_slot: instance write failed", exc_info=True)
    ctx.result.update({"action": "picked", "next": pick, "idle": idle})
    logger.info(
        "dsl exec find_idle_training_slot: player=%s next=%s idle=%s",
        player_id, pick, idle,
    )


DSL_EXEC_HANDLERS = {
    "sync_main_menu_training_status": _exec_sync_main_menu_training_status,
    "sync_main_menu_research_status": _exec_sync_main_menu_research_status,
    "sync_main_menu_marching_status": _exec_sync_main_menu_marching_status,
    "scan_main_menu_panel": _exec_scan_main_menu_panel,
    "tap_main_menu_panel_row": _exec_tap_main_menu_panel_row,
    "tap_training_accept": _exec_tap_training_accept,
    "find_idle_training_slot": _exec_find_idle_training_slot,
}
