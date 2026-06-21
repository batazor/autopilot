from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.state_schema import GamerState, StateDB
from config.state_sqlite import save_state_db, set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def player_state_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)

    gamer = GamerState(
        id=1001,
        nickname="TestPlayer",
        power=5000,
        gems=10,
    )
    gamer.buildings.furnace.level = 7
    gamer.buildings.furnace.power = 100
    gamer.buildings.levels["furnace"] = 7
    save_state_db(StateDB(gamers=[gamer]))

    devices_yaml = tmp_path / "db" / "devices.yaml"
    devices_yaml.parent.mkdir(parents=True, exist_ok=True)
    devices_yaml.write_text("devices: []\n", encoding="utf-8")

    import dashboard.player_state_data as psd

    monkeypatch.setattr(psd, "repo_root", lambda: tmp_path)
    yield tmp_path
    set_state_db_path_for_tests(None)


def test_build_persisted_player_view(player_state_repo: Path) -> None:
    from dashboard.player_state_data import build_persisted_player_view, load_state_db

    db, err, _ = load_state_db()
    assert err is None and db is not None
    view = build_persisted_player_view(db.gamers[0])
    assert view["player_id"] == "1001"
    assert view["summary"]["nickname"] == "TestPlayer"
    assert any(r["id"] == "furnace" for r in view["building_levels"])


def test_build_persisted_player_view_includes_event_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.state_schema import EventTimerState
    from dashboard import player_state_data as psd

    monkeypatch.setattr(psd.time, "time", lambda: 1_700_000_000.0)
    gamer = GamerState(id=42, nickname="TimerPlayer")
    gamer.event_timers["shop.artisans_trove"] = EventTimerState(
        remaining_s=119479,
        recorded_at=1_700_000_000.0,
        reset_at=1_700_119_479.0,
        raw_text="1d 09:11:19",
        source_region="artisans_trove.delay",
        confidence=0.97,
    )

    view = psd.build_persisted_player_view(gamer)

    timer = view["event_timers"][0]
    assert timer["event"] == "shop.artisans_trove"
    assert timer["status"] == "waiting"
    assert timer["remaining"] == "1d 09:11:19"
    assert timer["remaining_s"] == 119479
    assert timer["raw_text"] == "1d 09:11:19"
    assert timer["confidence"] == "0.97"
    assert timer["source_region"] == "artisans_trove.delay"
    assert timer["reset_at"] != "—"
    assert timer["recorded_at"] != "—"


def test_building_level_rows_from_redis() -> None:
    from dashboard.player_state_data import building_level_rows_from_redis

    rows = building_level_rows_from_redis(
        {
            "buildings.levels.furnace": "8",
            "buildings.levels.barracks": "3",
            "nickname": "x",
        }
    )
    ids = {r["id"] for r in rows}
    assert "furnace" in ids
    assert "barracks" in ids


def test_sync_player_from_century(monkeypatch: pytest.MonkeyPatch, player_state_repo: Path) -> None:
    from dashboard import player_state_data as psd

    class _FakeData:
        nickname = "SyncedNick"
        stove_level = 9
        kid = 42
        avatar_image = "https://example.com/a.png"
        stove_lv_content = 999

    class _FakeClient:
        async def fetch_player(self, fid: int) -> _FakeData:
            assert fid == 1001
            return _FakeData()

    class _FakeStore:
        def update_from_flat(self, patch: dict) -> None:
            self.patch = patch

    class _FakeStateStore:
        def get_or_create(self, pid: str, *, nickname: str) -> _FakeStore:
            return _FakeStore()

    monkeypatch.setattr(psd, "CenturyClient", lambda: _FakeClient())
    monkeypatch.setattr(psd, "get_state_store", lambda: _FakeStateStore())
    monkeypatch.setattr(psd, "upsert_device_gamer", lambda **_: None)
    monkeypatch.setattr(psd, "infer_instance_id_for_player", lambda _: "")

    out = psd.sync_player_from_century("1001")
    assert out["ok"] is True
    assert out["nickname"] == "SyncedNick"
    assert len(out["steps"]) == 3
