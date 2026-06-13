"""DSL exec handler: compute the event-calendar look-ahead and publish it.

``read_calendar`` derives "what's on today and the next few days" from the
declarative catalog (events.yaml) and writes the digest + per-event active flags
into player state — no screen capture needed. Pulling limited-time events off
the on-screen calendar via OCR is a separate, later handler; until those regions
are labeled this catalog-driven view is the calendar the strategy layer reads.

Args (sibling keys on the ``exec:`` step):
  days   look-ahead horizon in server-days (default 3 = today + next two).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from games.wos.core.calendar.adapter import DEFAULT_DAYS, load_catalog, publish

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)


def _as_days(value: object) -> int:
    try:
        days = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return days if days > 0 else DEFAULT_DAYS


async def _exec_read_calendar(ctx: DslExecContext) -> None:
    days = _as_days((ctx.args or {}).get("days"))
    calendar = load_catalog()
    if not calendar.enabled:
        ctx.result.update({"action": "disabled"})
        return
    if ctx.redis_client is None or not ctx.player_id:
        ctx.result.update({"action": "no_target"})
        return

    now = time.time()
    view = await publish(ctx.redis_client, ctx.player_id, calendar, now, days=days)
    active_ids = [e["id"] for e in view["active"]]
    ctx.result.update(
        {
            "action": "published",
            "days": days,
            "active": active_ids,
            "upcoming": [e["id"] for e in view["upcoming"]],
        }
    )
    logger.info(
        "calendar: player=%s active=%s upcoming=%d",
        ctx.player_id,
        ",".join(active_ids) or "-",
        len(view["upcoming"]),
    )


DSL_EXEC_HANDLERS = {
    "read_calendar": _exec_read_calendar,
}
