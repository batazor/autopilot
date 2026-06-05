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
