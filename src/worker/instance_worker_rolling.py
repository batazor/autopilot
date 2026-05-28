from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config.paths import repo_root
from config.reference_naming import rolling_preview_basename, temporal_png_abs_path
from config.tracing import screenshot_analysis_duration_histogram

logger = logging.getLogger(__name__)


def _runtime_error_is_adb_signal_exit(exc: BaseException) -> bool:
    """True when ADB died on a signal (Python reports ``exit -N``); common during Ctrl+C shutdown."""
    if not isinstance(exc, RuntimeError):
        return False
    return "ADB failed (exit -" in str(exc)


def _runtime_error_is_device_offline(exc: BaseException) -> bool:
    """True when ADB reports the device isn't currently connected.

    User-flow: emulator closed / USB unplugged / ``adb kill-server`` ran. We
    don't want the per-second rolling tick to log a fresh traceback every
    iteration — the cause is the operator's choice, not a fault.
    """
    if not isinstance(exc, RuntimeError):
        return False
    s = str(exc)
    return (
        "device '" in s and "' not found" in s
    ) or "device not found" in s or "no devices/emulators found" in s


def _rolling_snapshot_interval(cfg: Any) -> float:
    """Rolling preview cadence, independent of task busy state."""
    return float(cfg.device_reference_snapshot_interval_seconds)


def _rolling_should_skip_screen_detect(
    cfg: Any, *, task_busy: bool, navigating: bool = False
) -> bool:
    """Gate the screen detector during a busy task.

    ``navigating`` overrides the busy gate: while the scenario's pre-step
    navigation phase is in flight (``nav_target`` set in Redis), the FSM is
    actively hopping between screens and ``current_screen`` must keep being
    re-evaluated — otherwise a popup that appears mid-BFS (e.g. ``myriad_bazaar``)
    is invisible to overlay rules that gate on ``screens:``.
    """
    if navigating:
        return False
    return bool(task_busy) and not bool(cfg.screen_detect_when_busy)


def _rolling_should_skip_overlay(cfg: Any, *, task_busy: bool) -> bool:
    """Gate the overlay analyzer during a busy task."""
    return bool(task_busy) and not bool(cfg.overlay_analyze_when_busy)


def _rolling_overlay_device_level_only(
    *,
    active_player: str,
    cfg: Any,
    task_busy: bool,
) -> bool:
    """Whether rolling/overlay ticks should evaluate only ``device_level`` overlay rules.

    Pre-identity (empty ``active_player``): only device-level popups can run
    meaningfully — player-bound overlay rules have nothing to gate on yet.
    Busy default: same as ``_rolling_should_skip_overlay`` (device-level still runs).
    """
    if not str(active_player or "").strip():
        return True
    return _rolling_should_skip_overlay(cfg, task_busy=task_busy)


async def _read_navigating(instance_id: str, redis_async: Any | None) -> bool:
    """True while a DSL scenario is in its pre-step navigation phase.

    ``nav_target`` is set by ``_navigate_to_node`` before ``Navigator.navigate_to``
    and cleared after it returns. While that field is non-empty the FSM is
    actively hopping between screens — popup pre-empts (priority<10) need a fresh
    ``detect_screen`` every tick, not the sticky ``main_city`` carry-over.
    """
    if redis_async is None:
        return False
    try:
        from tasks.dsl_scenario_helpers import _read_instance_state_field

        value = await _read_instance_state_field(
            instance_id, "nav_target", redis_async
        )
        return bool(value.strip())
    except Exception:
        logger.debug(
            "rolling: failed to read nav_target for %s", instance_id, exc_info=True
        )
        return False


def _node_metric_value(node: str | None) -> str:
    node_s = (node or "").strip()
    return node_s or "unknown"


def _write_png_atomic(p: Path, img: Any) -> bool:
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
        tmp.replace(p)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _record_screenshot_analysis_duration(
    elapsed_s: float,
    *,
    node: str | None,
    source: str,
    device_level_only: bool,
    task_busy: bool,
    outcome: str,
) -> None:
    screenshot_analysis_duration_histogram().record(
        max(0.0, float(elapsed_s)),
        attributes={
            "node": _node_metric_value(node),
            "source": source,
            "device_level_only": bool(device_level_only),
            "task_busy": bool(task_busy),
            "outcome": outcome,
        },
    )



