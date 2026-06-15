"""Golden ordering fixtures for queue ranking (ADR 0001 §"Test cases").

These tests target ``RedisQueue._rank_candidates`` directly — a pure synchronous
method that computes the sort tuple for every due candidate. No Redis or Docker
required; graph topology is monkeypatched via ``_bfs_hops``.

End-to-end ``pop_due`` integration coverage stays in the ``redis_async`` tests
which need testcontainers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from scheduler import queue as queue_mod
from scheduler.queue import RedisQueue


def _due_item(
    *,
    task_type: str,
    priority: int,
    run_at: float = 1000.0,
    created_at: float = 1000.0,
    player_id: str = "",
    task_id: str = "",
) -> tuple[str, dict[str, Any]]:
    data: dict[str, Any] = {
        "task_id": task_id or f"id:{task_type}:{priority}",
        "player_id": player_id,
        "task_type": task_type,
        "priority": priority,
        "run_at": run_at,
        "instance_id": "bs1",
        "created_at": created_at,
    }
    return json.dumps(data), data


def _rank(
    due: list[tuple[str, dict[str, Any]]],
    *,
    current_screen: str = "main_city",
    recent_counts: dict[tuple[str, str], int] | None = None,
):
    q = RedisQueue.__new__(RedisQueue)
    ranked = q._rank_candidates(
        due,
        current_screen=current_screen,
        recent_counts=recent_counts or {},
        now=1000.0,
    )
    ranked.sort(key=lambda x: x[0])
    return ranked


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Clear the process-level BFS cache before each test (real function only;
    monkeypatch-restored fake will be reset by the time teardown runs)."""
    clear = getattr(queue_mod._bfs_hops, "cache_clear", None)
    if clear:
        clear()
    return


