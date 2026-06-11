"""Worker task: full kingdom radar scan (scan → stitch → tiles) on this device.

Enqueued by ``POST /api/radar/scan`` as ``task_type="radar_scan"``. The run id
travels via the ``radar:scan_active`` Redis key (set by the API at enqueue
time, maintained by the scanner) — registered task factories only receive
``(task_id, player_id, priority, redis_client)``, so the key is the handoff
point rather than the queue payload.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tasks.base import TaskResult

logger = logging.getLogger(__name__)

TASK_TYPE = "radar_scan"


@dataclass
class RadarScanTask:
    task_id: str
    player_id: str
    priority: int
    redis_client: Any = None
    task_type: str = TASK_TYPE
    cooldown_seconds: int = 0
    is_cooperative: bool = False

    def estimate_duration(self) -> int:
        return 600

    async def execute(self, instance_id: str) -> TaskResult:
        # The scan is synchronous (deliberate sleeps between taps); it owns the
        # device for its whole duration, exactly like a long DSL scenario.
        return await asyncio.to_thread(self._run_blocking, instance_id)

    def _run_blocking(self, instance_id: str) -> TaskResult:
        import redis

        from config.loader import load_settings
        from modules.radar.config import default_config_path, runs_root
        from modules.radar.events import RadarEventPublisher, read_active
        from modules.radar.scanner import run_scan
        from modules.radar.stitch import run_stitch
        from modules.radar.tiles import generate_tiles

        settings = load_settings()
        # Own sync client: events + active-key writes happen from this worker
        # thread; the task's injected client is the worker's async one.
        client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
        try:
            active = read_active(client)
            run_id = str((active or {}).get("run_id") or "").strip()
            if not run_id:
                run_id = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M")
            serial = next(
                (
                    inst.bluestacks_window_title
                    for inst in settings.instances
                    if inst.instance_id == instance_id
                ),
                None,
            )
            out_dir = runs_root() / run_id
            publisher = RadarEventPublisher(client, run_id)
            try:
                run_scan(
                    default_config_path(),
                    out_dir,
                    serial=serial,
                    adb_bin=settings.worker.adb_executable or "adb",
                    events=publisher,
                )
            except Exception as exc:
                # run_scan already published scan_failed and cleared the key.
                logger.exception("radar scan failed (run %s)", run_id)
                return TaskResult(success=False, metadata={"run_id": run_id, "reason": str(exc)})

            try:
                run_stitch(out_dir)
                generate_tiles(out_dir)
                publisher.tiles_ready()
            except Exception:
                # Frames + manifest are safe on disk; tiles can be rebuilt
                # from the UI, so a stitch failure doesn't fail the task.
                logger.exception("radar stitch/tiles failed (run %s)", run_id)
            return TaskResult(success=True, metadata={"run_id": run_id})
        finally:
            try:
                client.close()
            except Exception:
                logger.debug("radar: redis client close failed", exc_info=True)
