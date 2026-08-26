"""Per-module rolling-capture rate overrides (config/capture_rate.py)."""
from __future__ import annotations

from config.capture_rate import (
    DEEP_IDLE_AFTER_S,
    DEEP_IDLE_SCRCPY_MAX_FPS,
    FORCE_REFRESH_MAX_S,
    IDLE_SCRCPY_MAX_FPS,
    MIN_CAPTURE_INTERVAL_S,
    capture_interval_s_for_scenario_key,
    force_refresh_window_s,
    module_capture_interval_ms,
    scrcpy_max_fps_for_capture_interval,
)
from config.paths import repo_root


def test_scrcpy_fps_uncapped_for_fast_scenario() -> None:
    # A scenario with a capture override (fishing) streams uncapped (0),
    # regardless of any (stale) deep-idle verdict.
    assert scrcpy_max_fps_for_capture_interval(0.1) == 0
    assert scrcpy_max_fps_for_capture_interval(0.1, deep_idle=True) == 0


def test_scrcpy_fps_idle_cap_for_normal_scenario() -> None:
    # No override (normal autopilot / idle) → low cap.
    assert scrcpy_max_fps_for_capture_interval(None) == IDLE_SCRCPY_MAX_FPS
    assert IDLE_SCRCPY_MAX_FPS > 0


def test_scrcpy_fps_deep_idle_cap() -> None:
    # Long-stable idle screen / paused instance → deepest cap; still >0 so the
    # preview keepalive and a task's first capture have frames to grab.
    assert scrcpy_max_fps_for_capture_interval(None, deep_idle=True) == DEEP_IDLE_SCRCPY_MAX_FPS
    assert 0 < DEEP_IDLE_SCRCPY_MAX_FPS < IDLE_SCRCPY_MAX_FPS
    assert DEEP_IDLE_AFTER_S > 0


def test_fast_modules_declare_capture_interval() -> None:
    r = repo_root()
    # Fishing + dreamscape opt into ~10 fps via module.yaml.
    assert module_capture_interval_ms(r, "dreamscape_memory") == 100
    assert module_capture_interval_ms(r, "fishing_tournament") == 100


def test_module_without_override_is_none() -> None:
    r = repo_root()
    assert module_capture_interval_ms(r, "heroes") is None
    # Unknown / empty ids never override.
    assert module_capture_interval_ms(r, "does_not_exist") is None
    assert module_capture_interval_ms(r, None) is None


def test_scenario_key_resolves_to_owning_module_interval() -> None:
    r = repo_root()
    # A scenario from a fast module resolves to that module's seconds value.
    assert capture_interval_s_for_scenario_key(r, "dreamscape_memory") == 0.1
    assert capture_interval_s_for_scenario_key(r, "dreamscape_memory_multiplayer") == 0.1


def test_empty_or_coreless_scenario_key_is_none() -> None:
    r = repo_root()
    assert capture_interval_s_for_scenario_key(r, "") is None
    assert capture_interval_s_for_scenario_key(r, "   ") is None


def test_floor_is_sane() -> None:
    # 20 fps hard cap guards against a typo'd sub-millisecond interval.
    assert 0.0 < MIN_CAPTURE_INTERVAL_S <= 0.1


def test_reuse_window_always_outlives_the_tick_that_uses_it() -> None:
    """The regression this function exists for.

    A phash skip compares ``now - last_computed`` against the window; if the
    window is no longer than the gap between ticks, the comparison is false on
    every tick and the skip can never fire — the worker silently pays a full
    detect + overlay sweep forever. Assert the invariant across the whole range
    of cadences the rolling loop can pick, so a future tier change can't
    reintroduce it.
    """
    base = 4.0
    for tick in (0.05, 0.3, 1.0, 2.5, 5.0):
        window = force_refresh_window_s(base, tick)
        assert window > tick, f"skip can never fire at a {tick}s tick"


def test_reuse_window_never_shrinks_below_its_base() -> None:
    # Fast cadences must keep exactly the staleness bound they had before the
    # window became tick-relative.
    assert force_refresh_window_s(4.0, 0.3) == 4.0
    assert force_refresh_window_s(4.0, 1.0) == 4.0


def test_reuse_window_is_capped() -> None:
    # A future slower tick must not stretch reuse without bound.
    assert force_refresh_window_s(4.0, 600.0) == FORCE_REFRESH_MAX_S
    assert FORCE_REFRESH_MAX_S > 0


def test_reuse_window_falls_back_to_base_without_a_known_tick() -> None:
    # Before the rolling loop has published a cadence (or in a non-rolling
    # caller) the original constant applies unchanged.
    assert force_refresh_window_s(4.0, None) == 4.0
    assert force_refresh_window_s(4.0, 0.0) == 4.0
