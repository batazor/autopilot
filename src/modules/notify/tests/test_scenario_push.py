"""Notification -> direct scenario push onto the worker queue.

Covers the state-DB lookups (nickname -> gamer.id, serial -> device name) and
the end-to-end ``_maybe_push_scenario`` enqueue path with a fake Redis client.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from modules.notify import config, state_lookup
from modules.notify.publisher import RedisPublisher
from modules.notify.service import MonitorService


def _make_state_db(path) -> None:
    # Seed a plaintext state DB — state_lookup reads it read-only.
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gamers (
            game TEXT NOT NULL DEFAULT 'wos',
            player_id INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (game, player_id)
        );
        CREATE TABLE devices (
            name TEXT, adb_serial TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO gamers (game, player_id, state_json, updated_at) VALUES (?,?,?,?)",
        ("wos", 401227964, json.dumps({"id": 401227964, "nickname": "batazor"}), 0.0),
    )
    conn.execute(
        "INSERT INTO devices (name, adb_serial) VALUES (?,?)",
        ("bs1", "127.0.0.1:5555"),
    )
    conn.commit()
    conn.close()


def test_resolve_player_id_case_insensitive(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    assert state_lookup.resolve_player_id("batazor", "wos") == "401227964"
    assert state_lookup.resolve_player_id("BATAZOR", "wos") == "401227964"
    assert state_lookup.resolve_player_id("unknown", "wos") is None
    assert state_lookup.resolve_player_id("", "wos") is None


def test_resolve_instance_id(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    assert state_lookup.resolve_instance_id("127.0.0.1:5555") == "bs1"
    assert state_lookup.resolve_instance_id("127.0.0.1:9999") is None
    assert state_lookup.resolve_instance_id("") is None


def test_resolve_missing_db_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", tmp_path / "absent.db")
    assert state_lookup.resolve_player_id("batazor", "wos") is None
    assert state_lookup.resolve_instance_id("127.0.0.1:5555") is None


class _FakeClient:
    def __init__(self) -> None:
        self.zcalls = []
        self.published = []

    def zadd(self, key, mapping):
        self.zcalls.append((key, mapping))
        return 1

    def eval(self, _script, _numkeys, *keys_and_args):
        # The publisher enqueues through the shared ``enqueue_sync`` facade with
        # ``skip_if_duplicate=True``, i.e. the atomic DEDUP_ZADD_LUA. Simulate it
        # on a fresh queue: no existing duplicate → ZADD the member, return 1.
        # ARGV layout: (queue_key, payload, run_at, eff_type, pid, iid, region, ignore)
        key, payload, score = keys_and_args[0], keys_and_args[1], keys_and_args[2]
        self.zcalls.append((key, {payload: score}))
        return 1

    def publish(self, channel, message):
        # Dashboard ``queue/enqueue`` event — best-effort, recorded for assertions.
        self.published.append((channel, message))
        return 0


def _service_with_fake_redis():
    pub = RedisPublisher()
    pub._client = _FakeClient()
    return MonitorService(publisher=pub), pub


def test_maybe_push_enqueues_mapped_scenario(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    # no operator override -> derive instance from serial
    monkeypatch.setattr("modules.notify.db.get_setting", lambda _k, d=None: d)

    svc, pub = _service_with_fake_redis()
    svc._maybe_push_scenario("intel_lighthouse", "intel_lighthouse", "wos", "batazor", "127.0.0.1:5555")

    assert len(pub._client.zcalls) == 1
    key, mapping = pub._client.zcalls[0]
    assert key == "wos:queue:bs1"
    body = json.loads(next(iter(mapping)))
    assert body["task_type"] == "dsl_scenario"
    assert body["dsl_scenario"] == "intel_lighthouse"
    assert body["player_id"] == "401227964"
    assert body["instance_id"] == "bs1"


def test_no_scenario_does_not_enqueue(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    monkeypatch.setattr("modules.notify.db.get_setting", lambda _k, d=None: d)

    svc, pub = _service_with_fake_redis()
    # a pattern with no scenario set -> informational event only, no push
    svc._maybe_push_scenario("", "storehouse_supply", "wos", "batazor", "127.0.0.1:5555")
    assert pub._client.zcalls == []


def test_unknown_nickname_skips_push(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    monkeypatch.setattr("modules.notify.db.get_setting", lambda _k, d=None: d)

    svc, pub = _service_with_fake_redis()
    svc._maybe_push_scenario("intel_lighthouse", "intel_lighthouse", "wos", "ghost", "127.0.0.1:5555")
    assert pub._client.zcalls == []


def test_operator_instance_override_wins(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    monkeypatch.setattr(
        "modules.notify.db.get_setting",
        lambda k, d=None: "bs9" if k == "instance_id" else d,
    )

    svc, pub = _service_with_fake_redis()
    # serial intentionally unknown; override should still target bs9
    svc._maybe_push_scenario("intel_lighthouse", "intel_lighthouse", "wos", "batazor", "")
    assert pub._client.zcalls[0][0] == "wos:queue:bs9"


@pytest.mark.integration
def test_push_lands_in_real_queue(tmp_path, monkeypatch, redis_sync):
    """End-to-end against a real Redis (testcontainers): the pushed envelope
    lands in ``wos:queue:<instance>`` as a ZSET member the worker can pop."""
    db = tmp_path / "state.db"
    _make_state_db(db)
    monkeypatch.setattr(config, "STATE_DB_PATH", db)
    monkeypatch.setattr("modules.notify.db.get_setting", lambda _k, d=None: d)

    pub = RedisPublisher()
    pub._client = redis_sync  # the flushed testcontainers client
    svc = MonitorService(publisher=pub)

    svc._maybe_push_scenario("intel_lighthouse", "intel_lighthouse", "wos", "batazor", "127.0.0.1:5555")

    members = redis_sync.zrange("wos:queue:bs1", 0, -1)
    assert len(members) == 1
    body = json.loads(members[0])
    assert body["task_type"] == "dsl_scenario"
    assert body["dsl_scenario"] == "intel_lighthouse"
    assert body["player_id"] == "401227964"
    assert body["instance_id"] == "bs1"
    # score == run_at so pop_due treats it as due immediately
    score = redis_sync.zscore("wos:queue:bs1", members[0])
    assert score == pytest.approx(body["run_at"])
