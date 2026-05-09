from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

from config.reference_naming import reference_file_basename, reference_png_abs_path

logger = logging.getLogger(__name__)


class InstanceWorkerRollingMixin:
    _cfg: Any
    _settings: Any
    _stopping: bool
    _ui_paused: bool
    _task_busy: Any
    _rolling_snap_seq: int

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _grab_layout_bgr(self) -> np.ndarray:
        raise NotImplementedError

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        raise NotImplementedError

    async def _overlay_analyze_bgr(
        self, image_bgr: np.ndarray, *, current_screen_override: str | None = None
    ) -> None:
        raise NotImplementedError

    async def _device_reference_snapshot_tick(self) -> None:
        """ADB screencap → rolling preview PNG + overlay rules (same frame)."""
        repo_root = Path(__file__).resolve().parent.parent
        (repo_root / "references").mkdir(parents=True, exist_ok=True)
        base = reference_file_basename(None, self._cfg.instance_id)
        path = reference_png_abs_path(repo_root, base, self._cfg.instance_id)

        logger.debug(
            "[rolling] %s: ADB screencap (serial=%s) → %s",
            self._cfg.instance_id,
            self._cfg.bluestacks_window_title,
            path,
        )

        try:
            image_bgr = await self._run_blocking(self._grab_layout_bgr)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._stopping:
                logger.debug(
                    "[rolling] %s: screenshot skipped during shutdown",
                    self._cfg.instance_id,
                    exc_info=True,
                )
            else:
                logger.exception(
                    "[rolling] %s: screenshot failed (exception during capture)",
                    self._cfg.instance_id,
                )
            return

        def _write_png_atomic(p: Path, img: np.ndarray) -> bool:
            """Write to a temp file in the same dir, then ``os.replace`` (atomic on macOS/Linux)."""
            import cv2

            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".rolling-", suffix=".png", dir=p.parent)
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                if not cv2.imwrite(str(tmp), img):
                    tmp.unlink(missing_ok=True)
                    return False
                os.replace(tmp, p)
                return True
            except OSError:
                tmp.unlink(missing_ok=True)
                raise

        if not await self._run_blocking(_write_png_atomic, path, image_bgr):
            logger.warning("[rolling] %s: PNG write failed %s", self._cfg.instance_id, path)
            return

        self._rolling_snap_seq += 1
        h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
        logger.debug(
            "[rolling] %s: saved screenshot %s (%d×%d), tick #%d",
            self._cfg.instance_id,
            path,
            w,
            h,
            self._rolling_snap_seq,
        )

        current_screen = await self._detect_current_screen_on_frame(image_bgr)

        cfg = self._settings.worker
        overlay_skipped_busy = not cfg.overlay_analyze_when_busy and self._task_busy.is_set()
        if overlay_skipped_busy:
            logger.debug("overlay-after-snapshot skipped (task busy, overlay_analyze_when_busy=false)")
            return
        await self._overlay_analyze_bgr(image_bgr, current_screen_override=current_screen)

    async def _overlay_tick_now(self, *, reason: str) -> None:
        """Take one screenshot and run overlay analysis immediately."""
        if self._stopping:
            return
        logger.info("[overlay] %s: running overlay tick (%s)", self._cfg.instance_id, reason)
        try:
            image_bgr = await self._run_blocking(self._grab_layout_bgr)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._stopping:
                logger.debug(
                    "[overlay] %s: screenshot skipped during shutdown (%s)",
                    self._cfg.instance_id,
                    reason,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "[overlay] %s: screenshot failed — skipping overlay tick (%s)",
                    self._cfg.instance_id,
                    reason,
                )
            return
        try:
            current_screen = await self._detect_current_screen_on_frame(image_bgr)
            await self._overlay_analyze_bgr(image_bgr, current_screen_override=current_screen)
        except Exception:
            logger.warning(
                "[overlay] %s: analysis failed — skipping overlay tick (%s)",
                self._cfg.instance_id,
                reason,
            )

    async def _startup_overlay_tick(self) -> None:
        """Run overlay analysis immediately at startup."""
        await self._overlay_tick_now(reason="startup")

    async def _device_reference_snapshot_loop(self) -> None:
        cfg = self._settings.worker
        await asyncio.sleep(0.5)
        logger.info(
            "[rolling] %s: snapshot loop started (interval=%.2fs)",
            self._cfg.instance_id,
            float(cfg.device_reference_snapshot_interval_seconds),
        )
        while True:
            try:
                interval = cfg.device_reference_snapshot_interval_seconds
                await asyncio.sleep(max(0.3, float(interval)))
                if self._stopping:
                    return
                if self._ui_paused:
                    continue
                await self._device_reference_snapshot_tick()
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                blocking_executor_live = bool(getattr(self, "_blocking_executor_live", True))
                if not blocking_executor_live:
                    raise asyncio.CancelledError() from exc
                logger.exception("device_reference_snapshot_loop error on %s", self._cfg.instance_id)
            except Exception:
                logger.exception("device_reference_snapshot_loop error on %s", self._cfg.instance_id)

