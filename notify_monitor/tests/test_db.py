"""Tests for the SQLite data layer using an isolated temp database."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from notify_monitor import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db_mod = importlib.import_module("notify_monitor.db")
    db_mod.init_db()
    return db_mod


def test_seed_patterns_present(db):
    pats = db.list_patterns()
    assert pats, "seed patterns should be inserted on init"
    assert {p["game"] for p in pats} == {"wos", "kingshot"}


def test_init_is_idempotent_and_keeps_edits(db):
    pid = db.add_pattern("wos", r"foo", "foo_event", "x")
    db.init_db()  # re-init should not wipe patterns
    assert any(p["id"] == pid for p in db.list_patterns())


def test_player_lifecycle(db):
    pid = db.add_player("Tazor", "wos")
    assert any(p["id"] == pid for p in db.list_players("wos"))
    db.set_player_active(pid, False)
    assert db.list_players("wos")[0]["active"] == 0
    db.delete_player(pid)
    assert db.list_players("wos") == []


def test_ensure_player_autodiscovers_active(db):
    row = db.ensure_player("NewGuy", "kingshot")
    assert row["active"] == 1
    # second call returns existing without reactivating
    db.set_player_active(row["id"], False)
    again = db.ensure_player("NewGuy", "kingshot")
    assert again["active"] == 0
    assert db.ensure_player("  ", "wos") is None


def test_events_and_counts(db):
    db.add_event("wos", "Tazor", "research_done", "Research complete!")
    evs = db.list_events(limit=10, game="wos")
    assert evs[0]["event_type"] == "research_done"
    assert db.counts()["events"] == 1


def test_unrecognized_flow(db):
    nid = db.add_unrecognized("wos", "weird text")
    assert db.list_unrecognized()[0]["id"] == nid
    db.set_unrecognized_reviewed(nid, True)
    assert db.list_unrecognized(include_reviewed=False) == []
    assert db.get_unrecognized(nid)["reviewed"] == 1


def test_settings_roundtrip(db):
    assert db.get_setting("poll_interval") == "10"
    db.set_setting("poll_interval", "30")
    assert db.get_setting("poll_interval") == "30"


def test_seed_sets_scenario_for_intel(db):
    pats = {(p["game"], p["event_type"]): p for p in db.list_patterns()}
    intel = pats[("wos", "intel_lighthouse")]
    assert intel["scenario"] == "intel_lighthouse"
    # an informational pattern carries no scenario
    assert pats[("wos", "storehouse_supply")]["scenario"] == ""


def test_seed_backfills_scenario_without_clobbering_edits(db):
    from notify_monitor import migrations
    from notify_monitor.db import _engine

    rows = {(p["game"], p["event_type"]): p for p in db.list_patterns()}
    # operator cleared intel's scenario (simulating a stale/pre-migration DB)
    db.update_pattern(rows[("wos", "intel_lighthouse")]["id"], scenario="")
    # ...and set a custom scenario on another pattern that must survive re-seed
    db.update_pattern(rows[("wos", "research_done")]["id"], scenario="my_custom")

    migrations.run_migrations(_engine())

    after = {(p["game"], p["event_type"]): p for p in db.list_patterns()}
    assert after[("wos", "intel_lighthouse")]["scenario"] == "intel_lighthouse"  # backfilled
    assert after[("wos", "research_done")]["scenario"] == "my_custom"  # preserved


def test_matcher_returns_scenario(db):
    from notify_monitor.parser import PatternMatcher

    m = PatternMatcher(ttl_seconds=0.0)
    res = m.match("New Intel in the Lighthouse — the Lighthouse has new Intel, check it", "wos")
    assert res is not None
    assert res.event_type == "intel_lighthouse"
    assert res.scenario == "intel_lighthouse"
