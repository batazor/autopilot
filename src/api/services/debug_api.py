"""Debug scenario runner API."""
from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from config.loader import load_settings
from config.paths import repo_root
from dashboard.redis_client import bump_dsl_preempt_generation, push_instance_command
from dsl import template_resolver as _tmpl

_REPO = repo_root()


def _slug(key: str) -> str:
    return key.replace("/", "_").replace(".", "_")


def run_scenario(
    *,
    instance_id: str,
    scenario_key: str,
    player_id: str = "",
    priority: int = 0,
    start_step_index: int = 0,
) -> dict[str, Any]:
    if _tmpl.resolve(_REPO, scenario_key) is None:
        msg = f"unknown scenario: {scenario_key}"
        raise KeyError(msg)
    settings = load_settings()
    if not any(i.instance_id == instance_id for i in settings.instances):
        msg = f"unknown instance: {instance_id}"
        raise KeyError(msg)

    from api.deps import get_redis

    client = get_redis()
    now = time.time()
    task_id = f"ui:debug:{instance_id}:{_slug(scenario_key)}:{int(now)}"
    payload: dict[str, Any] = {
        "task_id": task_id,
        "player_id": player_id,
        "task_type": scenario_key,
        "priority": int(priority),
        "run_at": float(now),
        "instance_id": instance_id,
        "debug": True,
        "source": "api.debug",
    }
    if start_step_index > 0:
        payload["start_step_index"] = int(start_step_index)
    with suppress(Exception):
        bump_dsl_preempt_generation(client, instance_id)
    client.zadd(
        f"wos:queue:{instance_id}",
        {json.dumps(payload, ensure_ascii=False): float(now)},
    )
    pid = str(player_id or "").strip()
    if pid:
        client.hset(
            f"wos:instance:{instance_id}:state",
            mapping={
                "active_player": pid,
                "active_player_at": str(now),
            },
        )
    push_instance_command(client, instance_id, {"cmd": "wake"})
    return {
        "ok": True,
        "task_id": task_id,
        "instance_id": instance_id,
        "scenario": scenario_key,
        "player_id": player_id or None,
    }
