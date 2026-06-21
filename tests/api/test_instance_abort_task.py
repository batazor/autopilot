from __future__ import annotations

import json
from typing import Any

import pytest

from api.services import instance_detail


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.pushed: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, json.loads(payload)))
        return 1

    def lpush(self, key: str, payload: str) -> int:
        self.pushed.append((key, json.loads(payload)))
        return 1


@pytest.fixture
def known_instance(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(instance_detail, "list_instance_ids", lambda: ["bs1"])
    monkeypatch.setattr(
        instance_detail,
        "push_instance_command",
        lambda client, iid, cmd: client.lpush(f"wos:ui:command:{iid}", json.dumps(cmd)),
    )
    return "bs1"


def test_abort_publishes_to_abort_channel(known_instance: str) -> None:
    client = _FakeRedis()
    instance_detail.abort_current_task(client, known_instance, reason="op skip")

    assert client.published == [
        ("wos:events:abort_task:bs1", {"reason": "op skip"})
    ]
    assert client.pushed == []


def test_abort_with_restart_queues_restart_command(known_instance: str) -> None:
    client = _FakeRedis()
    instance_detail.abort_current_task(
        client, known_instance, reason="op skip", restart=True
    )

    assert client.published[0][0] == "wos:events:abort_task:bs1"
    assert client.pushed == [("wos:ui:command:bs1", {"cmd": "restart"})]


def test_abort_unknown_instance_raises(known_instance: str) -> None:
    with pytest.raises(ValueError, match="unknown instance"):
        instance_detail.abort_current_task(_FakeRedis(), "nope", reason="x")


def test_current_task_fields_running_and_idle() -> None:
    running = instance_detail._current_task_fields(
        {
            "state": "busy",
            "current_scenario": "deals.sign_in",
            "current_task_started_at": "1000.5",
        }
    )
    assert running == {
        "task_scenario": "deals.sign_in",
        "task_started_at": 1000.5,
    }

    idle = instance_detail._current_task_fields({"state": "idle"})
    assert idle == {"task_scenario": "", "task_started_at": None}
