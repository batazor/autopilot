from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from capture.adb_screencap import adb_screencap_to_file
from config.reference_naming import reference_file_basename, reference_png_abs_path

logger = logging.getLogger(__name__)


class InstanceWorkerUiMixin:
    _cfg: Any
    _redis: aioredis.Redis | None
    _queue: Any
    _switcher: Any
    _ui_paused: bool

    def _worker_adb_bin(self) -> str:  # provided by InstanceWorker
        raise NotImplementedError

    async def _push_ui_screenshot(self, reference_name: str | None = None) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        (repo_root / "references").mkdir(parents=True, exist_ok=True)
        base = reference_file_basename(reference_name, self._cfg.instance_id)
        path = reference_png_abs_path(repo_root, base, self._cfg.instance_id)
        ok, msg = adb_screencap_to_file(
            path,
            adb_bin=self._worker_adb_bin(),
            serial=self._cfg.bluestacks_window_title,
        )
        if ok:
            logger.debug("[ui] %s: saved screenshot %s", self._cfg.instance_id, path)
            return
        logger.error("[ui] %s: screenshot failed: %s", self._cfg.instance_id, msg)
        raise RuntimeError(msg)

    async def _schedule_manual_task(self, player_id: str, task_type: str) -> None:
        if self._queue is None:
            return
        task_id = f"ui:{player_id}:{task_type}:{uuid.uuid4().hex[:8]}"
        await self._queue.schedule(
            task_id=task_id,
            player_id=player_id,
            task_type=task_type,
            priority=10_000,
            run_at=time.time(),
            instance_id=self._cfg.instance_id,
        )
        logger.info("Manual task queued: %s %s", task_type, player_id)

    async def _handle_ui_command(self, raw: str | bytes) -> None:
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            data: dict[str, object] = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Invalid UI command JSON")
            return

        cmd = str(data.get("cmd", ""))
        inst_key = f"wos:instance:{self._cfg.instance_id}:state"

        match cmd:
            case "pause":
                self._ui_paused = True
                await self._redis.hset(inst_key, "paused", "1")  # type: ignore[union-attr]
                logger.info("UI pause enabled for %s", self._cfg.instance_id)
            case "resume":
                self._ui_paused = False
                await self._redis.hset(inst_key, "paused", "0")  # type: ignore[union-attr]
                logger.info("UI pause cleared for %s", self._cfg.instance_id)
            case "screenshot":
                try:
                    ref = data.get("name")
                    if ref is None:
                        ref = data.get("reference_name")
                    ref_s = str(ref).strip() if ref is not None else None
                    if ref_s == "":
                        ref_s = None
                    await self._push_ui_screenshot(reference_name=ref_s)
                except Exception:
                    logger.exception("UI screenshot failed")
            case "switch_player":
                pid = str(data.get("player_id", ""))
                if pid:
                    await self._switcher.switch_to(pid, self._cfg.instance_id)  # type: ignore[union-attr]
            case "run_task":
                pid = str(data.get("player_id", ""))
                ttype = str(data.get("task_type", ""))
                if pid and ttype:
                    await self._schedule_manual_task(pid, ttype)
            case "restart":
                await self._restart_instance()  # provided by InstanceWorker
            case "wake":
                # Used by debug UI / tooling to interrupt idle wait so queued work is picked up.
                pass
            case _:
                logger.warning("Unknown UI command: %s", cmd)

    async def _drain_ui_commands(self) -> None:
        if self._redis is None:
            return
        key = f"wos:ui:command:{self._cfg.instance_id}"
        while True:
            raw = await self._redis.rpop(key)
            if raw is None:
                break
            await self._handle_ui_command(raw)

