from __future__ import annotations

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
