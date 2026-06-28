"""Per-module screenshot (rolling-capture) rate overrides.

The worker's rolling loop captures a frame every
``device_reference_snapshot_interval_seconds`` (global, ~1 Hz). Fast-reaction
modules — fishing, dreamscape-memory — need new frames much more often while
they're solving. A module opts in by declaring ``capture_interval_ms`` in its
``module.yaml``:

    id: fishing_tournament
    capture_interval_ms: 100      # ~10 fps while a fishing scenario runs

The override is keyed to the *running scenario*'s owning module: while the
worker executes a scenario from a module that declares ``capture_interval_ms``,
the rolling loop ticks at that rate; otherwise it falls back to the global
interval. Resolution is cached (module layout is static at runtime).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from config.module_discovery import iter_module_dirs, load_module_yaml, module_meta_id

# Hard floor for any capture cadence (~20 fps). Guards ADB/CPU against a typo'd
# ``capture_interval_ms: 1`` hammering the device.
MIN_CAPTURE_INTERVAL_S = 0.05

# scrcpy stream frame-rate cap (fps) during normal autopilot. The overlay only
# samples ~2 fps and post-tap captures need a fresh frame within ~125 ms, so a
# low cap keeps H.264 decode cheap without hurting responsiveness. Fast modules
# that declare ``capture_interval_ms`` (fishing, dreamscape) stream uncapped.
IDLE_SCRCPY_MAX_FPS = 8


def scrcpy_max_fps_for_capture_interval(
    capture_interval_override_s: float | None,
) -> int:
    """scrcpy ``max_fps`` for the active scenario (0 = uncapped).

    A scenario whose module declares a faster ``capture_interval_ms`` (so
    ``capture_interval_override_s`` is set) needs frames as fast as the device
    produces them — stream uncapped. Otherwise cap to ``IDLE_SCRCPY_MAX_FPS`` so
    a static screen isn't decoded at the device's native frame rate.
    """
    return 0 if capture_interval_override_s else IDLE_SCRCPY_MAX_FPS


@lru_cache(maxsize=8)
def _module_capture_ms_map(repo_root_s: str) -> dict[str, int]:
    """``{module_id: capture_interval_ms}`` for modules that declare it (>0)."""
    out: dict[str, int] = {}
    for module_dir in iter_module_dirs(Path(repo_root_s)):
        raw = load_module_yaml(module_dir).get("capture_interval_ms")
        if raw is None:
            continue
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            continue
        if ms > 0:
            out[module_meta_id(module_dir)] = ms
    return out


def module_capture_interval_ms(repo_root: Path, module_id: str | None) -> int | None:
    """Declared ``capture_interval_ms`` for ``module_id`` (None when unset)."""
    if not module_id:
        return None
    return _module_capture_ms_map(str(repo_root.resolve())).get(module_id)


def capture_interval_s_for_scenario_key(
    repo_root: Path, scenario_key: str
) -> float | None:
    """Rolling-capture override (seconds) for the module owning ``scenario_key``.

    Returns None when the key is unresolved, coreless, or its module declares no
    ``capture_interval_ms`` — callers then keep the global interval.
    """
    if not (scenario_key or "").strip():
        return None
    from config.test_module import module_id_for_scenario_key

    ms = module_capture_interval_ms(
        repo_root, module_id_for_scenario_key(repo_root, scenario_key)
    )
    return ms / 1000.0 if ms else None
