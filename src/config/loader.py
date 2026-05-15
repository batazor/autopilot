from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class InstanceConfig:
    instance_id: str
    bluestacks_window_title: str  # ADB serial (adb -s …)


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
class OmniparserConfig:
    """Optional screen-parser sidecar for labeling auto-detect (microsoft/OmniParser)."""

    url: str = ""
    timeout_seconds: int = 120


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: int = 30
    ortools_timeout_seconds: float = 1.0


@dataclass(frozen=True)
class WorkerConfig:
    health_check_interval_seconds: int = 15
    restart_wait_seconds: int = 10
    task_timeout_seconds: int = 300
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
    """How often to overwrite the rolling preview PNG and run overlay rules on that frame."""
    device_reference_snapshot_busy_interval_seconds: float = 5.0
    """Rolling preview cadence while a task is busy. Longer than the idle cadence
    because the preview's only consumer during a task is the UI watcher, and
    overlay/detect are typically gated off (see ``overlay_analyze_when_busy`` /
    ``screen_detect_when_busy``). Setting equal to the idle interval restores
    the historical "always at idle cadence" behavior."""
    adb_executable: str = ""
    """Explicit ``adb`` path when empty PATH differs from GUI (taps + screencap)."""


@dataclass(frozen=True)
class Settings:
    redis: RedisConfig
    ocr: OcrConfig
    omniparser: OmniparserConfig
    scheduler: SchedulerConfig
    worker: WorkerConfig
    instances: list[InstanceConfig]


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


def load_settings(path: Path | None = None) -> Settings:
    from config.env_loader import load_env_once

    load_env_once()
    if path is None:
        path = Path(__file__).parent / "settings.yaml"
    raw = yaml.safe_load(path.read_text())

    redis_raw = dict(raw["redis"])
    if redis_url := _env_value("WOS_REDIS_URL"):
        redis_raw["url"] = redis_url
    if redis_prefix := _env_value("WOS_REDIS_KEY_PREFIX"):
        redis_raw["key_prefix"] = redis_prefix

    ocr_raw = dict(raw["ocr"])
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

    omniparser_raw = dict(raw.get("omniparser") or {})
    if omniparser_url := _env_value("OMNIPARSER_URL"):
        omniparser_raw["url"] = omniparser_url
    if (omniparser_timeout := _env_int("OMNIPARSER_TIMEOUT_SECONDS")) is not None:
        omniparser_raw["timeout_seconds"] = omniparser_timeout

    redis_cfg = RedisConfig(**redis_raw)
    ocr_cfg = OcrConfig(**ocr_raw)
    omniparser_cfg = OmniparserConfig(**omniparser_raw)
    scheduler_cfg = SchedulerConfig(**raw["scheduler"])
    worker_cfg = WorkerConfig(**raw.get("worker", {}))

    # Each ``db/devices.yaml`` entry maps to one ``InstanceConfig``. Inline
    # import keeps ``config.devices`` out of the module-level cycle.
    from config.devices import load_devices as _load_devices

    devices_registry = _load_devices()
    instances = [
        InstanceConfig(
            instance_id=d.name,
            bluestacks_window_title=d.effective_serial,
        )
        for d in devices_registry.devices
        if d.name.strip()
    ]

    return Settings(
        redis=redis_cfg,
        ocr=ocr_cfg,
        omniparser=omniparser_cfg,
        scheduler=scheduler_cfg,
        worker=worker_cfg,
        instances=instances,
    )


_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Bind settings from Dishka bootstrap (or tests)."""
    global _settings  # noqa: PLW0603
    _settings = settings


def reset_settings() -> None:
    """Clear cached settings (tests)."""
    global _settings  # noqa: PLW0603
    _settings = None


def get_settings() -> Settings:
    """Return settings bound by :func:`set_settings` / Dishka bootstrap."""
    if _settings is None:
        raise RuntimeError(
            "Settings are not initialized — call set_settings(load_settings()) "
            "or bootstrap_app_di() before get_settings()"
        )
    return _settings
