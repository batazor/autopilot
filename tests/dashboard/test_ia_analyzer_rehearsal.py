from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from config.loader import get_settings
from config.paths import repo_root
from dashboard import ia_overlay_executor, ia_preview_service
from dashboard.ia_queue_executor import _pop_ia_item
from scheduler.queue import RedisQueue


class _SyncRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hset(
        self,
        key: str,
        field: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> int:
        row = self.hashes.setdefault(key, {})
        if mapping is not None:
            row.update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
        if field is not None:
            row[str(field)] = str(value or "")
            return 1
        return 0


def test_ia_preview_bootstrap_sets_active_player_from_device_registry(mocker) -> None:
    client = _SyncRedis()
    mocker.patch.object(
        ia_preview_service,
        "player_ids_for_device_candidates",
        return_value=["765502864"],
    )

    player_id = ia_preview_service._ensure_active_player_for_instance(
        client,  # type: ignore[arg-type]
        instance_id="bs1",
        adb_serial="127.0.0.1:5555",
    )

    assert player_id == "765502864"
    assert client.hashes["wos:instance:bs1:state"]["active_player"] == "765502864"
    assert client.hashes["wos:instance:bs1:state"]["active_player_at"]


def test_ia_preview_bootstrap_preserves_existing_active_player(mocker) -> None:
    client = _SyncRedis()
    client.hset("wos:instance:bs1:state", mapping={"active_player": "401227964"})
    candidates = mocker.patch.object(
        ia_preview_service,
        "player_ids_for_device_candidates",
        return_value=["765502864"],
    )

    player_id = ia_preview_service._ensure_active_player_for_instance(
        client,  # type: ignore[arg-type]
        instance_id="bs1",
        adb_serial="127.0.0.1:5555",
    )

    assert player_id == "401227964"
    assert client.hashes["wos:instance:bs1:state"] == {"active_player": "401227964"}
    candidates.assert_not_called()


@pytest.mark.asyncio
async def test_ia_executor_consumes_overlay_dsl_task_and_resolves_active_player(
    redis_async: object,
) -> None:
    redis = redis_async
    settings = get_settings()
    queue = RedisQueue(redis, settings)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    await redis.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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
    await redis.zadd("wos:queue:bs1", {payload: time.time() - 1})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    item = await _pop_ia_item(redis, queue, instance_id="bs1", repo_root=repo_root())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    assert item is not None
    assert item.task_id == "ovl:bs1:claim_trials.1:abc12345"
    assert item.task_type == "claim_trials.1"
    assert item.player_id == "765502864"
    assert await redis.zrange("wos:queue:bs1", 0, -1) == []  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_module_scoped_ia_analyzer_pushes_trials_overlay_task(
    redis_async: object,
    mocker,
) -> None:
    redis = redis_async
    settings = get_settings()
    queue = RedisQueue(redis, settings)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    root = repo_root()
    fixture = root / "games" / "wos" / "events" / "trials" / "references" / "page.trials.png"
    assert fixture.is_file()
    mocker.patch.object(
        ia_overlay_executor,
        "rolling_live_preview_path",
        return_value=Path(fixture),
    )
    await redis.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "event.trials.day.1",
            "active_player": "765502864",
        },
    )
    pusher = ia_overlay_executor._OverlayPusher()

    await ia_overlay_executor._analyze_instance(
        redis,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        queue,
        pusher,
        instance_id="bs1",
        scope="events/trials",
        rule_eval_state={},
    )

    rows = await redis.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    docs = [json.loads(row) for row in rows]
    assert [doc["task_type"] for doc in docs] == ["claim_trials.1"]
    assert docs[0]["task_id"].startswith("ovl:bs1:claim_trials.1:")
    assert docs[0]["player_id"] == ""
    assert docs[0]["region"] == "trial.day.1"
    status_raw = await redis.get(ia_overlay_executor.analyzer_status_key("bs1"))  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    status = json.loads(status_raw)
    assert status["scope"] == "events/trials"
    assert status["matched"][0]["rule"] == "trials.day.1.has_red_dot"
    assert status["pushed"][0]["task_type"] == "claim_trials.1"
