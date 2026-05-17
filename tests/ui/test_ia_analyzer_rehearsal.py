from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from config.loader import get_settings
from config.paths import repo_root
from scheduler.queue import RedisQueue
from ui import ia_overlay_executor
from ui.ia_queue_executor import _pop_ia_item


@pytest.mark.asyncio
async def test_ia_executor_consumes_overlay_dsl_task_and_resolves_active_player(
    redis_async: object,
) -> None:
    redis = redis_async
    settings = get_settings()
    queue = RedisQueue(redis, settings)  # type: ignore[arg-type]
    await redis.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "765502864"},
    )
    payload = json.dumps(
        {
            "task_id": "ovl:bs1:claim_trials.1:abc12345",
            "player_id": "",
            "task_type": "claim_trials.1",
            "priority": 80_000,
            "run_at": time.time() - 1,
            "instance_id": "bs1",
            "region": "trial.day.1",
        },
        ensure_ascii=False,
    )
    await redis.zadd("wos:queue:bs1", {payload: time.time() - 1})  # type: ignore[attr-defined]

    item = await _pop_ia_item(redis, queue, instance_id="bs1", repo_root=repo_root())  # type: ignore[arg-type]

    assert item is not None
    assert item.task_id == "ovl:bs1:claim_trials.1:abc12345"
    assert item.task_type == "claim_trials.1"
    assert item.player_id == "765502864"
    assert await redis.zrange("wos:queue:bs1", 0, -1) == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_module_scoped_ia_analyzer_pushes_trials_overlay_task(
    redis_async: object,
    mocker,
) -> None:
    redis = redis_async
    settings = get_settings()
    queue = RedisQueue(redis, settings)  # type: ignore[arg-type]
    root = repo_root()
    fixture = root / "modules" / "events" / "trials" / "references" / "page.trials.png"
    assert fixture.is_file()
    mocker.patch.object(
        ia_overlay_executor,
        "rolling_live_preview_path",
        return_value=Path(fixture),
    )
    await redis.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "event.trials.day.1",
            "active_player": "765502864",
        },
    )
    pusher = ia_overlay_executor._OverlayPusher()

    await ia_overlay_executor._analyze_instance(
        redis,  # type: ignore[arg-type]
        queue,
        pusher,
        instance_id="bs1",
        scope="events/trials",
        rule_eval_state={},
    )

    rows = await redis.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    docs = [json.loads(row) for row in rows]
    assert [doc["task_type"] for doc in docs] == ["claim_trials.1"]
    assert docs[0]["task_id"].startswith("ovl:bs1:claim_trials.1:")
    assert docs[0]["player_id"] == ""
    assert docs[0]["region"] == "trial.day.1"
    status_raw = await redis.get(ia_overlay_executor.analyzer_status_key("bs1"))  # type: ignore[attr-defined]
    status = json.loads(status_raw)
    assert status["scope"] == "events/trials"
    assert status["matched"][0]["rule"] == "trials.day.1.has_red_dot"
    assert status["pushed"][0]["task_type"] == "claim_trials.1"
