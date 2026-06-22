"""Dynamic edge resolver: open the Events panel on the Calendar tab.

Calendar is always the FIRST (leftmost) tab of the swipe-only Events carousel, but
the panel opens on whatever event is primary, so ``calendar.tab`` is usually
scrolled off-screen and the node graph can't reach it. This resolver packages a
``goto_calendar`` tap spec; the interactive walk (open panel, swipe to the leftmost
page, tap the Calendar tab) runs in ``Navigator._tap_goto_calendar_async``, which
has the screen + OCR access — mirroring how ``calendar_go`` works.

    edges:
      main_city:
        event.calendar: { resolver: goto_calendar }
"""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_goto_calendar(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    _ = (spec, instance_id, redis_client)
    return [{"type": "goto_calendar"}]


register_edge_resolver("goto_calendar", resolve_goto_calendar)
