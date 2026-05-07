from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from analysis.overlay import parse_duration_seconds

logger = logging.getLogger(__name__)


class InstanceWorkerOverlayMixin:
    _cfg: Any
    _redis: Any
    _queue: Any
    _switcher: Any

    async def _schedule_overlay_matches(self, overlay_results: dict[str, object]) -> None:
        """Handle matched overlay rules.

        Policy: overlay analysis never enqueues tap actions. It may only enqueue DSL scenarios
        via `pushScenario` (and other non-tap metadata).
        """
        if not getattr(self._cfg, "player_ids", None) or self._queue is None:
            return

        active = await self._switcher.current_player(self._cfg.instance_id)  # type: ignore[union-attr]
        player_id = active if active else self._cfg.player_ids[0]
        is_main = bool(
            isinstance(overlay_results.get("main_city.visible"), dict)
            and overlay_results.get("main_city.visible", {}).get("matched")
        )
        if not is_main:
            return

        now = time.time()
        for _name, payload in overlay_results.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            try:
                await self._enqueue_push_scenarios_from_overlay(payload, player_id=player_id, run_at=now)
            except Exception:
                logger.debug("Failed to enqueue pushScenario task(s) from overlay", exc_info=True)

    async def _enqueue_push_scenarios_from_overlay(
        self,
        payload: dict[str, object],
        *,
        player_id: str,
        run_at: float,
    ) -> None:
        if self._queue is None:
            return

        pu = payload.get("pushScenario")
        if not isinstance(pu, list):
            # Backward compat
            pu = payload.get("pushUsecase")

        if isinstance(pu, list):
            for item in pu:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("name") or item.get("type") or "").strip()
                if not t:
                    continue
                pr_raw = item.get("priority")
                pr = int(pr_raw) if pr_raw is not None else 80_000

                ttl_raw = item.get("ttl")
                if ttl_raw is None:
                    ttl_raw = item.get("ttl_seconds")
                ttl = parse_duration_seconds(ttl_raw)
                if ttl and self._redis is not None:
                    guard_key = f"wos:overlay:push_ttl:{self._cfg.instance_id}:{player_id}:{t}"
                    ok = await self._redis.set(guard_key, "1", ex=int(ttl), nx=True)
                    if not ok:
                        continue

                await self._queue.schedule(
                    task_id=f"ovl:{self._cfg.instance_id}:{t}:{uuid.uuid4().hex[:8]}",
                    player_id=player_id,
                    task_type=t,
                    priority=pr,
                    run_at=run_at,
                    instance_id=self._cfg.instance_id,
                    skip_if_duplicate=True,
                )
            return

        push_t = str(payload.get("push_task_type") or "").strip()
        if not push_t:
            return
        pr_raw = payload.get("push_task_priority")
        pr = int(pr_raw) if pr_raw is not None else 80_000
        await self._queue.schedule(
            task_id=f"ovl:{self._cfg.instance_id}:{push_t}:{uuid.uuid4().hex[:8]}",
            player_id=player_id,
            task_type=push_t,
            priority=pr,
            run_at=run_at,
            instance_id=self._cfg.instance_id,
            skip_if_duplicate=True,
        )

