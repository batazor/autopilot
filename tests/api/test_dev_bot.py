from __future__ import annotations

from unittest.mock import MagicMock

from api.routers import dev_bot


def test_start_endpoint_uses_supervisor_subprocess(monkeypatch) -> None:
    calls: list[str] = []

    def _start() -> dict[str, object]:
        calls.append("supervisor")
        return {"running": True, "mode": "supervisor", "pid": 1234}

    monkeypatch.setattr(dev_bot.local_bot, "start_supervisor_subprocess", _start)

    assert dev_bot.post_bot_start() == {
        "running": True,
        "mode": "supervisor",
        "pid": 1234,
    }
    assert calls == ["supervisor"]


def test_status_uses_fleet_heartbeat_when_process_is_not_local(
    monkeypatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        dev_bot.local_bot,
        "bot_status",
        lambda: {"running": False, "mode": None, "pid": None, "processes": []},
    )
    monkeypatch.setattr(
        dev_bot.fleet,
        "build_overview",
        lambda _client: {
            "fleet": [
                {"instance_id": "bs1", "status": "live"},
                {"instance_id": "bs2", "status": "stale"},
            ],
        },
    )

    assert dev_bot.get_bot_status(client) == {
        "running": True,
        "mode": "fleet",
        "pid": None,
        "processes": [{"pid": None, "started_at": None}],
        "fleet_workers": 1,
    }
