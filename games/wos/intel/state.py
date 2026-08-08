"""Redis / DSL-arg glue shared by the Intel exec handlers.

The Intel handlers all read the same flat player state (``stamina``, the Crazy
Joe reserve flag, deploy-screen TTL) and parse the same kinds of step args.
Keeping that small I/O + coercion layer here lets :mod:`detection` stay a pure
vision module and :mod:`march_lease` / :mod:`exec` stay thin.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Plausible upper bound for the stamina pool. References + live boards show 70-90;
# scrcpy H.264 noise mangles the small denominator ("70"→"710"/"10"), so any max
# above this (or below the current value) is an OCR artefact, not a real cap.
STAMINA_MAX_CAP = 300

_STAMINA_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_STAMINA_SINGLE_RE = re.compile(r"\d+")
# The intel board's own stamina counter (top-right «200», a spendable currency
# with a "+" to buy more) has no visible max, and can far exceed the old «X/90»
# bar's range, so a plain-number read gets a much wider plausibility bound.
STAMINA_SINGLE_CAP = 5000


def parse_stamina(text: Any, *, cap: int = STAMINA_MAX_CAP) -> tuple[int, int | None] | None:
    """Parse the intel-board stamina OCR into ``(current, max_or_None)``.

    Two on-screen shapes:

    * ``«current/max»`` (legacy «43/70» bar): scrcpy H.264 frequently corrupts
      the small denominator, so the reliable numerator drives the result —
      ``(current, max)`` when both are plausible, ``(current, None)`` when the
      max is an artefact (``max < current`` or ``max > cap``).
    * A plain integer («200», the top-right board stamina counter, no visible
      max): returned as ``(value, None)`` when ``0 < value <= STAMINA_SINGLE_CAP``.

    ``None`` — nothing plausible parsed; the caller retries / reports parse_failed.
    """
    if text is None:
        return None
    match = _STAMINA_RE.search(str(text))
    if match:
        current, maximum = int(match.group(1)), int(match.group(2))
        if not (0 < current <= cap):
            return None
        if current <= maximum <= cap:
            return (current, maximum)
        return (current, None)
    # Plain single number (top-right board stamina «200» — no «/max»).
    single = _STAMINA_SINGLE_RE.search(str(text))
    if single:
        value = int(single.group(0))
        if 0 < value <= STAMINA_SINGLE_CAP:
            return (value, None)
    return None


# --- DSL step-arg coercion ---------------------------------------------------


def as_int_arg(args: dict[str, Any], key: str, default: int) -> int:
    """Positive int step arg, or ``default`` when missing / non-positive."""
    try:
        value = int(args.get(key))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def as_float_arg(args: dict[str, Any], key: str, default: float) -> float:
    """Positive float step arg, or ``default`` when missing / non-positive."""
    try:
        value = float(args.get(key))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def as_bool_arg(args: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = args.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def as_quota_arg(args: dict[str, Any], key: str) -> int | None:
    """Daily-quota-left arg: a non-negative int, or ``None`` (unlimited/unknown)."""
    value = args.get(key)
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


# --- Redis text + player-state reads -----------------------------------------


def decode_redis_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


async def read_player_stamina(ctx: DslExecContext) -> float | None:
    """Latest stamina estimate for this player (written by ``read_intel_stamina``)."""
    if ctx.redis_client is None or not ctx.player_id:
        return None
    try:
        raw = await ctx.redis_client.hget(
            f"wos:player:{ctx.player_id}:state", "stamina"
        )
    except Exception:
        logger.debug(
            "intel: stamina read failed player=%s", ctx.player_id, exc_info=True
        )
        return None
    if raw is None:
        return None
    text = decode_redis_text(raw)
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


async def read_player_state_field(ctx: DslExecContext, field: str) -> str:
    if ctx.redis_client is None or not ctx.player_id or not field:
        return ""
    try:
        raw = await ctx.redis_client.hget(f"wos:player:{ctx.player_id}:state", field)
    except Exception:
        logger.debug(
            "intel: player state read failed player=%s field=%s",
            ctx.player_id,
            field,
            exc_info=True,
        )
        return ""
    return decode_redis_text(raw)


async def intel_reserve(ctx: DslExecContext) -> int:
    """Stamina to hold back for higher-priority *active* events (e.g. Crazy Joe).

    Reads the player's flat state and reuses the stamina budget's reserve rule
    (:func:`stamina.model.reserve_for`): higher-priority demands hold their
    ``reserve_floor`` while their ``active_when`` flag is set. Those flags are fed
    by the calendar fan-out (``joe_event_active`` = Crazy Joe live-or-imminent), so
    the intel reserve tracks the event window with no hardcoded schedule.
    """
    if ctx.redis_client is None or not ctx.player_id:
        return 0
    try:
        raw = await ctx.redis_client.hgetall(f"wos:player:{ctx.player_id}:state")
    except Exception:
        logger.debug(
            "intel: reserve state read failed player=%s", ctx.player_id, exc_info=True
        )
        return 0
    state = {decode_redis_text(k): decode_redis_text(v) for k, v in (raw or {}).items()}
    try:
        from games.wos.core.stamina.adapter import load_budget
        from games.wos.core.stamina.model import reserve_for

        return reserve_for(load_budget(), "intel_events", state)
    except Exception:
        logger.debug("intel: reserve derivation failed", exc_info=True)
        return 0
