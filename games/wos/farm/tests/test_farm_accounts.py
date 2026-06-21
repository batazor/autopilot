"""Farm credential generator + encrypted account store."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from games.wos.farm import generator

from config import farm_accounts_db as db
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _temp_state_db(tmp_path: Path) -> Iterator[None]:
    set_state_db_path_for_tests(tmp_path / "state.db")
    try:
        yield
    finally:
        set_state_db_path_for_tests(None)


def test_add_get_list_count_and_collision() -> None:
    acct = db.add_account("farmabc123", password="pw1", email="a@x.io")
    assert acct.status == db.STATUS_PENDING
    assert acct.created_at > 0
    assert db.username_exists("farmabc123")
    assert db.count_accounts() == 1
    assert [a.username for a in db.list_accounts()] == ["farmabc123"]
    # (game, username) is unique — re-adding must not clobber credentials.
    with pytest.raises(ValueError, match="already exists"):
        db.add_account("farmabc123", password="other")


def test_status_transitions_and_binding() -> None:
    db.add_account("farmreg", password="pw")
    assert db.set_status("farmreg", db.STATUS_REGISTERED) is True
    reg = db.get_account("farmreg")
    assert reg.status == db.STATUS_REGISTERED
    assert reg.registered_at is not None

    assert (
        db.upsert_character(
            "farmreg",
            server="wos_beta_1",
            fid="900123",
            nickname="first",
        )
        is not None
    )
    assert (
        db.upsert_character(
            "farmreg",
            server="wos_beta_2",
            fid="900999",
            nickname="second",
        )
        is not None
    )
    assert db.upsert_character("missing", server="wos_beta", fid="1") is None
    chars = db.get_account("farmreg").characters
    assert [(c.server, c.fid, c.nickname) for c in chars] == [
        ("wos_beta_1", "900123", "first"),
        ("wos_beta_2", "900999", "second"),
    ]

    assert db.bind_device("farmreg", "127.0.0.1:5555") is True
    bound = db.get_account("farmreg")
    assert bound.status == db.STATUS_BOUND
    assert bound.device_serial == "127.0.0.1:5555"

    with pytest.raises(ValueError, match="invalid status"):
        db.set_status("farmreg", "nonsense")
    assert db.set_status("missing", db.STATUS_REGISTERED) is False
    assert db.delete_account("farmreg") is True
    assert db.count_accounts() == 0
    assert db.list_characters("farmreg") == []


def test_character_upsert_delete_and_validation() -> None:
    db.add_account("farmchars", password="pw")
    with pytest.raises(ValueError, match="server"):
        db.upsert_character("farmchars", server="", fid="1")
    with pytest.raises(ValueError, match="fid"):
        db.upsert_character("farmchars", server="wos_beta", fid="")

    created = db.upsert_character("farmchars", server="wos_beta", fid="100")
    assert created is not None
    assert created.fid == "100"
    updated = db.upsert_character(
        "farmchars",
        server="wos_beta",
        fid="101",
        nickname="main",
    )
    assert updated is not None
    assert updated.fid == "101"
    assert updated.nickname == "main"
    assert [(c.server, c.fid) for c in db.list_characters("farmchars")] == [
        ("wos_beta", "101")
    ]

    assert db.delete_character("farmchars", server="wos_beta") is True
    assert db.delete_character("farmchars", server="wos_beta") is False


def test_generator_deterministic_is_reproducible() -> None:
    a = generator.generate(3, seed="batch-1", exists=lambda _u: False)
    b = generator.generate(3, seed="batch-1", exists=lambda _u: False)
    assert [x.username for x in a] == [x.username for x in b]
    assert [x.password for x in a] == [x.password for x in b]
    # Different seed → different usernames.
    c = generator.generate(3, seed="batch-2", exists=lambda _u: False)
    assert [x.username for x in a] != [x.username for x in c]


def test_generator_random_is_unique_and_shaped() -> None:
    accts = generator.generate(25, exists=lambda _u: False)
    names = [a.username for a in accts]
    assert len(set(names)) == 25
    for a in accts:
        assert a.email == f"{a.username.lower()}@farm.local"
        # Beta form: username + password must be 6-15 letters/digits only.
        assert 6 <= len(a.username) <= 15 and a.username.isalnum()
        assert 6 <= len(a.password) <= 15 and a.password.isalnum()
        # Auto-generated farm nicknames should look human-readable, no digits.
        assert a.username.isalpha()
        assert a.username == a.username.lower()


def test_generator_uses_multiple_name_shapes() -> None:
    names = [
        a.username
        for a in generator.generate(50, seed="shape-test", exists=lambda _u: False)
    ]
    shapes = set()
    for name in names:
        if name in generator.COMPOUNDS:
            shapes.add("compound")
        if any(name.startswith(w) for w in generator.STEMS):
            shapes.add("stem_first")
        if any(name.startswith(w) for w in generator.PREFIXES):
            shapes.add("prefix_first")
        if any(name.endswith(w) for w in generator.SUFFIXES):
            shapes.add("suffix_last")
        if any(name.startswith(w) for w in generator.NOUNS):
            shapes.add("noun_first")

    assert len(shapes) >= 4


def test_generator_retries_on_collision() -> None:
    # Reject the first deterministic name, force a retry to a fresh one.
    first = generator.generate(1, seed="s", exists=lambda _u: False)[0].username

    def _exists(u: str) -> bool:
        return u == first

    retried = generator.generate(1, seed="s", exists=_exists)[0].username
    assert retried != first


def test_add_or_generate_falls_back_when_name_taken() -> None:
    # The operator's nick already exists in the store → claim must not clobber
    # it, and must mint a fresh pretty name instead.
    db.add_account("balabol", password="seedpw")
    res = generator.add_or_generate("balabol")
    assert res.requested == "balabol"
    assert res.requested_taken is True
    assert res.account.username != "balabol"
    assert db.username_exists(res.account.username)
    # A free letter-only name is claimed as-is.
    free = generator.add_or_generate("frostraven")
    assert free.requested_taken is False
    assert free.account.username == "frostraven"
    # Invalid desired (digits / symbols / too short) is rejected up front.
    with pytest.raises(ValueError, match="letters"):
        generator.add_or_generate("frostraven9")
    with pytest.raises(ValueError, match="letters"):
        generator.add_or_generate("ab!")


def test_generate_and_store_persists_pending() -> None:
    stored = generator.generate_and_store(4, seed="store-seed")
    assert len(stored) == 4
    assert db.count_accounts(status=db.STATUS_PENDING) == 4
    for a in stored:
        got = db.get_account(a.username)
        assert got is not None
        assert got.password == a.password
        assert got.server == "wos_beta"