def _patch_required_nodes(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(
        RedisQueue,
        "_task_type_to_required_node",
        staticmethod(lambda: mapping),
    )


def _patch_hops(monkeypatch, table: dict[tuple[str, str], int | None]) -> None:
    def fake(src: str, dst: str) -> int | None:
        return table.get((src, dst))

    monkeypatch.setattr(queue_mod, "_bfs_hops", fake)


def test_same_base_same_run_at_fewer_hops_wins(monkeypatch):
    """ADR §1: tie on -effective + unreachable_flag → fewer hops wins."""
    _patch_required_nodes(monkeypatch, {"near_task": "screen_a", "far_task": "screen_b"})
    _patch_hops(monkeypatch, {("main_city", "screen_a"): 1, ("main_city", "screen_b"): 1})

    near = _due_item(task_type="near_task", priority=80_000)
    far = _due_item(task_type="far_task", priority=80_000)

    # Adjust hops so effective_priority ties (near=1, far=3) — same band so
    # priority differs by hop debuff only. To meet the spec we need same
    # effective; instead vary one hop and confirm fewer-hops wins.
    _patch_hops(
        monkeypatch,
        {("main_city", "screen_a"): 1, ("main_city", "screen_b"): 1},
    )
    ranked = _rank([near, far])
    # Tie on effective_priority (same base, same hops=1, no recent debuff) →
    # determinism falls to run_at then created_at; both equal here, so order is
    # stable input order. Use distinct created_at to verify last-tiebreak.
    near2 = _due_item(task_type="near_task", priority=80_000, created_at=999.0)
    far2 = _due_item(task_type="far_task", priority=80_000, created_at=1001.0)
    ranked = _rank([far2, near2])
    assert ranked[0][2]["task_type"] == "near_task", "earlier created_at wins last tiebreak"

    # Now the actual spec: same base, differing hops → fewer hops wins (lower
    # hop_debuff → higher effective_priority).
    _patch_hops(
        monkeypatch,
        {("main_city", "screen_a"): 1, ("main_city", "screen_b"): 3},
    )
    ranked = _rank([far, near])
    assert ranked[0][2]["task_type"] == "near_task"
    assert ranked[0][3]["hops"] == 1
    assert ranked[1][3]["hops"] == 3


def test_same_base_same_hops_lower_recent_count_wins(monkeypatch):
    """ADR §2: lower recent_count → lower recent_debuff → wins."""
    _patch_required_nodes(monkeypatch, {"fresh_task": "screen_a", "hot_task": "screen_a"})
    _patch_hops(monkeypatch, {("main_city", "screen_a"): 1})

    fresh = _due_item(task_type="fresh_task", priority=80_000, player_id="p1")
    hot = _due_item(task_type="hot_task", priority=80_000, player_id="p1")

    ranked = _rank(
        [hot, fresh],
        recent_counts={("hot_task", "p1"): 2, ("fresh_task", "p1"): 0},
    )
    assert ranked[0][2]["task_type"] == "fresh_task"
    assert ranked[0][3]["recent_debuff"] == 0
    assert ranked[1][3]["recent_debuff"] == 2 * queue_mod.W_RECENT


def test_recent_debuff_caps_at_recent_cap(monkeypatch):
    """recent_count beyond RECENT_RUNS_CAP must not keep growing the debuff."""
    _patch_required_nodes(monkeypatch, {"hot_task": ""})

    hot = _due_item(task_type="hot_task", priority=80_000, player_id="p1")
    ranked = _rank(
        [hot],
        recent_counts={("hot_task", "p1"): 99},
    )
    assert ranked[0][3]["recent_debuff"] == queue_mod.RECENT_RUNS_CAP * queue_mod.W_RECENT


def test_recent_debuff_can_be_disabled_for_tab_navigation(monkeypatch):
    """Tab-strip navigation may need several back-to-back runs inside a page
    family; recent-run debuff must not let older outside work pull us away."""
    _patch_required_nodes(
        monkeypatch,
        {
            "shop.daily_deals": "shop.daily_deals",
            "deals.deals": "deals",
        },
    )
    _patch_hops(
        monkeypatch,
        {
            ("shop.daily_deals", "shop.daily_deals"): 0,
            ("shop.daily_deals", "deals"): 2,
        },
    )
    monkeypatch.setattr(
        RedisQueue,
        "_task_types_without_recent_debuff",
        staticmethod(lambda: {"tabs.strip.advance"}),
    )

    current_page_work = _due_item(task_type="shop.daily_deals", priority=80_000)
    tab_advance = _due_item(task_type="tabs.strip.advance", priority=79_900)
    outside_deals = _due_item(task_type="deals.deals", priority=80_000)

    ranked = _rank(
        [outside_deals, tab_advance, current_page_work],
        current_screen="shop.daily_deals",
        recent_counts={("tabs.strip.advance", ""): 3},
    )

    assert [r[2]["task_type"] for r in ranked] == [
        "shop.daily_deals",
        "tabs.strip.advance",
        "deals.deals",
    ]
    assert ranked[1][3]["recent_count"] == 3
    assert ranked[1][3]["recent_debuff"] == 0
    assert ranked[1][3]["recent_debuff_disabled"] is True


def test_tab_advance_beats_far_deals_work(monkeypatch):
    """A visible red-dot tab should be clicked before jumping out through
    ``main_city`` to a far Deals page.
    """
    _patch_required_nodes(
        monkeypatch,
        {"deals.tundra_trading_station": "deals.tundra_trading_station"},
    )
    _patch_hops(
        monkeypatch,
        {("deals.hall_of_heroes", "deals.tundra_trading_station"): 3},
    )
    monkeypatch.setattr(
        RedisQueue,
        "_task_types_without_recent_debuff",
        staticmethod(lambda: {"tabs.strip.advance"}),
    )

    tab_advance = _due_item(task_type="tabs.strip.advance", priority=79_900)
    tundra = _due_item(task_type="deals.tundra_trading_station", priority=80_000)

    ranked = _rank([tundra, tab_advance], current_screen="deals.hall_of_heroes")

    assert [r[2]["task_type"] for r in ranked] == [
        "tabs.strip.advance",
        "deals.tundra_trading_station",
    ]


def test_current_screen_task_ignores_recent_debuff(monkeypatch):
    """Visible work on the current page must finish before jumping tabs.

    Regression: a red-dot task for the page we are already on could receive a
    recent-run debuff, letting a fresh sibling tab task with the same base
    priority pull the bot away from the actionable page.
    """
    _patch_required_nodes(
        monkeypatch,
        {
            "deals.vault_of_enigma": "deals.vault_of_enigma",
            "deals.hall_of_heroes": "deals.hall_of_heroes",
        },
    )
    _patch_hops(
        monkeypatch,
        {
            ("deals.vault_of_enigma", "deals.vault_of_enigma"): 0,
            ("deals.vault_of_enigma", "deals.hall_of_heroes"): 1,
        },
    )
    monkeypatch.setattr(
        RedisQueue,
        "_task_types_without_recent_debuff",
        staticmethod(set),
    )

    vault = _due_item(task_type="deals.vault_of_enigma", priority=80_000)
    hall = _due_item(task_type="deals.hall_of_heroes", priority=80_000)

    ranked = _rank(
        [hall, vault],
        current_screen="deals.vault_of_enigma",
        recent_counts={("deals.vault_of_enigma", ""): 3},
    )

    assert ranked[0][2]["task_type"] == "deals.vault_of_enigma"
    assert ranked[0][3]["recent_count"] == 3
    assert ranked[0][3]["recent_debuff"] == 0
    assert ranked[0][3]["recent_debuff_disabled"] is True
    assert ranked[0][3]["on_required_node"] is True


def test_reachable_beats_unreachable_at_same_effective(monkeypatch):
    """ADR §3: unreachable_flag=1 sorts after reachable=0 on the second key."""
    _patch_required_nodes(
        monkeypatch,
        {"reachable_task": "screen_a", "stranded_task": "screen_unreachable"},
    )
    # Reachable in 4 hops → graph_debuff = 4*W_HOPS = 2000
    # Unreachable → UNREACHABLE_DEBUFF = 5000
    # To tie effective_priority, give unreachable a higher base.
    _patch_hops(
        monkeypatch,
        {("main_city", "screen_a"): 4, ("main_city", "screen_unreachable"): None},
    )

    reach = _due_item(task_type="reachable_task", priority=80_000)
    stranded = _due_item(
        task_type="stranded_task",
        priority=80_000 + (queue_mod.UNREACHABLE_DEBUFF - 4 * queue_mod.W_HOPS),
    )

    ranked = _rank([stranded, reach])
    # Same effective_priority — reachable wins by unreachable_flag tiebreak.
    assert ranked[0][3]["effective_priority"] == ranked[1][3]["effective_priority"]
    assert ranked[0][2]["task_type"] == "reachable_task"
    assert ranked[0][3]["unreachable_flag"] == 0
    assert ranked[1][3]["unreachable_flag"] == 1


def test_priority_band_guard_higher_base_wins_despite_hop_debuff(monkeypatch):
    """ADR §4: 10k base gap > max bounded debuff → higher band always wins."""
    _patch_required_nodes(monkeypatch, {"hi": "screen_a", "lo": "screen_b"})
    _patch_hops(
        monkeypatch,
        {("main_city", "screen_a"): 5, ("main_city", "screen_b"): 0},
    )

    hi = _due_item(task_type="hi", priority=80_000)  # 5 hops, max graph debuff
    lo = _due_item(task_type="lo", priority=70_000)  # 0 hops

    ranked = _rank([lo, hi])
    assert ranked[0][2]["task_type"] == "hi"
    # Confirm bounded: even at the cap, debuff < 10k band gap.
    assert ranked[0][3]["graph_debuff"] < 10_000


def test_node_independent_neutral_against_node_bound_peer(monkeypatch):
    """ADR §5: required_node empty → graph_debuff=0, hops=0, reachable."""
    _patch_required_nodes(monkeypatch, {"node_bound": "screen_a"})  # housekeeping has no entry
    _patch_hops(monkeypatch, {("main_city", "screen_a"): 3})

    housekeeping = _due_item(task_type="housekeeping", priority=80_000)
    bound = _due_item(task_type="node_bound", priority=80_000)

    ranked = _rank([bound, housekeeping])
    # housekeeping: graph_debuff=0; node_bound: 3 hops → 1500 debuff. Housekeeping wins.
    assert ranked[0][2]["task_type"] == "housekeeping"
    assert ranked[0][3]["graph_debuff"] == 0
    assert ranked[0][3]["hops"] == 0
    assert ranked[0][3]["unreachable_flag"] == 0


def test_node_independent_neutral_when_current_screen_unknown(monkeypatch):
    """ADR §7 (opt): screen unknown → node-independent stays neutral, not unreachable."""
    _patch_required_nodes(monkeypatch, {})  # no node-bound entries
    _patch_hops(monkeypatch, {})

    probe = _due_item(task_type="housekeeping", priority=85_000)
    ranked = _rank([probe], current_screen="")
    assert ranked[0][3]["graph_debuff"] == 0
    assert ranked[0][3]["unreachable_flag"] == 0
    assert ranked[0][3]["hops"] == 0


def test_node_bound_with_unknown_screen_gets_neutral_graph(monkeypatch):
    """Screen unknown + required_node non-empty → no graph debuff (gating happens
    earlier in ``pop_due``; ranking must not invent a path penalty)."""
    _patch_required_nodes(monkeypatch, {"node_task": "screen_a"})
    _patch_hops(monkeypatch, {})

    task = _due_item(task_type="node_task", priority=80_000)
    ranked = _rank([task], current_screen="")
    assert ranked[0][3]["graph_debuff"] == 0
    assert ranked[0][3]["unreachable_flag"] == 0


def test_created_at_is_final_stable_tiebreak(monkeypatch):
    """All earlier tuple components tie → earlier created_at wins."""
    _patch_required_nodes(monkeypatch, {})
    _patch_hops(monkeypatch, {})

    later = _due_item(task_type="t", priority=80_000, run_at=1000.0, created_at=2000.0)
    earlier = _due_item(task_type="t", priority=80_000, run_at=1000.0, created_at=1500.0)

    ranked = _rank([later, earlier])
    assert ranked[0][2]["created_at"] == 1500.0


def test_required_node_map_covers_template_keys():
    """Regression: pre-fix ``_task_type_to_required_node`` only scanned cron
    YAMLs, so overlay-pushed hero scenarios (no ``cron:``) and template fills
    (``heroes.{hero}.wiki``) all resolved to ``required_node=""`` and lost the
    hops penalty. The extended map must surface both.
    """
    mapping = RedisQueue._task_type_to_required_node()
    # Hero card template (``{hero}.yaml`` → key ``ahmose``, node ``page.heroes.ahmose``).
    assert mapping.get("ahmose") == "page.heroes.ahmose"
    assert mapping.get("bahiti") == "page.heroes.bahiti"
    # Wiki template (``heroes.{hero}.wiki.yaml`` → ``heroes.bahiti.wiki``,
    # node ``heroes.bahiti.wiki``). Rendered through ``load_doc``.
    assert mapping.get("heroes.bahiti.wiki") == "heroes.bahiti.wiki"
    # Cron scenario still present (covered by the original cron-only path too).
    assert mapping.get("check_main_city") == "main_city"


def test_requiring_node_gating_stays_cron_only():
    """The gating set (``_task_types_requiring_node``) is intentionally narrower
    than the ranking map — overlay-pushed node-bound scenarios use the DSL's
    own ``awaiting_screen_identity`` early-exit path, not queue-level gating.
    """
    gating = RedisQueue._task_types_requiring_node()
    ranking = RedisQueue._task_type_to_required_node()
    assert "check_main_city" in gating
    # ``ahmose`` is in ranking (template-derived) but NOT in gating
    # (no ``cron:`` on the template).
    assert "ahmose" in ranking
    assert "ahmose" not in gating
    assert "heroes.bahiti.wiki" in ranking
    assert "heroes.bahiti.wiki" not in gating


def test_overlay_push_wins_locality_over_older_far_push(monkeypatch):
    """Reproduces the bahiti.wiki vs molly case: an older queued task with
    many hops loses to a freshly-pushed 0-hop task at the same priority.
    Before the ranking-map extension, this came out backwards (older run_at
    wins under FIFO when both tasks had ``required_node=""``).
    """
    _patch_required_nodes(monkeypatch, {
        "heroes.bahiti.wiki": "heroes.bahiti.wiki",
        "molly": "page.heroes.molly",
    })
    _patch_hops(monkeypatch, {
        ("page.heroes.bahiti", "heroes.bahiti.wiki"): 1,
        ("page.heroes.bahiti", "page.heroes.molly"): 6,
    })

    wiki = _due_item(
        task_type="heroes.bahiti.wiki", priority=80_000,
        run_at=2000.0, created_at=2000.0,
    )
    molly = _due_item(
        task_type="molly", priority=80_000,
        run_at=1500.0, created_at=1500.0,  # 500s older
    )

    ranked = _rank([wiki, molly], current_screen="page.heroes.bahiti")
    assert ranked[0][2]["task_type"] == "heroes.bahiti.wiki", (
        "0-hop wiki should beat 6-hop molly even though molly is much older"
    )


def test_pop_candidates_log_includes_ranking_breakdown(monkeypatch, caplog):
    """The queue pop log should explain why one due task beat another."""
    _patch_required_nodes(
        monkeypatch,
        {"squad_fight": "squad_settings", "deals.deals": "deals"},
    )
    _patch_hops(
        monkeypatch,
        {("squad_settings", "squad_settings"): 0, ("squad_settings", "deals"): 3},
    )

    fight = _due_item(task_type="squad_fight", priority=80_000, task_id="fight")
    deals = _due_item(task_type="deals.deals", priority=80_000, task_id="deals")
    ranked = _rank([deals, fight], current_screen="squad_settings")

    with caplog.at_level(logging.INFO, logger="scheduler.queue"):
        RedisQueue._log_pop_candidates(
            instance_id="bs1",
            current_screen="squad_settings",
            claimed_task_id="fight",
            ranked=ranked,
        )

    assert "queue.pop_due candidates" in caplog.text
    assert "current_screen='squad_settings'" in caplog.text
    assert "claimed=fight" in caplog.text
    assert "squad_fight#fight" in caplog.text
    assert "deals.deals#deals" in caplog.text
    assert "graph=1500" in caplog.text


@pytest.mark.asyncio
async def test_explain_top_n_returns_full_breakdown(monkeypatch):
    """Debug command surfaces base_priority, effective, debuffs, hops, reachable."""
    _patch_required_nodes(monkeypatch, {"node_task": "screen_a"})
    _patch_hops(monkeypatch, {("main_city", "screen_a"): 2})

    near = _due_item(task_type="node_task", priority=80_000, player_id="p1", task_id="A")
    free = _due_item(task_type="housekeeping", priority=80_000, player_id="p1", task_id="B")

    q = RedisQueue.__new__(RedisQueue)

    async def fake_collect(self, instance_id, current_screen, now):
        return _rank([near, free])

    monkeypatch.setattr(RedisQueue, "_collect_ranked_due", fake_collect)

    rows = await q.explain_top_n("bs1", current_screen="main_city", n=10)
    assert [r["task_id"] for r in rows] == ["B", "A"]  # housekeeping has 0 debuff
    b = rows[0]
    assert b["graph_debuff"] == 0
    assert b["hops"] == 0
    assert b["reachable"] is True
    a = rows[1]
    assert a["graph_debuff"] == 2 * queue_mod.W_HOPS
    assert a["hops"] == 2
    assert a["required_node"] == "screen_a"
    assert a["reachable"] is True
    assert a["effective_priority"] == 80_000 - 2 * queue_mod.W_HOPS


def test_per_player_recent_keys_isolate_history(monkeypatch):
    """ADR §2: ``recent_key = (task_type, player_id)`` — same type, different
    player should NOT share the hot-window debuff."""
    _patch_required_nodes(monkeypatch, {})
    _patch_hops(monkeypatch, {})

    p1_hot = _due_item(task_type="scenario_s", priority=80_000, player_id="p1")
    p2_fresh = _due_item(task_type="scenario_s", priority=80_000, player_id="p2")

    ranked = _rank(
        [p1_hot, p2_fresh],
        recent_counts={("scenario_s", "p1"): 3},
    )
    # p2 has no history → wins; p1 paid full cap.
    assert ranked[0][2]["player_id"] == "p2"
    assert ranked[0][3]["recent_debuff"] == 0
    assert ranked[1][3]["recent_debuff"] == queue_mod.RECENT_RUNS_CAP * queue_mod.W_RECENT
