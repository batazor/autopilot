"""March-slot lease bookkeeping for Intel deploys.

An Intel run sends a march that holds a slot for its whole round trip. The
resource planner creates a short *unconfirmed* reservation before pushing
``intel_run``; once the Deploy button is pressed this module stretches that
reservation to the real round-trip duration read off the deploy screen
(``outbound TTL * multiplier + slack``), so the 2..6 march-slot capacity is
respected while the march is out. Without an upstream reservation (a manual
run) it writes an equivalent confirmed one-slot lease instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from games.wos.core.resources import adapter as resource_adapter

from .state import as_float_arg, as_int_arg, decode_redis_text, read_player_state_field

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_DEFAULT_MARCH_TTL_FIELD = "intel.march_ttl"
_DEFAULT_MARCH_ROUND_TRIP_MULTIPLIER = 2.0
_DEFAULT_MARCH_EXTRA_SECONDS = 15


def parse_march_ttl_seconds(raw: Any) -> int | None:
    """Parse deploy-screen TTL text into seconds.

    Accepts the raw OCR forms used by the game (``MM:SS`` / ``HH:MM:SS``) plus
    bare integer seconds as a fallback for tests or pre-parsed state.
    """
    text = decode_redis_text(raw)
    if not text:
        return None
    groups = [int(part) for part in re.findall(r"\d+", text)]
    if not groups:
        return None
    if ":" in text:
        if len(groups) >= 3:
            h, m, s = groups[-3], groups[-2], groups[-1]
            return h * 3600 + m * 60 + s
        if len(groups) == 2:
            m, s = groups
            return m * 60 + s
        return None
    return groups[-1]


async def _write_manual_march_lease(
    ctx: DslExecContext,
    *,
    now: float,
    lease_seconds: int,
    ttl_seconds: int,
) -> str | None:
    """Fallback ledger write when the scenario was launched without a reservation."""
    if ctx.redis_client is None or not ctx.player_id:
        return None
    res_id = f"intel_run:manual:{int(now)}"
    entry = {
        "id": res_id,
        "action_id": str(ctx.args.get("resource_action_id") or "intel_run"),
        "slots": 1,
        "stamina": 0,
        "troops": dict(ctx.args.get("assign_troops") or {}),
        "heroes": list(ctx.args.get("assign_heroes") or []),
        "created_at": now,
        "confirm_by": now,
        "expires_at": now + lease_seconds,
        "lease_seconds": lease_seconds,
        "confirmed": True,
        "source": "intel.deploy",
        "ttl_seconds": ttl_seconds,
    }
    await ctx.redis_client.hset(
        f"wos:player:{ctx.player_id}:resource_reservations",
        res_id,
        json.dumps(entry),
    )
    return res_id


async def _annotate_confirmed_march_lease(
    ctx: DslExecContext,
    *,
    reservation: str,
    ends_at: float,
    lease_seconds: int,
    ttl_seconds: int,
) -> None:
    if ctx.redis_client is None or not ctx.player_id or not reservation:
        return
    key = f"wos:player:{ctx.player_id}:resource_reservations"
    raw = await ctx.redis_client.hget(key, reservation)
    if not raw:
        return
    text = decode_redis_text(raw)
    try:
        entry = json.loads(text)
    except (TypeError, ValueError):
        return
    entry.update(
        {
            "confirmed": True,
            "expires_at": ends_at,
            "lease_seconds": lease_seconds,
            "source": "intel.deploy",
            "ttl_seconds": ttl_seconds,
        }
    )
    await ctx.redis_client.hset(key, reservation, json.dumps(entry))


async def _write_march_lease_state(
    ctx: DslExecContext,
    *,
    ttl_seconds: int,
    lease_seconds: int,
    ends_at: float,
) -> None:
    if ctx.redis_client is None or not ctx.player_id:
        return
    await ctx.redis_client.hset(
        f"wos:player:{ctx.player_id}:state",
        mapping={
            "intel.march_ttl_seconds": str(ttl_seconds),
            "intel.march_lease_seconds": str(lease_seconds),
            "intel.march_ends_at": str(ends_at),
            "intel.march_lease_at": str(time.time()),
        },
    )


async def confirm_intel_march_lease(ctx: DslExecContext) -> None:
    """Confirm an intel march slot lease from the deploy-screen TTL.

    The resource planner creates a short unconfirmed reservation before pushing
    ``intel_run``. Once the Deploy button is pressed, this handler stretches that
    reservation to the real round-trip duration: outbound TTL * 2 + event slack.
    If no reservation is present (manual run), it creates an equivalent confirmed
    one-slot lease so 2..6 march-slot capacity is still respected.
    """
    ttl_field = str(ctx.args.get("ttl_field") or _DEFAULT_MARCH_TTL_FIELD).strip()
    ttl_raw = await read_player_state_field(ctx, ttl_field)
    if not ttl_raw:
        ttl_raw = await read_player_state_field(ctx, f"{ttl_field}_text")
    ttl_seconds = parse_march_ttl_seconds(ttl_raw)
    if ttl_seconds is None or ttl_seconds <= 0:
        ctx.result.update(
            {
                "action": "lease_skipped",
                "reason": "ttl_parse_failed",
                "ttl_field": ttl_field,
                "ttl_raw": ttl_raw,
            }
        )
        return

    multiplier = as_float_arg(
        ctx.args,
        "round_trip_multiplier",
        _DEFAULT_MARCH_ROUND_TRIP_MULTIPLIER,
    )
    extra_seconds = as_int_arg(
        ctx.args,
        "extra_seconds",
        _DEFAULT_MARCH_EXTRA_SECONDS,
    )
    lease_seconds = int(round(ttl_seconds * multiplier + extra_seconds))
    now = time.time()
    ends_at = now + lease_seconds

    reservation = str(ctx.args.get("resource_reservation") or "").strip()
    confirmed = False
    if reservation and ctx.redis_client is not None and ctx.player_id:
        try:
            confirmed = await resource_adapter.confirm_reservation(
                ctx.redis_client,
                ctx.player_id,
                reservation,
                ends_at=ends_at,
            )
        except Exception:
            logger.debug(
                "intel: resource reservation confirm failed player=%s reservation=%s",
                ctx.player_id,
                reservation,
                exc_info=True,
            )
            confirmed = False

    if confirmed:
        try:
            await _annotate_confirmed_march_lease(
                ctx,
                reservation=reservation,
                ends_at=ends_at,
                lease_seconds=lease_seconds,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            logger.debug(
                "intel: resource reservation annotate failed player=%s reservation=%s",
                ctx.player_id,
                reservation,
                exc_info=True,
            )

    fallback_reservation = ""
    if not confirmed:
        try:
            fallback_reservation = (
                await _write_manual_march_lease(
                    ctx,
                    now=now,
                    lease_seconds=lease_seconds,
                    ttl_seconds=ttl_seconds,
                )
                or ""
            )
        except Exception:
            logger.debug(
                "intel: fallback march lease write failed player=%s",
                ctx.player_id,
                exc_info=True,
            )

    try:
        await _write_march_lease_state(
            ctx,
            ttl_seconds=ttl_seconds,
            lease_seconds=lease_seconds,
            ends_at=ends_at,
        )
    except Exception:
        logger.debug(
            "intel: march lease state write failed player=%s",
            ctx.player_id,
            exc_info=True,
        )

    ctx.result.update(
        {
            "action": "lease_confirmed" if confirmed else "lease_recorded",
            "reservation": reservation if confirmed else fallback_reservation,
            "ttl_field": ttl_field,
            "ttl_raw": ttl_raw,
            "ttl_seconds": ttl_seconds,
            "lease_seconds": lease_seconds,
            "ends_at": ends_at,
        }
    )


# Back-compat alias for the previous private handler name.
_exec_confirm_intel_march_lease = confirm_intel_march_lease
