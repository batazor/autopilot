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

# Deep-idle cap: no task and the same known screen for ``DEEP_IDLE_AFTER_S``
# (or the instance is operator-paused) — nothing consumes frames beyond the
# ~0.2–0.4 Hz rolling tick, yet the stream decodes (H.264 + YUV→BGR swscale)
# every frame it's allowed. Profiled on live workers: decode+convert was the
# main CPU of a *paused* worker, ~2% of a core each, summed across the fleet.
# The cap is restored to ``IDLE_SCRCPY_MAX_FPS`` the moment a task starts
# (``_run_one_queue_item``) so post-tap captures keep their fresh-frame budget.
DEEP_IDLE_SCRCPY_MAX_FPS = 2
DEEP_IDLE_AFTER_S = 60.0

# How many rolling ticks a frame-unchanged reuse window must span. The workers'
# phash skips (screen detect, overlay sweep, preview PNG) reuse the last result
# while the frame is visually unchanged, bounded by a "force a real one anyway"
# window so a sub-threshold change self-heals. Those windows were absolute
# constants (~4-5 s) chosen against the ~1 s base tick — but the idle backoff
# stretches the tick to 2.5 s and then 5.0 s, at which point the window is
# shorter than the gap between ticks and the skip *never* fires: every deep-idle
# tick paid a full detect + full overlay sweep, exactly the work the backoff
# existed to shed. Expressing the window in ticks keeps the original intent
# (~4 ticks of tolerated staleness) at every cadence.
FORCE_REFRESH_MIN_TICKS = 3

# Ceiling for the derived window, so a future slower tick can't stretch reuse
# without bound.
FORCE_REFRESH_MAX_S = 15.0


def force_refresh_window_s(base_window_s: float, tick_interval_s: float | None) -> float:
    """Reuse-window length for a phash skip running at ``tick_interval_s``.

    Never shorter than ``base_window_s`` (so the fast-tick behaviour is exactly
    what it was) and never longer than :data:`FORCE_REFRESH_MAX_S`.
    """
    if not tick_interval_s or tick_interval_s <= 0:
        return base_window_s
    return min(
        FORCE_REFRESH_MAX_S,
        max(base_window_s, FORCE_REFRESH_MIN_TICKS * tick_interval_s),
    )


def scrcpy_max_fps_for_capture_interval(
    capture_interval_override_s: float | None,
    *,
    deep_idle: bool = False,
) -> int:
    """scrcpy ``max_fps`` for the active scenario (0 = uncapped).

    A scenario whose module declares a faster ``capture_interval_ms`` (so
    ``capture_interval_override_s`` is set) needs frames as fast as the device
    produces them — stream uncapped. Otherwise cap to ``IDLE_SCRCPY_MAX_FPS``
    (or ``DEEP_IDLE_SCRCPY_MAX_FPS`` on a long-stable idle screen) so a static
    screen isn't decoded at the device's native frame rate.
    """
    if capture_interval_override_s:
        return 0
    return DEEP_IDLE_SCRCPY_MAX_FPS if deep_idle else IDLE_SCRCPY_MAX_FPS


@lru_cache(maxsize=8)
def _module_capture_ms_map(repo_root_s: str, game: str) -> dict[str, int]:
    """``{module_id: capture_interval_ms}`` for modules that declare it (>0).

    ``game`` is in the cache key, not read from process state inside the body.
    It used to be the latter: the key was ``repo_root`` alone while
    ``iter_module_dirs`` resolved the active catalog, so whichever catalog warmed
    the map served its answer to every later caller — a worker that bound
    ``wos_ru`` afterwards got wos's intervals, permanently, and there was no
    clear helper to recover.
    """
    out: dict[str, int] = {}
    for module_dir in iter_module_dirs(Path(repo_root_s), game=game or None):
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


def clear_capture_rate_cache() -> None:
    """Drop the per-catalog capture-interval map."""
    _module_capture_ms_map.cache_clear()


def module_capture_interval_ms(
    repo_root: Path, module_id: str | None, *, game: str | None = None
) -> int | None:
    """Declared ``capture_interval_ms`` for ``module_id`` (None when unset)."""
    if not module_id:
        return None
    from config.games import resolve_module_catalog

    catalog = resolve_module_catalog(game)
    return _module_capture_ms_map(str(repo_root.resolve()), catalog).get(module_id)


def capture_interval_s_for_scenario_key(
    repo_root: Path, scenario_key: str, *, game: str | None = None
) -> float | None:
    """Rolling-capture override (seconds) for the module owning ``scenario_key``.

    Returns None when the key is unresolved, coreless, or its module declares no
    ``capture_interval_ms`` — callers then keep the global interval.
    """
    if not (scenario_key or "").strip():
        return None
    from config.test_module import module_id_for_scenario_key

    ms = module_capture_interval_ms(
        repo_root, module_id_for_scenario_key(repo_root, scenario_key), game=game
    )
    return ms / 1000.0 if ms else None
