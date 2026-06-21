"""The calendar_go edge resolver packages a structured tap spec for the navigator."""
from __future__ import annotations

from navigation.calendar_go_resolver import resolve_calendar_go


async def test_resolver_packages_event_spec():
    out = await resolve_calendar_go({"event": "foundry_battle"}, "inst", None)
    assert out == [{"type": "calendar_go", "event": "foundry_battle"}]


async def test_resolver_passes_aliases_and_threshold():
    out = await resolve_calendar_go(
        {"aliases": ["Foundry Battle"], "threshold": 0.8}, "inst", None
    )
    assert out == [{"type": "calendar_go", "aliases": ["Foundry Battle"], "threshold": 0.8}]


async def test_resolver_none_without_target():
    assert await resolve_calendar_go({}, "inst", None) is None
