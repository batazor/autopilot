"""The goto_calendar edge resolver packages the structured tap spec, and the
navigator dispatches that spec to the goto_calendar tap handler."""
from __future__ import annotations

from navigation.calendar_open_resolver import resolve_goto_calendar
from navigation.screen_graph import EDGE_RESOLVERS


async def test_resolver_packages_goto_calendar_spec():
    out = await resolve_goto_calendar({}, "inst", None)
    assert out == [{"type": "goto_calendar"}]


async def test_resolver_ignores_extra_spec_fields():
    # Calendar is always the leftmost tab — the resolver needs no args.
    out = await resolve_goto_calendar({"event": "anything"}, "inst", None)
    assert out == [{"type": "goto_calendar"}]


def test_resolver_registered():
    assert EDGE_RESOLVERS.get("goto_calendar") is resolve_goto_calendar


async def test_dispatch_routes_goto_calendar_to_handler(monkeypatch):
    """A goto_calendar tap spec in a hop calls _tap_goto_calendar_async."""
    from navigation.navigator import Navigator

    nav = Navigator.__new__(Navigator)

    calls: list[dict] = []

    async def fake_goto(instance_id, spec, **kwargs):
        calls.append(spec)
        return True

    async def fail(*a, **k):  # any other tap path is a routing bug
        msg = "goto_calendar spec routed to the wrong handler"
        raise AssertionError(msg)

    monkeypatch.setattr(nav, "_tap_goto_calendar_async", fake_goto, raising=False)
    monkeypatch.setattr(nav, "_tap_region_name_async", fail, raising=False)
    monkeypatch.setattr(nav, "_active_player_state_flat", _async_none, raising=False)
    monkeypatch.setattr(nav, "_set_nav_expected_screen", _async_noop, raising=False)
    monkeypatch.setattr(nav, "_clear_nav_expected_screen", _async_noop, raising=False)
    monkeypatch.setattr(nav, "_wait_for_screen_verified", _async_true, raising=False)
    monkeypatch.setattr(nav, "_write_screen", _async_noop, raising=False)

    result = await nav._execute_hops(
        "inst",
        [("event.calendar", [{"type": "goto_calendar"}])],
        from_screen="main_city",
    )
    assert result == "ok"
    assert calls == [{"type": "goto_calendar"}]


async def _async_none(*a, **k):
    return None


async def _async_noop(*a, **k):
    return None


async def _async_true(*a, **k):
    return True
