from __future__ import annotations

import importlib.util
from pathlib import Path

# exec.py is imported via importlib without a sys.modules entry at runtime, so
# load it the same way here rather than as a package module.
_EXEC = Path(__file__).resolve().parents[1] / "exec.py"
_spec = importlib.util.spec_from_file_location("onboarding_exec_under_test", _EXEC)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
_slug = _mod._slug


def test_slug_plain_name() -> None:
    assert _slug("Sawmill") == "sawmill"
    assert _slug("Cookhouse") == "cookhouse"


def test_slug_strips_level_suffix() -> None:
    assert _slug("Hunters' Hut Lv. 1") == "hunters_hut"
    assert _slug("Sawmill Lv.2") == "sawmill"


def test_slug_empty_and_junk() -> None:
    assert _slug("") == ""
    assert _slug("   ") == ""
    assert _slug("Lv. 3") == ""


def test_handler_registered() -> None:
    assert "record_onboarding_build" in _mod.DSL_EXEC_HANDLERS


class _FakeRedis:
    """Returns the build-title OCR breadcrumbs; records hset mirrors."""

    def __init__(self, region: str, value: str) -> None:
        self._fields = {"dsl_last_ocr_region": region, "dsl_last_ocr_value": value}
        self.hset_calls: list[tuple[str, str]] = []

    async def hget(self, _key: str, field: str):
        return self._fields.get(field)

    async def hset(self, _key: str, field: str, value: str) -> None:
        self.hset_calls.append((field, value))


class _FakeStore:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def to_flat_dict(self) -> dict:
        return {}

    def update_from_flat(self, flat: dict) -> None:
        self._sink.update(flat)


import pytest  # noqa: E402

from tasks.dsl_exec.context import DslExecContext  # noqa: E402


@pytest.mark.asyncio
async def test_record_writes_durable_and_mirror_keyed_by_device(monkeypatch) -> None:
    durable: dict = {}

    class _Root:
        def get_or_create(self, pid: str):
            durable["_player_id"] = pid
            return _FakeStore(durable)

    import config.state_store as ss

    monkeypatch.setattr(ss, "get_state_store", lambda: _Root())

    r = _FakeRedis("onboarding.build.title", "Sawmill")
    # player_id empty (onboarding) → falls back to the device id.
    ctx = DslExecContext(redis_client=r, player_id="", instance_id="bs1")
    await _mod._exec_record_onboarding_build(ctx)

    assert durable.get("buildings.levels.sawmill") == 1
    assert durable["_player_id"] == "bs1"
    assert ("buildings.levels.sawmill", "1") in r.hset_calls
    assert ctx.result["building"] == "sawmill"


@pytest.mark.asyncio
async def test_record_ignores_non_title_ocr() -> None:
    r = _FakeRedis("some.other.region", "Sawmill")
    ctx = DslExecContext(redis_client=r, player_id="", instance_id="bs1")
    await _mod._exec_record_onboarding_build(ctx)
    assert ctx.result.get("reason") == "no_title_ocr"
    assert r.hset_calls == []
