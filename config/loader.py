from __future__ import annotations

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
    url: str
    timeout_seconds: int = 10


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: int = 30
    ortools_timeout_seconds: float = 1.0


@dataclass(frozen=True)
class WorkerConfig:
    health_check_interval_seconds: int = 15
    restart_wait_seconds: int = 10
    task_timeout_seconds: int = 300
    bluestacks_launch_timeout_seconds: int = 120
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
    scheduler: SchedulerConfig
    worker: WorkerConfig
    instances: list[InstanceConfig]


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        path = Path(__file__).parent / "settings.yaml"
    raw = yaml.safe_load(path.read_text())

    redis_cfg = RedisConfig(**raw["redis"])
    ocr_cfg = OcrConfig(**raw["ocr"])
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
        scheduler=scheduler_cfg,
        worker=worker_cfg,
        instances=instances,
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = load_settings()
    return _settings
