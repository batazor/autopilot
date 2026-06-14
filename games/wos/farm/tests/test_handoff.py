"""Redis handoff between the registration process and the dashboard button."""
from __future__ import annotations

import pytest

from dashboard import farm_handoff


class _FakeRedis:
    def __init__(self) -> None:
        self.h: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}

    def delete(self, key: str) -> None:
        self.h.pop(key, None)
        self.kv.pop(key, None)

    def hset(self, key: str, mapping: dict[str, str] | None = None) -> None:
        self.h.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.h.get(key, {}))

    def expire(self, key: str, ttl: int) -> None:
        return None

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value

    def get(self, key: str) -> str | None:
        return self.kv.get(key)


def test_pending_lifecycle_and_signal() -> None:
    r = _FakeRedis()
    assert farm_handoff.get_pending(r) is None

    farm_handoff.set_pending(r, "FrostRaven7")
    pending = farm_handoff.get_pending(r)
    assert pending is not None
    assert pending["username"] == "FrostRaven7"
    assert "started_at" in pending

    assert farm_handoff.read_signal(r, "FrostRaven7") is None
    farm_handoff.signal(r, "FrostRaven7", "done")
    assert farm_handoff.read_signal(r, "FrostRaven7") == "done"

    farm_handoff.clear_pending(r, "FrostRaven7")
    assert farm_handoff.get_pending(r) is None
    assert farm_handoff.read_signal(r, "FrostRaven7") is None


def test_set_pending_clears_stale_signal() -> None:
    r = _FakeRedis()
    farm_handoff.signal(r, "EmberWolf3", "done")  # leftover from a previous run
    farm_handoff.set_pending(r, "EmberWolf3")
    assert farm_handoff.read_signal(r, "EmberWolf3") is None


def test_signal_rejects_unknown_outcome() -> None:
    r = _FakeRedis()
    with pytest.raises(ValueError, match="must be"):
        farm_handoff.signal(r, "EmberWolf3", "maybe")