if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerRollingMixin(_Base):
    _cfg: Any
    _settings: Any
    _stopping: bool
    _ui_paused: bool
    _task_busy: Any
    _rolling_snap_seq: int
    _rolling_pool: Any
    _rolling_analyze_task: asyncio.Task[None] | None

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def _run_rolling_blocking(
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        """Run capture/PNG IO on a dedicated executor — never starve the UI preview."""
        if self._stopping:
            raise asyncio.CancelledError()
        import functools

        loop = asyncio.get_running_loop()
        if kwargs:
            target: Callable[..., Any] = functools.partial(fn, *args, **kwargs)
        elif args:
            target = functools.partial(fn, *args)
        else:
            target = fn
        return await loop.run_in_executor(self._rolling_pool, target)

    def _grab_layout_bgr(self) -> np.ndarray:
        raise NotImplementedError

    def _capture_and_write_rolling_preview(self, path: Path) -> np.ndarray | None:
        """Grab, publish to ``frame_bus``, and write PNG on the rolling thread.

        Keeps preview updates flowing when the asyncio loop is busy with sync
        overlay/navigation work — the write must not wait for a second executor
        trip on the main loop.
        """
        image_bgr = self._grab_layout_bgr()
        if not _write_png_atomic(path, image_bgr):
            logger.warning("[rolling] %s: PNG write failed %s", self._cfg.instance_id, path)
            return None
        return image_bgr

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        raise NotImplementedError

    async def _overlay_analyze_bgr(
        self,
        image_bgr: np.ndarray,
        *,
        current_screen_override: str | None = None,
        device_level_only: bool = False,
    ) -> None:
        raise NotImplementedError

    async def _maybe_enqueue_who_i_am_when_active_player_missing(self) -> None:
        raise NotImplementedError

    async def _run_rolling_snapshot_analysis(self, image_bgr: np.ndarray) -> None:
        """Screen detect + overlay on a captured frame (may run in background)."""
        from tasks.dsl_scenario_helpers import _read_active_player

        cfg = self._settings.worker
        task_busy = self._task_busy.is_set()
        active_player = await _read_active_player(self._cfg.instance_id, self._redis)
        navigating = task_busy and await _read_navigating(
            self._cfg.instance_id, self._redis
        )
        current_screen: str | None = None
        device_level_only = False
        outcome = "ok"
        analysis_started = time.monotonic()
        try:
            if _rolling_should_skip_screen_detect(
                cfg, task_busy=task_busy, navigating=navigating
            ):
                device_level_only = True
                await self._overlay_analyze_bgr(image_bgr, device_level_only=True)
                current_screen = getattr(self, "_last_current_screen", None)
                logger.debug(
                    "screen-detect-after-snapshot skipped; ran device-level overlay only"
                )
                return

            current_screen = await self._detect_current_screen_on_frame(image_bgr)

            device_level_only = _rolling_overlay_device_level_only(
                active_player=active_player,
                cfg=cfg,
                task_busy=task_busy,
            )
            if device_level_only and not str(active_player or "").strip():
                logger.debug(
                    "[rolling] %s: pre-identity — device-level overlay only (no active_player)",
                    self._cfg.instance_id,
                )
            elif device_level_only:
                logger.debug("overlay-after-snapshot skipped; ran device-level overlay only")
            await self._overlay_analyze_bgr(
                image_bgr,
                current_screen_override=current_screen,
                device_level_only=device_level_only,
            )
            if not _rolling_should_skip_overlay(cfg, task_busy=task_busy):
                await self._maybe_enqueue_who_i_am_when_active_player_missing()
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = "error"
            logger.exception(
                "[rolling] %s: snapshot analysis failed",
                self._cfg.instance_id,
            )
        finally:
            current_screen = current_screen or getattr(self, "_last_current_screen", None)
            _record_screenshot_analysis_duration(
                time.monotonic() - analysis_started,
                node=current_screen,
                source="rolling",
                device_level_only=device_level_only,
                task_busy=task_busy,
                outcome=outcome,
            )

    def _schedule_rolling_snapshot_analysis(self, image_bgr: np.ndarray) -> None:
        """Analyze the latest frame without blocking the next capture tick."""
        prev = self._rolling_analyze_task
        if prev is not None and not prev.done():
            logger.debug(
                "[rolling] %s: analysis skipped — prior tick still running",
                self._cfg.instance_id,
            )
            return

        async def _runner() -> None:
            try:
                await self._run_rolling_snapshot_analysis(image_bgr)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "[rolling] %s: background analysis task failed",
                    self._cfg.instance_id,
                )

        self._rolling_analyze_task = asyncio.create_task(
            _runner(),
            name=f"rolling-analyze-{self._cfg.instance_id}",
        )

    async def _device_reference_snapshot_tick(self, *, analyze: bool = True) -> None:
        """Screenshot → rolling preview PNG; optionally run screen/overlay analysis."""
        root = repo_root()
        path = temporal_png_abs_path(
            root, rolling_preview_basename(self._cfg.instance_id)
        )

        logger.debug(
            "[rolling] %s: screenshot (backend=%s serial=%s) → %s",
            self._cfg.instance_id,
            getattr(self._cfg, "screenshot_backend", "quartz"),
            self._cfg.bluestacks_window_title,
            path,
        )

        try:
            image_bgr = await self._run_rolling_blocking(
                self._capture_and_write_rolling_preview,
                path,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._stopping:
                logger.debug(
                    "[rolling] %s: screenshot skipped during shutdown",
                    self._cfg.instance_id,
                    exc_info=True,
                )
            elif _runtime_error_is_adb_signal_exit(e):
                logger.debug(
                    "[rolling] %s: screenshot aborted (ADB subprocess exited on a signal; "
                    "common when stopping with Ctrl+C)",
                    self._cfg.instance_id,
                    exc_info=True,
                )
            elif _runtime_error_is_device_offline(e):
                # Emulator off / disconnected — operator action, not a fault.
                # Log once per ~10 min so the operator stays aware without
                # drowning stdout in tracebacks at 1 Hz.
                import time as _t

                last = getattr(self, "_rolling_offline_logged_at", 0.0)
                now = _t.time()
                if now - last > 600.0:
                    logger.info(
                        "[rolling] %s: device offline (%s) — pausing capture",
                        self._cfg.instance_id,
                        self._cfg.bluestacks_window_title,
                    )
                    self._rolling_offline_logged_at = now
            else:
                logger.exception(
                    "[rolling] %s: screenshot failed (exception during capture)",
                    self._cfg.instance_id,
                )
            return

        if image_bgr is None:
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

        if not analyze:
            return

        self._schedule_rolling_snapshot_analysis(image_bgr)

    async def _overlay_tick_now(self, *, reason: str) -> None:
        """Take one screenshot and run overlay analysis immediately."""
        if self._stopping:
            return
        logger.info("[overlay] %s: running overlay tick (%s)", self._cfg.instance_id, reason)
        try:
            image_bgr = await self._run_rolling_blocking(self._grab_layout_bgr_cached)
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
        from tasks.dsl_scenario_helpers import _read_active_player

        cfg = self._settings.worker
        task_busy = self._task_busy.is_set()
        active_player = await _read_active_player(self._cfg.instance_id, self._redis)
        analysis_started = time.monotonic()
        current_screen: str | None = None
        device_level_only = _rolling_overlay_device_level_only(
            active_player=active_player,
            cfg=cfg,
            task_busy=task_busy,
        )
        outcome = "ok"
        try:
            current_screen = await self._detect_current_screen_on_frame(image_bgr)
            await self._overlay_analyze_bgr(
                image_bgr,
                current_screen_override=current_screen,
                device_level_only=device_level_only,
            )
            if not _rolling_should_skip_overlay(cfg, task_busy=task_busy):
                await self._maybe_enqueue_who_i_am_when_active_player_missing()
        except Exception:
            outcome = "error"
            logger.warning(
                "[overlay] %s: analysis failed — skipping overlay tick (%s)",
                self._cfg.instance_id,
                reason,
            )
        finally:
            current_screen = current_screen or getattr(self, "_last_current_screen", None)
            _record_screenshot_analysis_duration(
                time.monotonic() - analysis_started,
                node=current_screen,
                source="overlay_tick",
                device_level_only=device_level_only,
                task_busy=task_busy,
                outcome=outcome,
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
            _rolling_snapshot_interval(cfg),
        )
        while True:
            try:
                # Re-read worker config every iteration so a hot-edit of
                # ``settings.yaml`` propagates without a restart.
                cfg_now = self._settings.worker
                interval = _rolling_snapshot_interval(cfg_now)
                await asyncio.sleep(max(0.3, interval))
                if self._stopping:
                    return
                if self._ui_paused:
                    await self._device_reference_snapshot_tick(analyze=False)
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

