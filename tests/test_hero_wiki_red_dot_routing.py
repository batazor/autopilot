"""End-to-end coverage for the per-hero wiki red-dot pipeline.

Layers exercised here:

* ``screen_graph._load_edge_taps`` synthesises a wiki edge pair (forward via
  the wiki icon, back via the back button) for every hero in
  ``db/heroes/index.yaml``.
* ``screen_graph.load_screen_verify_config`` fills in a ``from_screen`` rule
  for every ``heroes.<hero>.wiki`` destination — the navigator can verify the
  popup by checking the prior hop in Redis instead of OCR'ing the screen.
* ``InstanceWorkerOverlayMixin._enqueue_push_scenarios_from_overlay``
  substitutes ``${hero_id}`` in pushScenario names from ``current_screen``
  and drops the push entirely when the screen isn't a ``page.heroes.<id>``
  page.

The Navigator history side (LPUSH / LTRIM into ``screen_history``) and the
history-based verify rule are exercised in
``test_navigator_screen_history.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from navigation.screen_graph import (
    EDGE_TAPS,
    load_screen_verify_config,
    route_taps,
    screen_verify_rules,
)
from worker.instance_worker_overlay import (
    _PAGE_HEROES_SCREEN_RE,
    InstanceWorkerOverlayMixin,
)


def test_wiki_forward_edge_exists_for_known_hero() -> None:
    assert ("page.heroes.ahmose", "heroes.ahmose.wiki") in EDGE_TAPS
    assert EDGE_TAPS[("page.heroes.ahmose", "heroes.ahmose.wiki")] == [
        "page.heroes.unit.wiki"
    ]


def test_wiki_back_edge_exists_for_known_hero() -> None:
    assert ("heroes.ahmose.wiki", "page.heroes.ahmose") in EDGE_TAPS
    assert EDGE_TAPS[("heroes.ahmose.wiki", "page.heroes.ahmose")] == ["back_button"]


def test_wiki_back_edge_routes_via_static_topology() -> None:
    # ``page.heroes.ahmose`` is reached via a ``hero_grid`` dynamic edge, so
    # forward routing requires the async resolver — but the reverse hop is
    # static and exercisable in isolation.
    assert route_taps("heroes.ahmose.wiki", "page.heroes.ahmose") == [["back_button"]]


def test_wiki_screen_verify_uses_from_screen() -> None:
    rules = screen_verify_rules("heroes.ahmose.wiki")
    assert rules, "expected per-hero wiki screen to be synthesised"
    assert any(
        isinstance(r, dict)
        and r.get("from_screen") == ["page.heroes.ahmose"]
        for r in rules
    )


def test_wiki_screen_verify_synthesised_for_every_hero() -> None:
    # Sanity bound: the index has ≥10 heroes; every one must contribute a
    # synth entry. Catches a regression where ``_hero_ids`` returns ``[]``
    # silently (e.g. the YAML moves and the path is no longer found).
    screens = load_screen_verify_config().get("screens") or {}
    wiki_screens = [s for s in screens if isinstance(s, str) and s.endswith(".wiki")]
    assert len(wiki_screens) >= 10, wiki_screens
    for name in wiki_screens:
        assert name.startswith("heroes.")
        assert any(
            isinstance(r, dict) and "from_screen" in r
            for r in screen_verify_rules(name)
        ), name


def test_page_heroes_screen_regex_extracts_hero_id() -> None:
    m = _PAGE_HEROES_SCREEN_RE.match("page.heroes.lumak_bokan")
    assert m is not None and m.group("hero") == "lumak_bokan"
    assert _PAGE_HEROES_SCREEN_RE.match("page.heroes") is None
    assert _PAGE_HEROES_SCREEN_RE.match("heroes") is None
    # ``page.heroes.unit`` is also regex-matchable (``unit`` looks like a
    # hero id) — the non-hero filter in ``_resolve_hero_id_from_screen``
    # is what keeps it out, not the regex itself.
    m_unit = _PAGE_HEROES_SCREEN_RE.match("page.heroes.unit")
    assert m_unit is not None and m_unit.group("hero") == "unit"


class _FakeRedis:
    """Minimal stand-in returning a pre-seeded ``current_screen``.

    Implements just enough ``hget`` semantics for ``_resolve_hero_id_from_screen``.
    """

    def __init__(self, current_screen: str = "") -> None:
        self.current_screen = current_screen

    async def hget(self, _key: str, field: str) -> bytes | None:
        if field != "current_screen":
            return None
        return self.current_screen.encode() if self.current_screen else None


def _make_mixin(redis: Any) -> InstanceWorkerOverlayMixin:
    """Build a bare mixin instance bound to a fake Redis + cfg.

    The mixin's protocol fields (``_cfg``, ``_redis``, ``_queue``) are set on a
    fresh instance — we don't instantiate the full ``InstanceWorker`` because
    the unit under test only touches ``_resolve_hero_id_from_screen``.
    """
    mixin = InstanceWorkerOverlayMixin.__new__(InstanceWorkerOverlayMixin)
    mixin._redis = redis
    mixin._cfg = type("Cfg", (), {"instance_id": "bs1"})()
    mixin._queue = None
    return mixin


@pytest.mark.asyncio
async def test_resolve_hero_id_from_per_hero_screen() -> None:
    mixin = _make_mixin(_FakeRedis("page.heroes.sergey"))
    assert await mixin._resolve_hero_id_from_screen() == "sergey"


@pytest.mark.asyncio
async def test_resolve_hero_id_empty_on_unrelated_screen() -> None:
    mixin = _make_mixin(_FakeRedis("main_city"))
    assert await mixin._resolve_hero_id_from_screen() == ""


@pytest.mark.asyncio
async def test_resolve_hero_id_empty_when_screen_unset() -> None:
    mixin = _make_mixin(_FakeRedis(""))
    assert await mixin._resolve_hero_id_from_screen() == ""


@pytest.mark.asyncio
async def test_resolve_hero_id_filters_generic_unit_subname() -> None:
    """``page.heroes.unit`` is the FSM detail-page node, not a real hero —
    treating ``unit`` as a hero id would push ``heroes.unit.wiki`` which the
    template resolver can't render."""
    mixin = _make_mixin(_FakeRedis("page.heroes.unit"))
    assert await mixin._resolve_hero_id_from_screen() == ""
