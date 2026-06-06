from __future__ import annotations

import json

from api.services import queue_api


def test_remove_pending_scenario_tasks_is_instance_and_scenario_scoped(
    redis_sync: object,
) -> None:
    key_bs1 = "wos:queue:bs1"
    key_bs2 = "wos:queue:bs2"
    rows = [
        (
            key_bs1,
            {
                "task_id": "manual:old-dreamscape",
                "task_type": "dsl_scenario",
                "instance_id": "bs1",
                "dsl_scenario": "dreamscape_memory",
                "run_at": 1,
            },
        ),
        (
            key_bs1,
            {
                "task_id": "cron:keep",
                "task_type": "claim",
                "instance_id": "bs1",
                "dsl_scenario": "claim",
                "run_at": 2,
            },
        ),
        (
            key_bs2,
            {
                "task_id": "manual:other-instance",
                "task_type": "dsl_scenario",
                "instance_id": "bs2",
                "dsl_scenario": "dreamscape_memory",
                "run_at": 3,
            },
        ),
    ]
    for key, row in rows:
        redis_sync.zadd(key, {json.dumps(row): float(row["run_at"])})  # type: ignore[attr-defined]

    removed = queue_api.remove_pending_scenario_tasks(
        redis_sync,  # type: ignore[arg-type]
        instance_id="bs1",
        scenario_key="dreamscape_memory",
    )

    assert removed == 1
    remaining_bs1 = [json.loads(raw) for raw in redis_sync.zrange(key_bs1, 0, -1)]  # type: ignore[attr-defined]
    remaining_bs2 = [json.loads(raw) for raw in redis_sync.zrange(key_bs2, 0, -1)]  # type: ignore[attr-defined]
    assert [row["task_id"] for row in remaining_bs1] == ["cron:keep"]
    assert [row["task_id"] for row in remaining_bs2] == ["manual:other-instance"]


def test_enqueue_user_task_can_abort_running_task(
    redis_sync: object,
    monkeypatch,
) -> None:
    published: list[tuple[str, dict[str, str]]] = []

    monkeypatch.setattr(
        queue_api._tmpl,
        "resolve",
        lambda _root, _scenario_key: object(),
    )
    monkeypatch.setattr(
        queue_api._tmpl,
        "load_doc",
        lambda _root, _scenario_key: (None, {"device_level": True}),
    )
    monkeypatch.setattr(queue_api, "enqueue_envelope", lambda _env, _client: "wos:queue:bs1")
    monkeypatch.setattr(queue_api, "push_scheduler_command", lambda _client, _cmd: None)

    def publish(channel: str, payload: str) -> int:
        published.append((channel, json.loads(payload)))
        return 1

    monkeypatch.setattr(redis_sync, "publish", publish)

    result = queue_api.enqueue_user_task(
        redis_sync,  # type: ignore[arg-type]
        scenario_key="dreamscape_memory",
        instance_id="bs1",
        player_id="",
        scheduled_at=123.0,
        priority=90000,
        replace_existing=True,
        abort_running=True,
    )

    assert result["queue_key"] == "wos:queue:bs1"
    assert (
        "wos:events:abort_task:bs1",
        {"reason": "operator restart requested: dreamscape_memory"},
    ) in published
