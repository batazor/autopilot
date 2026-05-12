from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TaskConfig:
    cooldown_seconds: int
    max_attempts: int
    timeout_seconds: int


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
    fuzzy_threshold: float = 0.80


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
    device_reference_snapshot_interval_seconds: float = 2.0
    """How often to overwrite the rolling preview PNG and run overlay rules on that frame."""
    adb_executable: str = ""
    """Explicit ``adb`` path when empty PATH differs from GUI (taps + screencap)."""


@dataclass(frozen=True)
class Settings:
    redis: RedisConfig
    ocr: OcrConfig
    scheduler: SchedulerConfig
    worker: WorkerConfig
    instances: list[InstanceConfig]
    tasks: dict[str, TaskConfig]


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
    tasks = {tid: TaskConfig(**tcfg) for tid, tcfg in raw["tasks"].items()}

    return Settings(
        redis=redis_cfg,
        ocr=ocr_cfg,
        scheduler=scheduler_cfg,
        worker=worker_cfg,
        instances=instances,
        tasks=tasks,
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = load_settings()
    return _settings
