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


def _zadd_task(client: object, instance_id: str, task_id: str, run_at: float) -> None:
    payload = {
        "task_id": task_id,
        "task_type": "dsl_scenario",
        "instance_id": instance_id,
        "dsl_scenario": "claim",
        "priority": 80_000,
        "run_at": run_at,
    }
    client.zadd(f"wos:queue:{instance_id}", {json.dumps(payload): run_at})  # type: ignore[attr-defined]


def test_build_queue_view_marks_offline_instance_rows_blocked(
    redis_sync: object,
    monkeypatch,
) -> None:
    monkeypatch.setattr(queue_api, "list_instance_ids", lambda: ["bs1", "bs2"])
    monkeypatch.setattr(
        queue_api,
        "sort_queue_rows_by_execution_order",
        lambda _client, rows: rows,
    )
    redis_sync.hset(  # type: ignore[attr-defined]
        "wos:instance:bs2:state",
        mapping={"last_error": "device offline (ADB)"},
    )
    _zadd_task(redis_sync, "bs1", "t-live", 1.0)
    _zadd_task(redis_sync, "bs2", "t-stuck", 2.0)

    view = queue_api.build_queue_view(redis_sync)  # type: ignore[arg-type]

    by_id = {row["task_id"]: row for row in view["pending"]}
    assert by_id["t-live"]["blocked"] is False
    assert by_id["t-live"]["blocked_reason"] == ""
    assert by_id["t-stuck"]["blocked"] is True
    assert by_id["t-stuck"]["blocked_reason"] == "device offline (ADB)"
    assert view["pending_blocked_count"] == 1


def test_purge_blocked_tasks_only_clears_offline_instances(
    redis_sync: object,
    monkeypatch,
) -> None:
    monkeypatch.setattr(queue_api, "list_instance_ids", lambda: ["bs1", "bs2"])
    # bs2's worker auto-paused on a dead device; bs1 is healthy.
    redis_sync.hset(  # type: ignore[attr-defined]
        "wos:instance:bs2:state",
        mapping={"auto_paused": "1", "paused": "1"},
    )
    _zadd_task(redis_sync, "bs1", "t-keep", 1.0)
    _zadd_task(redis_sync, "bs2", "t-gone-1", 2.0)
    _zadd_task(redis_sync, "bs2", "t-gone-2", 3.0)

    removed = queue_api.purge_blocked_tasks(redis_sync)  # type: ignore[arg-type]

    assert removed == 2
    assert redis_sync.zcard("wos:queue:bs1") == 1  # type: ignore[attr-defined]
    assert redis_sync.zcard("wos:queue:bs2") == 0  # type: ignore[attr-defined]


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
