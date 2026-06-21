"""Dynamic edge resolver: reach an event via the calendar's Go button.

Calendar event popups each have a Go button that jumps to the event screen, so
``event.calendar`` can route to any event — including ones with no main_city
floating icon. The edge declares which event to find::

    edges:
      event.calendar:
        event.foundry_battle:
          resolver: calendar_go
          event: foundry_battle          # catalog id → aliases (title + id)
          # or: aliases: ["Foundry Battle"]

The resolver just packages the spec; the interactive walk (tap each bar, OCR the
popup name, tap Go on a match) runs in ``Navigator._tap_calendar_go_async``,
which has the screen + OCR access — mirroring how ``tab_index`` works.
"""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_calendar_go(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    aliases = spec.get("aliases")
    event = str(spec.get("event") or "").strip()
    if not event and not aliases:
        return None
    tap_spec: dict[str, Any] = {"type": "calendar_go"}
    if event:
        tap_spec["event"] = event
    if aliases:
        tap_spec["aliases"] = aliases
    if "threshold" in spec:
        tap_spec["threshold"] = spec["threshold"]
    return [tap_spec]


register_edge_resolver("calendar_go", resolve_calendar_go)
