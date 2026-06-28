from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.state_schema import GamerState, StateDB
from config.state_sqlite import (
    delete_player_state,
    get_alliance_members,
    get_alliance_stats,
    get_player_stats,
    list_alliance_names,
    list_gamers_by_power,
    load_state_db_raw,
    record_alliance_members_snapshot,
    record_alliance_stats,
    record_player_stats,
    save_state_db,
    set_state_db_path_for_tests,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_state(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_save_load_roundtrip(sqlite_state: Path) -> None:
    g = GamerState(id=42, nickname="Alice", power=9000)
    g.buildings.furnace.level = 5
    save_state_db(StateDB(gamers=[g]))
    db, err, _ = load_state_db_raw()
    assert err is None
    assert db is not None
    assert len(db.gamers) == 1
    assert db.gamers[0].nickname == "Alice"
    assert db.gamers[0].power == 9000


def test_state_store_sets_alliance_money(sqlite_state: Path) -> None:
    from config.state_store import StateStore

    store = StateStore(sqlite_state)
    gamer = store.get_or_create("42", nickname="Alice")
    gamer.set("alliance.money", 313_416)

    assert gamer.get("alliance.money") == 313_416
    db, err, _ = load_state_db_raw()
    assert err is None
    assert db is not None
    assert db.gamers[0].alliance.money == 313_416


def test_state_store_persists_troop_counts_and_war_academy_fc(sqlite_state: Path) -> None:
    """The durable homes added for ``sync_troop_pool`` / ``sync_war_academy_levels``:
    ``troops.<type>.<suffix>`` and ``researches.war_academy_fc`` must persist through
    the same flat dot-key path the readers use, and survive a reload."""
    from config.state_store import StateStore

    store = StateStore(sqlite_state)
    gamer = store.get_or_create("77", nickname="Bravo")
    gamer.update_from_flat({
        "troops.infantry.available": 73_443,
        "troops.lancer.wilderness": 1_200,
        "troops.marksman.total": 34_449,
        "researches.war_academy_fc": 7,
    })

    db, err, _ = load_state_db_raw()
    assert err is None
    assert db is not None
    g = db.gamers[0]
    assert g.troops.infantry.available == 73_443
    assert g.troops.lancer.wilderness == 1_200
    assert g.troops.marksman.total == 34_449
    assert g.researches.war_academy_fc == 7


def test_record_alliance_members_snapshot_is_alliance_scoped(sqlite_state: Path) -> None:
    row = record_alliance_members_snapshot(
        alliance_name="Crimson",
        members=[
            {
                "rank": 4,
                "name": "RedLady",
                "power": 80_600_000,
                "level": 30,
                "status": "Online",
                "online": True,
                "last_online_text": "Online",
                "last_online_seconds": 0,
            },
            {
                "rank": 3,
                "name": "Ne2pY",
                "power": 79_900_000,
                "level": 30,
                "status": "7 minute(s) ago",
                "online": False,
                "last_online_text": "7 minute(s) ago",
                "last_online_seconds": 420,
            },
        ],
    )

    assert row["members_count"] == 2
    members = get_alliance_members("Crimson")["members"]
    assert [m["name"] for m in members] == ["RedLady", "Ne2pY"]
    assert members[0]["online"] is True
    assert members[0]["last_online_seconds"] == 0
    assert members[1]["online"] is False
    assert members[1]["last_online_text"] == "7 minute(s) ago"
    assert members[1]["last_online_seconds"] == 420


def test_list_gamers_by_power_filters_on_generated_column(sqlite_state: Path) -> None:
    save_state_db(StateDB(gamers=[
        GamerState(id=1, nickname="Weak", power=100),
        GamerState(id=2, nickname="Mid", power=5000),
        GamerState(id=3, nickname="Strong", power=9000),
    ]))
    strong = list_gamers_by_power(5000)
    assert [g.id for g in strong] == [3, 2]  # power DESC, >= 5000 only
    assert list_gamers_by_power(100_000) == []


def test_list_gamers_by_power_uses_index(sqlite_state: Path) -> None:
    import sqlite3

    save_state_db(StateDB(gamers=[GamerState(id=1, power=100)]))
    conn = sqlite3.connect(sqlite_state)
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT state_json FROM gamers "
            "WHERE game = 'wos' AND power >= 50"
        ).fetchall()
    finally:
        conn.close()
    assert any("idx_gamers_power" in str(row) for row in plan)


def test_daily_power_and_level_event(sqlite_state: Path) -> None:
    g = GamerState(id=1, power=100)
    g.buildings.furnace.level = 3
    save_state_db(StateDB(gamers=[g]))
    record_player_stats(g)

    g2 = g.model_copy(deep=True)
    g2.power = 150
    g2.buildings.furnace.level = 4
    record_player_stats(g2)

    stats = get_player_stats("1")
    assert len(stats["series"]) == 1
    assert stats["series"][0]["power"] == 150
    assert stats["series"][0]["furnace_level"] == 4
    assert len(stats["level_events"]) == 2
    assert stats["level_events"][-1]["level"] == 4


def test_direct_alliance_stats_record_rank_and_level(sqlite_state: Path) -> None:
    row = record_alliance_stats(
        alliance_name="KLA",
        power=4_388_228_831,
        rank=3,
        level=10,
        members_count=81,
        members_max=88,
    )

    assert row["alliance_name"] == "KLA"
    stats = get_alliance_stats("KLA")
    assert stats["series"] == [
        {
            "day": stats["series"][0]["day"],
            "power": 4_388_228_831,
            "rank": 3,
            "level": 10,
            "members_count": 81,
            "members_max": 88,
        }
    ]


def test_player_stats_mirror_alliance_rank_and_level(sqlite_state: Path) -> None:
    g = GamerState(id=1)
    g.alliance.name = "Crimson"
    g.alliance.power = 5000
    g.alliance.rank = 7
    g.alliance.myLevel = 11
    g.alliance.members.count = 42
    g.alliance.members.max = 50

    record_player_stats(g)

    latest = get_alliance_stats("Crimson")["series"][0]
    assert latest["rank"] == 7
    assert latest["level"] == 11


# ---------------------------------------------------------------------------
# Phase 2b: per-game scoping — overlapping player IDs across games must NOT
# clobber each other's state.
# ---------------------------------------------------------------------------


def test_gamers_with_same_id_across_games_do_not_collide(sqlite_state: Path) -> None:
    """Two games may issue the same numeric player_id; state must stay separate."""
    wos_gamer = GamerState(id=111, game="wos", nickname="WosAlice", power=1000)
    kingshot_gamer = GamerState(id=111, game="kingshot", nickname="KingAlice", power=2000)
    save_state_db(StateDB(gamers=[wos_gamer]))
    save_state_db(StateDB(gamers=[kingshot_gamer]))

    wos_db, err1, _ = load_state_db_raw("wos")
    king_db, err2, _ = load_state_db_raw("kingshot")
    assert err1 is None and err2 is None
    assert len(wos_db.gamers) == 1
    assert len(king_db.gamers) == 1
    assert wos_db.gamers[0].nickname == "WosAlice"
    assert wos_db.gamers[0].power == 1000
    assert king_db.gamers[0].nickname == "KingAlice"
    assert king_db.gamers[0].power == 2000


def test_save_state_db_only_touches_games_it_writes(sqlite_state: Path) -> None:
    """Saving wos gamers must not wipe kingshot rows (or vice versa)."""
    save_state_db(StateDB(gamers=[GamerState(id=1, game="kingshot", nickname="K")]))
    save_state_db(StateDB(gamers=[GamerState(id=2, game="wos", nickname="W")]))

    king_db, _, _ = load_state_db_raw("kingshot")
    assert {g.id for g in king_db.gamers} == {1}


def test_player_stats_are_game_scoped(sqlite_state: Path) -> None:
    """Same player_id under two games must have independent stat series."""
    wos_g = GamerState(id=42, game="wos", power=500)
    wos_g.buildings.furnace.level = 5
    wos_g.alliance.name = "ANGELS"
    king_g = GamerState(id=42, game="kingshot", power=900)
    king_g.buildings.furnace.level = 8
    king_g.alliance.name = "ANGELS"  # same name on purpose

    record_player_stats(wos_g)
    record_player_stats(king_g)

    wos_stats = get_player_stats("42", game="wos")
    king_stats = get_player_stats("42", game="kingshot")
    assert wos_stats["series"][0]["power"] == 500
    assert king_stats["series"][0]["power"] == 900
    assert wos_stats["series"][0]["furnace_level"] == 5
    assert king_stats["series"][0]["furnace_level"] == 8


def test_alliance_names_are_game_scoped(sqlite_state: Path) -> None:
    """Same alliance name in two games stays in two distinct lists."""
    wos_g = GamerState(id=1, game="wos")
    wos_g.alliance.name = "Crimson"
    king_g = GamerState(id=2, game="kingshot")
    king_g.alliance.name = "Crimson"

    record_player_stats(wos_g)
    record_player_stats(king_g)

    assert list_alliance_names("wos") == ["Crimson"]
    assert list_alliance_names("kingshot") == ["Crimson"]
    # And stats stay independent
    assert get_alliance_stats("Crimson", game="wos")["series"]
    assert get_alliance_stats("Crimson", game="kingshot")["series"]


def test_delete_player_state_only_wipes_target_game(sqlite_state: Path) -> None:
    save_state_db(StateDB(gamers=[GamerState(id=99, game="wos", nickname="W")]))
    save_state_db(StateDB(gamers=[GamerState(id=99, game="kingshot", nickname="K")]))

    counts = delete_player_state("99", game="wos")
    assert counts["gamers"] == 1

    wos_db, _, _ = load_state_db_raw("wos")
    king_db, _, _ = load_state_db_raw("kingshot")
    assert len(wos_db.gamers) == 0
    assert len(king_db.gamers) == 1


def test_default_game_back_compat(sqlite_state: Path) -> None:
    """Functions without explicit game arg target the platform default ('wos')."""
    g = GamerState(id=7, nickname="Legacy", power=1)
    save_state_db(StateDB(gamers=[g]))  # writes with game='wos'

    db, err, _ = load_state_db_raw()  # default game='wos'
    assert err is None
    assert len(db.gamers) == 1
    assert db.gamers[0].game == "wos"
