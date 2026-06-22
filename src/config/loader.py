from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 — kept for the legacy ``path`` argument signature

from config._settings_data import SETTINGS as _BAKED_SETTINGS
from config.device_display import DeviceDisplayConfig, parse_device_display


@dataclass(frozen=True)
class InstanceConfig:
    instance_id: str
    bluestacks_window_title: str  # ADB serial (adb -s …)
    # Empty string = smart default (scrcpy for every device);
    # set explicitly via the dashboard to override.
    screenshot_backend: str = ""
    # Empty = smart default (scrcpy).
    input_backend: str = ""
    display: DeviceDisplayConfig | None = None
    # Game id from ``config.games.GAMES`` — drives package resolution for
    # foreground checks, launch, screen-graph scope, etc.
    game: str = "wos"


@dataclass(frozen=True)
class RedisConfig:
    url: str
    key_prefix: str = "wos"


@dataclass(frozen=True)
class OcrConfig:
    lang: str = "eng"
    tesseract_cmd: str = "tesseract"
    tessdata_dir: str = ""
    timeout_seconds: int = 10


@dataclass(frozen=True)
class InferenceConfig:
    """Object-detection inference sidecar (Roboflow inference server).

    Used by feature debuggers (e.g. the Fishing Tournament fish detector) that
    locate objects a fixed template can't. ``service_url`` points at the
    self-hosted ``roboflow-inference-server-cpu`` container; ``api_key`` is the
    Roboflow key the server uses to pull model weights on first request.
    """

    service_url: str = "http://127.0.0.1:9001"
    api_key: str = ""
    fish_model_id: str = "find-fish-ssnpa/6"
    confidence: float = 0.4
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: int = 30
    ortools_timeout_seconds: float = 1.0


@dataclass(frozen=True)
class WorkerConfig:
    health_check_interval_seconds: int = 15
    restart_wait_seconds: int = 10
    task_timeout_seconds: int = 300
    stuck_task_abort_seconds: int = 900
    """Runtime backstop: abort a task whose ``task.execute()`` has been running
    (e.g. blocked on an unattended click-approval) longer than this, regardless
    of source. ``0`` disables it. Kept above ``task_timeout_seconds`` so it only
    bites in approval mode (where ``asyncio.wait_for`` is skipped) or on a truly
    wedged task — see ``InstanceWorker._run_stuck_task_watchdog``."""
    game_foreground_timeout_seconds: int = 120
    """Max seconds at worker boot to wait for Whiteout foreground via ADB (``am``/``monkey``)."""
    overlay_analyze_when_busy: bool = False
    """If False, skip ``analyze.yaml`` overlay matching while a queue task is executing."""
    screen_detect_when_busy: bool = False
    """If False, skip screen-detect during the rolling tick while a task is executing.

    Running scenarios already know which screen they're on (they navigated to it),
    so the rolling background detect is mostly redundant overhead. The post-task
    overlay tick (``_overlay_tick_now``) takes a fresh frame and re-detects right
    after a task finishes, so we don't go long without a verdict.
    """
    device_reference_snapshot_interval_seconds: float = 2.0
    """How often to overwrite the rolling preview PNG."""
    wait_jitter_pct: float = 0.0
    """Random jitter applied to DSL ``wait:`` durations as a fraction (e.g. ``0.15`` = ±15%).

    Defaults to 0 (no jitter) so existing tests stay deterministic. Set globally in
    ``settings.yaml`` to make per-scenario pauses look less mechanical across instances.
    Only the explicit ``wait:`` step is jittered — ``long_click duration``, ``ttl``,
    and retry intervals stay exact.
    """
    adb_executable: str = ""
    """Explicit ``adb`` path when empty PATH differs from GUI (taps + screencap)."""
    adb_probe_host: str = "127.0.0.1"
    """Host whose TCP ports the /adb scan probes for emulator endpoints.

    The default is right whenever the API runs on the machine that hosts the
    emulators. In the prod compose, the API lives in a bridge-network container,
    so its loopback is not the host's — set ``WOS_ADB_PROBE_HOST`` to
    ``host.docker.internal`` (paired with ``ADB_SERVER_SOCKET`` pointing at the
    host's adb server) so the scan reaches host-bound emulator ports.
    """
    device_display: DeviceDisplayConfig | None = None
    """Default ADB display profile applied at worker boot (per-device overrides in devices.yaml)."""


