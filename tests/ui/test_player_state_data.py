from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def player_state_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_yaml = tmp_path / "db" / "state.yaml"
    state_yaml.parent.mkdir(parents=True)
    state_yaml.write_text(
        """
gamers:
  - id: 1001
    nickname: TestPlayer
    power: 5000
    gems: 10
    buildings:
      furnace:
        level: 7
        power: 100
      levels:
        furnace: 7
    heroes:
      entries: {}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    devices_yaml = tmp_path / "db" / "devices.yaml"
    devices_yaml.write_text("devices: []\n", encoding="utf-8")

    import ui.player_state_data as psd

    monkeypatch.setattr(psd, "_STATE_PATH", state_yaml)
    monkeypatch.setattr(psd, "repo_root", lambda: tmp_path)
    return tmp_path


def test_build_persisted_player_view(player_state_repo: Path) -> None:
    from ui.player_state_data import build_persisted_player_view, load_state_db

    db, err, _ = load_state_db()
    assert err is None and db is not None
    view = build_persisted_player_view(db.gamers[0])
    assert view["player_id"] == "1001"
    assert view["summary"]["nickname"] == "TestPlayer"
    assert any(r["id"] == "furnace" for r in view["building_levels"])


def test_building_level_rows_from_redis() -> None:
    from ui.player_state_data import building_level_rows_from_redis

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
    from ui import player_state_data as psd

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
