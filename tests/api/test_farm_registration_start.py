from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from api.routers import farm as farm_api


def _allow_r5(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "_require_r5", lambda: None)


def _no_pending(_client: object) -> None:
    return None


def test_registration_start_launches_when_idle(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    started: list[farm_api.StartRegistrationBody] = []

    monkeypatch.setattr(farm_api.farm_handoff, "get_pending", _no_pending)
    monkeypatch.setattr(farm_api, "_registration_process_running", lambda: False)

    def fake_start(body: farm_api.StartRegistrationBody) -> dict[str, Any]:
        started.append(body)
        return {
            "running": True,
            "pid": 42,
            "started_at": 12.5,
            "log_path": "/tmp/farm_registration.log",
        }

    monkeypatch.setattr(farm_api, "_start_registration_process", fake_start)

    result = farm_api.post_start_registration(
        farm_api.StartRegistrationBody(seed="stable"),
        client=object(),
    )

    assert result == {
        "running": True,
        "pid": 42,
        "started_at": 12.5,
        "log_path": "/tmp/farm_registration.log",
        "pending": None,
    }
    assert [body.seed for body in started] == ["stable"]


def test_registration_start_reuses_pending_without_launch(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    pending = {"username": "NoraLily", "started_at": "2026-06-14T00:00:00Z"}

    class Proc:
        pid = 77

    monkeypatch.setattr(farm_api, "_registration_proc", Proc())
    monkeypatch.setattr(farm_api, "_registration_started_at", 12.5)

    def get_pending(_client: object) -> dict[str, str]:
        return pending

    monkeypatch.setattr(farm_api.farm_handoff, "get_pending", get_pending)
    monkeypatch.setattr(farm_api, "_registration_process_running", lambda: True)

    def fail_start(body: farm_api.StartRegistrationBody) -> dict[str, Any]:
        msg = "registration should not be launched"
        raise AssertionError(msg)

    monkeypatch.setattr(farm_api, "_start_registration_process", fail_start)

    result = farm_api.post_start_registration(
        farm_api.StartRegistrationBody(),
        client=object(),
    )

    assert result == {
        "running": True,
        "pending": pending,
        "pid": 77,
        "started_at": 12.5,
    }


def test_start_registration_process_builds_ui_command(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class Proc:
        pid = 99

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def poll(self) -> None:
            return None

    monkeypatch.setattr(farm_api.subprocess, "Popen", Proc)
    monkeypatch.setattr(farm_api, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(farm_api.time, "time", lambda: 33.0)

    result = farm_api._start_registration_process(
        farm_api.StartRegistrationBody(
            username="NoraLily",
            seed="stable",
            server="wos_beta",
            headless=True,
        )
    )

    assert captured["cmd"] == [
        farm_api.sys.executable,
        "-m",
        "games.wos.farm.register",
        "--ui",
        "--username",
        "NoraLily",
        "--seed",
        "stable",
        "--server",
        "wos_beta",
        "--headless",
    ]
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["stderr"] == farm_api.subprocess.STDOUT
    assert captured["kwargs"]["env"]["PYTHONUNBUFFERED"] == "1"
    assert result == {
        "running": True,
        "pid": 99,
        "started_at": 33.0,
        "log_path": str(tmp_path / "temporal" / "farm_registration.log"),
    }
    assert Path(result["log_path"]).read_text().startswith("[")
    farm_api._close_registration_log_handle()


def test_start_registration_process_can_use_existing_account(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class Proc:
        pid = 100

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def poll(self) -> None:
            return None

    monkeypatch.setattr(farm_api.subprocess, "Popen", Proc)
    monkeypatch.setattr(farm_api, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(farm_api.time, "time", lambda: 44.0)

    result = farm_api._start_registration_process(
        farm_api.StartRegistrationBody(
            username="mossvale",
            existing=True,
            server="wos_beta",
        )
    )

    assert captured["cmd"] == [
        farm_api.sys.executable,
        "-m",
        "games.wos.farm.register",
        "--ui",
        "--username",
        "mossvale",
        "--existing",
        "--server",
        "wos_beta",
    ]
    assert result["pid"] == 100
    farm_api._close_registration_log_handle()


def test_registration_status_reports_exit_and_redacted_log(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _allow_r5(monkeypatch)
    log_path = tmp_path / "farm_registration.log"
    log_path.write_text(
        "Account: NoraLily  password: Secret123  (status: pending)\nboom\n",
        encoding="utf-8",
    )

    class Proc:
        pid = 123

        def poll(self) -> int:
            return 7

    monkeypatch.setattr(farm_api, "_registration_proc", Proc())
    monkeypatch.setattr(farm_api, "_registration_started_at", 10.0)
    monkeypatch.setattr(farm_api, "_registration_finished_at", None)
    monkeypatch.setattr(farm_api, "_registration_exit_code", None)
    monkeypatch.setattr(farm_api, "_registration_log_path", str(log_path))
    monkeypatch.setattr(farm_api, "_registration_log_handle", None)
    monkeypatch.setattr(farm_api.farm_handoff, "get_pending", _no_pending)

    result = farm_api.get_registration_status(client=object())

    assert result["running"] is False
    assert result["pid"] == 123
    assert result["exit_code"] == 7
    assert "Secret123" not in result["log_tail"]
    assert "password: ***" in result["log_tail"]
    assert "boom" in result["log_tail"]


def test_clear_registration_log_resets_finished_state(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _allow_r5(monkeypatch)
    log_path = tmp_path / "farm_registration.log"
    log_path.write_text("done\n", encoding="utf-8")

    monkeypatch.setattr(farm_api, "_registration_proc", None)
    monkeypatch.setattr(farm_api, "_registration_started_at", 10.0)
    monkeypatch.setattr(farm_api, "_registration_finished_at", 20.0)
    monkeypatch.setattr(farm_api, "_registration_exit_code", 0)
    monkeypatch.setattr(farm_api, "_registration_log_path", str(log_path))
    monkeypatch.setattr(farm_api, "_registration_log_handle", None)
    monkeypatch.setattr(farm_api.farm_handoff, "get_pending", _no_pending)

    result = farm_api.clear_registration_log(client=object())

    assert result == {"ok": True}
    assert not log_path.exists()
    assert farm_api._registration_log_path is None
    assert farm_api._registration_started_at is None
    assert farm_api._registration_finished_at is None
    assert farm_api._registration_exit_code is None


def test_clear_registration_log_rejects_active_process(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _allow_r5(monkeypatch)
    log_path = tmp_path / "farm_registration.log"
    log_path.write_text("active\n", encoding="utf-8")

    class Proc:
        pid = 123

        def poll(self) -> None:
            return None

    monkeypatch.setattr(farm_api, "_registration_proc", Proc())
    monkeypatch.setattr(farm_api, "_registration_log_path", str(log_path))
    monkeypatch.setattr(farm_api, "_registration_log_handle", None)
    monkeypatch.setattr(farm_api.farm_handoff, "get_pending", _no_pending)

    with pytest.raises(farm_api.HTTPException) as exc:
        farm_api.clear_registration_log(client=object())

    assert exc.value.status_code == 409
    assert log_path.exists()


def test_delete_account_requires_matching_username(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    deleted: list[str] = []

    def fake_delete(username: str, *, game: str) -> bool:
        deleted.append(f"{game}:{username}")
        return True

    monkeypatch.setattr(farm_api.farm_accounts_db, "delete_account", fake_delete)

    with pytest.raises(farm_api.HTTPException) as missing_confirm:
        farm_api.delete_account("NoraLily", None)
    assert missing_confirm.value.status_code == 400

    with pytest.raises(farm_api.HTTPException) as wrong_confirm:
        farm_api.delete_account(
            "NoraLily",
            farm_api.DeleteAccountBody(confirm_username="WrongName"),
        )
    assert wrong_confirm.value.status_code == 400

    assert deleted == []


def test_delete_account_accepts_exact_username(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    deleted: list[str] = []

    def fake_delete(username: str, *, game: str) -> bool:
        deleted.append(f"{game}:{username}")
        return True

    monkeypatch.setattr(farm_api.farm_accounts_db, "delete_account", fake_delete)

    result = farm_api.delete_account(
        "NoraLily",
        farm_api.DeleteAccountBody(confirm_username="NoraLily"),
    )

    assert result == {"ok": True}
    assert deleted == ["wos:NoraLily"]


def test_character_upsert_returns_character(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    calls: list[dict[str, str]] = []

    def fake_upsert(
        username: str,
        *,
        server: str,
        fid: str,
        game: str,
        nickname: str,
        note: str,
    ) -> farm_api.farm_accounts_db.FarmCharacter:
        calls.append(
            {
                "username": username,
                "server": server,
                "fid": fid,
                "game": game,
                "nickname": nickname,
                "note": note,
            }
        )
        return farm_api.farm_accounts_db.FarmCharacter(
            game=game,
            username=username,
            server=server,
            fid=fid,
            nickname=nickname,
            created_at=1.0,
            updated_at=2.0,
            note=note,
        )

    monkeypatch.setattr(farm_api.farm_accounts_db, "upsert_character", fake_upsert)

    result = farm_api.post_character(
        "mossvale",
        farm_api.CharacterBody(
            server="wos_beta_1",
            fid="12345",
            nickname="Moss",
            note="main",
        ),
    )

    assert result["ok"] is True
    assert result["character"]["server"] == "wos_beta_1"
    assert result["character"]["fid"] == "12345"
    assert calls == [
        {
            "username": "mossvale",
            "server": "wos_beta_1",
            "fid": "12345",
            "game": "wos",
            "nickname": "Moss",
            "note": "main",
        }
    ]


def test_list_accounts_marks_active_character(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)

    monkeypatch.setattr(
        farm_api,
        "load_settings",
        lambda: SimpleNamespace(
            instances=[
                SimpleNamespace(instance_id="bs1"),
                SimpleNamespace(instance_id="bs2"),
            ]
        ),
    )

    def fake_state(_client: object, instance_id: str) -> dict[str, str]:
        if instance_id == "bs1":
            return {
                "active_player": "222",
                "current_screen": "main_city",
                "current_scenario": "intel_run",
            }
        return {"active_player": ""}

    monkeypatch.setattr(farm_api, "get_instance_state", fake_state)
    monkeypatch.setattr(
        farm_api.farm_accounts_db,
        "list_accounts",
        lambda *, game: [
            farm_api.farm_accounts_db.FarmAccount(
                game=game,
                username="mossvale",
                characters=(
                    farm_api.farm_accounts_db.FarmCharacter(
                        game=game,
                        username="mossvale",
                        server="s1",
                        fid="111",
                    ),
                    farm_api.farm_accounts_db.FarmCharacter(
                        game=game,
                        username="mossvale",
                        server="s2",
                        fid="222",
                    ),
                ),
            )
        ],
    )

    result = farm_api.list_accounts(client=object())

    account = result["accounts"][0]
    assert account["active"]["fid"] == "222"
    assert account["active"]["instances"][0]["instance_id"] == "bs1"
    assert account["characters"][0]["active"] is None
    assert account["characters"][1]["active"]["fid"] == "222"


def test_character_delete_uses_server(monkeypatch: Any) -> None:
    _allow_r5(monkeypatch)
    deleted: list[str] = []

    def fake_delete(username: str, *, server: str, game: str) -> bool:
        deleted.append(f"{game}:{username}:{server}")
        return True

    monkeypatch.setattr(farm_api.farm_accounts_db, "delete_character", fake_delete)

    result = farm_api.delete_character("mossvale", "wos_beta_1")

    assert result == {"ok": True}
    assert deleted == ["wos:mossvale:wos_beta_1"]