@dataclass(frozen=True)
class Settings:
    redis: RedisConfig
    ocr: OcrConfig
    scheduler: SchedulerConfig
    worker: WorkerConfig
    instances: list[InstanceConfig]
    inference: InferenceConfig = InferenceConfig()


def _env_value(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _env_int(name: str) -> int | None:
    raw = _env_value(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_float(name: str) -> float | None:
    raw = _env_value(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_settings(path: Path | None = None) -> Settings:
    from config.env_loader import load_env_once

    load_env_once()
    # ``path`` was a YAML override; kept in the signature for backward compat
    # with tests but ignored. The defaults live in ``_settings_data``; env-var
    # overrides below remain the only runtime knob.
    del path  # documented unused
    raw = copy.deepcopy(_BAKED_SETTINGS)

    redis_raw = dict(raw.get("redis") or {})
    if redis_url := _env_value("WOS_REDIS_URL"):
        redis_raw["url"] = redis_url
    if redis_prefix := _env_value("WOS_REDIS_KEY_PREFIX"):
        redis_raw["key_prefix"] = redis_prefix

    ocr_raw = dict(raw.get("ocr") or {})
    # Backwards compatibility: older configs used a sidecar URL. Local
    # Tesseract OCR no longer needs it, so ignore the key if it is still present.
    ocr_raw.pop("url", None)
    if ocr_lang := _env_value("WOS_OCR_LANG"):
        ocr_raw["lang"] = ocr_lang
    if ocr_cmd := _env_value("WOS_TESSERACT_CMD"):
        ocr_raw["tesseract_cmd"] = ocr_cmd
    if tessdata_dir := _env_value("TESSDATA_PREFIX"):
        ocr_raw["tessdata_dir"] = tessdata_dir
    if (ocr_timeout := _env_int("WOS_OCR_TIMEOUT_SECONDS")) is not None:
        ocr_raw["timeout_seconds"] = ocr_timeout

    inference_raw = dict(raw.get("inference") or {})
    if inference_url := _env_value("WOS_INFERENCE_URL"):
        inference_raw["service_url"] = inference_url
    if roboflow_key := _env_value("ROBOFLOW_API_KEY"):
        inference_raw["api_key"] = roboflow_key
    if fish_model := _env_value("WOS_FISH_MODEL_ID"):
        inference_raw["fish_model_id"] = fish_model
    if (fish_conf := _env_float("WOS_FISH_CONFIDENCE")) is not None:
        inference_raw["confidence"] = fish_conf
    if (infer_timeout := _env_float("WOS_INFERENCE_TIMEOUT_SECONDS")) is not None:
        inference_raw["timeout_seconds"] = infer_timeout

    redis_cfg = RedisConfig(**redis_raw)
    ocr_cfg = OcrConfig(**ocr_raw)
    inference_cfg = InferenceConfig(**inference_raw)
    scheduler_cfg = SchedulerConfig(**(raw.get("scheduler") or {}))
    worker_raw = dict(raw.get("worker") or {})
    if adb_probe_host := _env_value("WOS_ADB_PROBE_HOST"):
        worker_raw["adb_probe_host"] = adb_probe_host
    device_display = parse_device_display(worker_raw.pop("device_display", None))
    worker_cfg = WorkerConfig(**worker_raw, device_display=device_display)

    # Each ``db/devices.yaml`` entry maps to one ``InstanceConfig``. Inline
    # import keeps ``config.devices`` out of the module-level cycle.
    from config.devices import load_devices as _load_devices

    devices_registry = _load_devices()
    instances = [
        InstanceConfig(
            instance_id=d.name,
            bluestacks_window_title=d.effective_serial,
            screenshot_backend=d.screenshot_backend,
            input_backend=d.input_backend,
            display=d.display,
            game=d.game,
        )
        for d in devices_registry.devices
        if d.name.strip()
    ]

    return Settings(
        redis=redis_cfg,
        ocr=ocr_cfg,
        scheduler=scheduler_cfg,
        worker=worker_cfg,
        instances=instances,
        inference=inference_cfg,
    )


_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Bind settings from Dishka bootstrap (or tests)."""
    global _settings
    _settings = settings


def reset_settings() -> None:
    """Clear cached settings (tests)."""
    global _settings
    _settings = None


def get_settings() -> Settings:
    """Return settings bound by :func:`set_settings` / Dishka bootstrap."""
    if _settings is None:
        msg = (
            "Settings are not initialized — call set_settings(load_settings()) "
            "or bootstrap_app_di() before get_settings()"
        )
        raise RuntimeError(
            msg
        )
    return _settings
