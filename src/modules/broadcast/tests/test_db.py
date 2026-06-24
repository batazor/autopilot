"""Catalog + send-log persistence tests (use the autouse temp state.db)."""
from __future__ import annotations

from modules.broadcast import db, seed
from modules.broadcast.models import (
    CHANNEL_WORLD,
    SCOPE_KINGSHOT,
    SCOPE_WOS,
    BroadcastMessage,
)


def _msg(id_: str, *, scope: str = SCOPE_WOS, enabled: bool = True) -> BroadcastMessage:
    return BroadcastMessage(id=id_, title=id_, text="hello", game_scope=scope, enabled=enabled)


def test_upsert_get_roundtrip() -> None:
    db.upsert_message(_msg("a"))
    got = db.get_message("a")
    assert got is not None
    assert got.title == "a"
    assert got.created_at > 0
    assert got.updated_at >= got.created_at


def test_list_filters_by_game_including_all() -> None:
    db.upsert_message(_msg("w", scope=SCOPE_WOS))
    db.upsert_message(_msg("k", scope=SCOPE_KINGSHOT))
    db.upsert_message(BroadcastMessage(id="all", title="all", text="x", game_scope="all"))
    wos_ids = {m.id for m in db.list_messages(game=SCOPE_WOS)}
    assert wos_ids == {"w", "all"}
    ks_ids = {m.id for m in db.list_messages(game=SCOPE_KINGSHOT)}
    assert ks_ids == {"k", "all"}


def test_enabled_only_filter() -> None:
    db.upsert_message(_msg("on", enabled=True))
    db.upsert_message(_msg("off", enabled=False))
    ids = {m.id for m in db.list_messages(enabled_only=True)}
    assert ids == {"on"}


def test_set_enabled_and_delete() -> None:
    db.upsert_message(_msg("a"))
    updated = db.set_enabled("a", False)
    assert updated is not None and updated.enabled is False
    assert db.set_enabled("missing", True) is None
    assert db.delete_message("a") is True
    assert db.delete_message("a") is False
    assert db.get_message("a") is None


def test_upsert_preserves_created_at() -> None:
    first = db.upsert_message(_msg("a"), now=1000.0)
    second = db.upsert_message(_msg("a"), now=2000.0)
    assert second.created_at == first.created_at == 1000.0
    assert second.updated_at == 2000.0


def test_send_log_roundtrip() -> None:
    db.record_send(message_id="a", game="wos", alliance="ABC", fid="42", text="hi", sent_at=5.0)
    db.record_send(message_id="b", game="wos", alliance="XYZ", fid="9", text="yo", sent_at=9.0)
    rows = db.recent_sends(game="wos")
    assert [r.message_id for r in rows] == ["b", "a"]  # newest first
    abc = db.recent_sends(alliance="ABC")
    assert len(abc) == 1 and abc[0].fid == "42"


def test_channel_roundtrip_defaults_alliance() -> None:
    db.upsert_message(_msg("a"))  # default channel
    db.upsert_message(BroadcastMessage(id="w", title="w", text="join", channel=CHANNEL_WORLD))
    assert db.get_message("a").channel == "alliance"
    assert db.get_message("w").channel == "world"


def test_seed_defaults_is_idempotent() -> None:
    added = seed.seed_defaults()
    assert added  # starter set inserted
    again = seed.seed_defaults()
    assert again == []  # nothing new on a second run
    ids = {m.id for m in db.list_messages()}
    assert "starter_foundry_battle" in ids
    # The world-chat recruiting starter ships too.
    world = db.get_message("starter_world_recruit")
    assert world is not None and world.channel == "world"
